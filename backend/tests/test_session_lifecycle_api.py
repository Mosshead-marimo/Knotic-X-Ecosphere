from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import jsonschema
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
from knotic_api.lifecycle_api import LifecycleDependencies
from knotic_api.persistence.active_state import RedisSalesStateRepository
from knotic_api.security import (
    AuthenticatedActor,
    RedisBrowserSessionStore,
    RedisRateLimiter,
    ReplayCipher,
    derive_key,
)

ROOT = Path(__file__).parents[2]
ORIGIN = "https://app.test.example"
CSRF_TOKEN = "csrf-" + ("x" * 32)
SECURITY_KEY = b"test-session-security-key-32-bytes-minimum"


def _contract_schema(name: str) -> dict[str, Any]:
    contract = json.loads((ROOT / "docs" / "contracts" / "openapi.v1.json").read_text(encoding="utf-8"))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/components/schemas/{name}",
        "components": contract["components"],
    }


def _assert_contract(instance: object, schema_name: str) -> None:
    jsonschema.Draft202012Validator(_contract_schema(schema_name)).validate(instance)


@pytest.fixture(scope="module")
def lifecycle_services() -> tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings]:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis integration URLs are required for lifecycle API tests")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    redis_client = redis.Redis.from_url(redis_url, decode_responses=False)
    redis_client.flushdb()
    dependencies = LifecycleDependencies(
        engine=engine,
        browser_sessions=RedisBrowserSessionStore(
            redis_client,
            environment="api-test",
            master_key=SECURITY_KEY,
        ),
        rate_limiter=RedisRateLimiter(redis_client, environment="api-test", master_key=SECURITY_KEY),
        active_states=RedisSalesStateRepository(redis_client, environment="api-test"),
        replay_cipher=ReplayCipher(SECURITY_KEY),
        idempotency_hmac_key=derive_key(SECURITY_KEY, b"idempotency-key-hmac"),
        allowed_origins=frozenset({ORIGIN}),
    )
    settings = BackendSettings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        mcp_auth_token=SecretStr("a" * 40),
        agora_app_certificate=SecretStr("b" * 32),
        session_security_key=SecretStr(SECURITY_KEY.decode()),
        KNOTIC_MCP_BASE_URL="http://mcp.internal:8090",
        KNOTIC_AGORA_APP_ID="0123456789abcdef0123456789abcdef",
        KNOTIC_ALLOWED_ORIGINS=[ORIGIN],
    )
    yield engine, redis_client, dependencies, settings
    engine.dispose()
    command.downgrade(config, "base")


def _authorized_client(
    engine: Engine,
    dependencies: LifecycleDependencies,
    settings: BackendSettings,
    *,
    suffix: str,
) -> tuple[FlaskClient, AuthenticatedActor]:
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    with engine.begin() as connection:
        connection.execute(
            sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
            {"id": tenant_id, "slug": f"api-{suffix}-{tenant_id}"},
        )
        connection.execute(
            sa.text("insert into actors(id,tenant_id,actor_type,status) values (:id,:tenant_id,'USER','ACTIVE')"),
            {"id": actor_id, "tenant_id": tenant_id},
        )
    actor = AuthenticatedActor(tenant_id=tenant_id, actor_id=actor_id)
    cookie = f"browser-cookie-{suffix}-00000000000000000000000000000001"
    dependencies.browser_sessions.put(
        cookie=cookie,
        csrf_token=CSRF_TOKEN,
        actor=actor,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    client = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    client.set_cookie("knotic_session", cookie, domain="localhost")
    return client, actor


def _mutation_headers(key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "X-CSRF-Token": CSRF_TOKEN,
        "Idempotency-Key": key,
    }


@pytest.mark.integration
def test_authentication_origin_csrf_and_validation_fail_closed(
    lifecycle_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    engine, _, dependencies, settings = lifecycle_services
    anonymous = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    unauthorized = anonymous.get(f"/api/v1/sessions/{new_uuid7()}")
    assert unauthorized.status_code == 401
    _assert_contract(unauthorized.get_json(), "ErrorEnvelope")

    client, _ = _authorized_client(engine, dependencies, settings, suffix="security")
    denied_origin = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers={**_mutation_headers("security-key-0001"), "Origin": "https://attacker.example"},
    )
    assert denied_origin.status_code == 403
    assert denied_origin.get_json()["error"]["code"] == "ORIGIN_DENIED"

    bad_csrf = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers={**_mutation_headers("security-key-0002"), "X-CSRF-Token": "x" * 32},
    )
    assert bad_csrf.status_code == 403
    assert bad_csrf.get_json()["error"]["code"] == "CSRF_FAILED"

    invalid = client.post(
        "/api/v1/sessions",
        json={"locale": "not a locale", "timezone": "not/a-zone", "extra": True},
        headers=_mutation_headers("security-key-0003"),
    )
    assert invalid.status_code == 422
    _assert_contract(invalid.get_json(), "ErrorEnvelope")


@pytest.mark.integration
def test_create_read_end_idempotency_contract_and_tenant_isolation(
    lifecycle_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    engine, _, dependencies, settings = lifecycle_services
    client, actor = _authorized_client(engine, dependencies, settings, suffix="owner")
    create_headers = _mutation_headers("create-session-key-0000001")
    created = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers=create_headers,
    )
    assert created.status_code == 201
    created_body = created.get_json()
    _assert_contract(created_body, "SessionResource")
    assert created.headers["Location"] == f"/api/v1/sessions/{created_body['session_id']}"
    assert created.headers["ETag"] == '"1"'
    assert UUID(created.headers["X-Request-ID"])
    assert UUID(created.headers["X-Correlation-ID"])

    replay = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers=create_headers,
    )
    assert replay.status_code == 201
    assert replay.get_json() == created_body
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("select count(*) from sales_sessions where tenant_id=:tenant_id"),
                {"tenant_id": actor.tenant_id},
            )
            == 1
        )

    mismatch = client.post(
        "/api/v1/sessions",
        json={"locale": "fr-FR", "timezone": "UTC"},
        headers=create_headers,
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    read = client.get(f"/api/v1/sessions/{created_body['session_id']}")
    assert read.status_code == 200
    _assert_contract(read.get_json(), "SessionResource")

    other_client, _ = _authorized_client(engine, dependencies, settings, suffix="other")
    hidden = other_client.get(f"/api/v1/sessions/{created_body['session_id']}")
    assert hidden.status_code == 404

    end_headers = _mutation_headers("end-session-key-000000001")
    ended = client.post(
        f"/api/v1/sessions/{created_body['session_id']}/end",
        json={"reason": "COMPLETED", "expected_session_version": 1},
        headers=end_headers,
    )
    assert ended.status_code == 200
    ended_body = ended.get_json()
    _assert_contract(ended_body, "SessionResource")
    assert ended_body["status"] == "ENDED"
    assert ended_body["version"] == 3
    assert ended_body["ended_at"] is not None
    assert ended_body["state"]["outcome"] == "CLOSED_NO_ACTION"
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("select count(*) from domain_events where tenant_id=:tenant_id"),
                {"tenant_id": actor.tenant_id},
            )
            == 2
        )
        assert (
            connection.scalar(
                sa.text("select count(*) from session_outcomes where tenant_id=:tenant_id"),
                {"tenant_id": actor.tenant_id},
            )
            == 1
        )
        replay_ciphertext = connection.scalar(
            sa.text(
                "select response_body_ciphertext from idempotency_records "
                "where tenant_id=:tenant_id and status='COMPLETED' limit 1"
            ),
            {"tenant_id": actor.tenant_id},
        )
        assert isinstance(replay_ciphertext, bytes)
        assert created_body["session_id"].encode() not in replay_ciphertext

    end_replay = client.post(
        f"/api/v1/sessions/{created_body['session_id']}/end",
        json={"reason": "COMPLETED", "expected_session_version": 1},
        headers=end_headers,
    )
    assert end_replay.status_code == 200
    assert end_replay.get_json() == ended_body

    stale = client.post(
        f"/api/v1/sessions/{created_body['session_id']}/end",
        json={"reason": "COMPLETED", "expected_session_version": 1},
        headers=_mutation_headers("end-session-stale-0000001"),
    )
    assert stale.status_code == 409
    assert stale.get_json()["error"]["code"] == "VERSION_CONFLICT"


@pytest.mark.integration
def test_rate_limit_returns_contract_headers(
    lifecycle_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    engine, _, dependencies, settings = lifecycle_services
    client, actor = _authorized_client(engine, dependencies, settings, suffix="rate")
    for _ in range(30):
        assert dependencies.rate_limiter.check(actor, action="create-session", limit=30).allowed
    limited = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers=_mutation_headers("rate-limited-key-0000001"),
    )
    assert limited.status_code == 429
    _assert_contract(limited.get_json(), "ErrorEnvelope")
    assert limited.headers["RateLimit-Limit"] == "30"
    assert limited.headers["RateLimit-Remaining"] == "0"
    assert int(limited.headers["RateLimit-Reset"]) > int(datetime.now(UTC).timestamp())
    assert int(limited.headers["Retry-After"]) >= 1
