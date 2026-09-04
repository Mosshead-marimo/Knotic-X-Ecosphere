from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import redis
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from flask.testing import FlaskClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from knotic_api.app import create_app
from knotic_api.config import BackendSettings
from knotic_api.domain.identifiers import new_uuid7
from knotic_api.domain.models import SalesState
from knotic_api.domain.types import SessionStatus
from knotic_api.lifecycle_api import LifecycleDependencies
from knotic_api.persistence.active_state import RedisSalesStateRepository
from knotic_api.security import (
    AuthenticatedActor,
    RedisBrowserSessionStore,
    RedisRateLimiter,
    ReplayCipher,
    derive_key,
)
from knotic_api.voice.agora_token import decode_rtc_token
from knotic_api.voice.session_service import channel_name_for

ROOT = Path(__file__).parents[2]
ORIGIN = "https://app.test.example"
CSRF_TOKEN = "csrf-" + ("x" * 32)
SECURITY_KEY = b"test-session-security-key-32-bytes-minimum"
AGORA_APP_ID = "0123456789abcdef0123456789abcdef"
AGORA_APP_CERTIFICATE = "b" * 32


@pytest.fixture(scope="module")
def voice_services() -> tuple[Engine, LifecycleDependencies, BackendSettings]:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis integration URLs are required for voice API tests")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    redis_client = redis.Redis.from_url(redis_url, decode_responses=False)
    redis_client.flushdb()
    dependencies = LifecycleDependencies(
        engine=engine,
        browser_sessions=RedisBrowserSessionStore(redis_client, environment="voice-test", master_key=SECURITY_KEY),
        rate_limiter=RedisRateLimiter(redis_client, environment="voice-test", master_key=SECURITY_KEY),
        active_states=RedisSalesStateRepository(redis_client, environment="voice-test"),
        replay_cipher=ReplayCipher(SECURITY_KEY),
        idempotency_hmac_key=derive_key(SECURITY_KEY, b"idempotency-key-hmac"),
        allowed_origins=frozenset({ORIGIN}),
    )
    settings = BackendSettings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        mcp_auth_token=SecretStr("a" * 40),
        agora_app_certificate=SecretStr(AGORA_APP_CERTIFICATE),
        session_security_key=SecretStr(SECURITY_KEY.decode()),
        KNOTIC_MCP_BASE_URL="http://mcp.internal:8090",
        KNOTIC_AGORA_APP_ID=AGORA_APP_ID,
        KNOTIC_ALLOWED_ORIGINS=[ORIGIN],
    )
    yield engine, dependencies, settings
    engine.dispose()
    command.downgrade(config, "base")


def _authorized_client(
    engine: Engine, dependencies: LifecycleDependencies, settings: BackendSettings, *, suffix: str, ended: bool = False
) -> tuple[FlaskClient, AuthenticatedActor, str]:
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    session_id = new_uuid7()
    with engine.begin() as connection:
        connection.execute(
            sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
            {"id": tenant_id, "slug": f"voice-{suffix}-{tenant_id}"},
        )
        connection.execute(
            sa.text("insert into actors(id,tenant_id,actor_type,status) values (:id,:tenant_id,'USER','ACTIVE')"),
            {"id": actor_id, "tenant_id": tenant_id},
        )
    actor = AuthenticatedActor(tenant_id=tenant_id, actor_id=actor_id)
    now = datetime.now(UTC)
    state = SalesState(
        session_id=session_id,
        tenant_id=tenant_id,
        status=SessionStatus.ENDED if ended else SessionStatus.ACTIVE,
        version=1,
        created_at=now,
        updated_at=now,
    )
    dependencies.active_states.create(state, event_watermark=1, fencing_token=0)
    cookie = f"browser-cookie-{suffix}-00000000000000000000000000000001"
    dependencies.browser_sessions.put(
        cookie=cookie, csrf_token=CSRF_TOKEN, actor=actor, expires_at=now + timedelta(hours=1)
    )
    client = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    client.set_cookie("knotic_session", cookie, domain="localhost")
    return client, actor, str(session_id)


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/json", "Origin": ORIGIN, "X-CSRF-Token": CSRF_TOKEN}


@pytest.mark.integration
def test_issue_token_returns_a_valid_channel_scoped_publisher_token(
    voice_services: tuple[Engine, LifecycleDependencies, BackendSettings],
) -> None:
    engine, dependencies, settings = voice_services
    client, actor, session_id = _authorized_client(engine, dependencies, settings, suffix="issue")

    issued = client.post(f"/api/v1/sessions/{session_id}/voice/token", headers=_headers())

    assert issued.status_code == 200
    body = issued.get_json()
    assert body["app_id"] == AGORA_APP_ID
    assert body["channel_name"] == channel_name_for(tenant_id=actor.tenant_id, session_id=UUID(session_id))
    assert body["role"] == "PUBLISHER"
    assert AGORA_APP_CERTIFICATE not in issued.get_data(as_text=True)

    decoded = decode_rtc_token(body["token"], app_certificate=AGORA_APP_CERTIFICATE)
    assert decoded.signature_valid
    assert decoded.channel_name == body["channel_name"]
    assert decoded.uid == str(body["uid"])
    assert set(decoded.privileges) == {1, 2, 3, 4}


@pytest.mark.integration
def test_issue_token_rejects_unauthenticated_and_cross_tenant_access(
    voice_services: tuple[Engine, LifecycleDependencies, BackendSettings],
) -> None:
    engine, dependencies, settings = voice_services
    anonymous = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    unauthenticated = anonymous.post(f"/api/v1/sessions/{new_uuid7()}/voice/token", headers=_headers())
    assert unauthenticated.status_code == 401

    _owner_client, _owner, session_id = _authorized_client(engine, dependencies, settings, suffix="owner")
    other_client, _other, _ = _authorized_client(engine, dependencies, settings, suffix="intruder")
    cross_tenant = other_client.post(f"/api/v1/sessions/{session_id}/voice/token", headers=_headers())
    assert cross_tenant.status_code == 404


@pytest.mark.integration
def test_issue_token_rejects_ended_session_for_its_own_tenant(
    voice_services: tuple[Engine, LifecycleDependencies, BackendSettings],
) -> None:
    engine, dependencies, settings = voice_services
    client, _actor, session_id = _authorized_client(engine, dependencies, settings, suffix="ended-own", ended=True)

    response = client.post(f"/api/v1/sessions/{session_id}/voice/token", headers=_headers())

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "SESSION_NOT_ACCEPTING_VOICE"


@pytest.mark.integration
def test_renew_requires_a_prior_issuance_and_revoke_blocks_further_issuance(
    voice_services: tuple[Engine, LifecycleDependencies, BackendSettings],
) -> None:
    engine, dependencies, settings = voice_services
    client, _actor, session_id = _authorized_client(engine, dependencies, settings, suffix="renew")

    denied_renew = client.post(f"/api/v1/sessions/{session_id}/voice/token/renew", headers=_headers())
    assert denied_renew.status_code == 409
    assert denied_renew.get_json()["error"]["code"] == "VOICE_SESSION_DENIED"

    issued = client.post(f"/api/v1/sessions/{session_id}/voice/token", headers=_headers())
    assert issued.status_code == 200

    renewed = client.post(f"/api/v1/sessions/{session_id}/voice/token/renew", headers=_headers())
    assert renewed.status_code == 200
    assert renewed.get_json()["channel_name"] == issued.get_json()["channel_name"]

    revoked = client.delete(f"/api/v1/sessions/{session_id}/voice/token", headers=_headers())
    assert revoked.status_code == 200

    denied_after_revoke = client.post(f"/api/v1/sessions/{session_id}/voice/token", headers=_headers())
    assert denied_after_revoke.status_code == 409
    assert denied_after_revoke.get_json()["error"]["code"] == "VOICE_SESSION_DENIED"
