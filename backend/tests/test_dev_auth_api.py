from __future__ import annotations

import os
from pathlib import Path

import pytest
import redis
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy.engine import Engine

from knotic_api.app import create_app
from knotic_api.config import BackendSettings
from knotic_api.event_stream import DomainEventRelay, EventStreamDependencies
from knotic_api.lifecycle_api import LifecycleDependencies, build_lifecycle_dependencies

ROOT = Path(__file__).parents[2]
ORIGIN = "https://app.test.example"
SECURITY_KEY = "test-session-security-key-32-bytes-minimum"


@pytest.fixture(scope="module")
def dev_auth_services() -> tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings]:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis integration URLs are required for dev-auth API tests")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    dependencies = build_lifecycle_dependencies(
        database_url=database_url,
        redis_url=redis_url,
        environment="dev-auth-test",
        security_key=SECURITY_KEY.encode(),
        allowed_origins=[ORIGIN],
    )
    redis_client = redis.Redis.from_url(redis_url, decode_responses=False)
    redis_client.flushdb()
    settings = BackendSettings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(redis_url),
        mcp_auth_token=SecretStr("a" * 40),
        agora_app_certificate=SecretStr("b" * 32),
        session_security_key=SecretStr(SECURITY_KEY),
        KNOTIC_MCP_BASE_URL="http://mcp.internal:8090",
        KNOTIC_AGORA_APP_ID="0123456789abcdef0123456789abcdef",
        KNOTIC_ALLOWED_ORIGINS=[ORIGIN],
    )
    yield dependencies.engine, redis_client, dependencies, settings
    dependencies.engine.dispose()
    command.downgrade(config, "base")


@pytest.mark.integration
def test_dev_session_rejects_disallowed_origin(
    dev_auth_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    _, _, dependencies, settings = dev_auth_services
    client = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    response = client.post("/api/v1/auth/dev-session", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "ORIGIN_DENIED"


@pytest.mark.integration
def test_dev_session_issues_a_cookie_that_can_create_a_real_sales_session(
    dev_auth_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    engine, _, dependencies, settings = dev_auth_services
    client = create_app(settings, lifecycle_dependencies=dependencies).test_client()

    bootstrap = client.post("/api/v1/auth/dev-session", headers={"Origin": ORIGIN})
    assert bootstrap.status_code == 200
    csrf_token = bootstrap.get_json()["csrf_token"]
    assert "knotic_session" in bootstrap.headers.get("Set-Cookie", "")

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.get_json()["csrf_token"] == csrf_token
    assert session.get_json()["actor"]["roles"] == ["ADMIN"]

    # A second bootstrap call must not fail even though the demo tenant row already exists.
    second_bootstrap = client.post("/api/v1/auth/dev-session", headers={"Origin": ORIGIN})
    assert second_bootstrap.status_code == 200
    csrf_token = second_bootstrap.get_json()["csrf_token"]

    created = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers={
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "X-CSRF-Token": csrf_token,
            "Idempotency-Key": "dev-auth-smoke-test-0000000000001",
        },
    )
    assert created.status_code == 201, created.get_json()

    with engine.begin() as connection:
        tenant_count = connection.execute(
            sa.text("select count(*) from tenants where slug = 'local-demo'")
        ).scalar_one()
    assert tenant_count == 1


@pytest.mark.integration
def test_dev_session_logout_requires_csrf_and_revokes_cookie(
    dev_auth_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    _, _, dependencies, settings = dev_auth_services
    client = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    bootstrap = client.post("/api/v1/auth/dev-session", headers={"Origin": ORIGIN})
    csrf_token = bootstrap.get_json()["csrf_token"]
    assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
    signed_out = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token})
    assert signed_out.status_code == 200
    assert client.get("/api/v1/auth/session").status_code == 401


@pytest.mark.integration
def test_operator_console_lists_sessions_and_requests_idempotent_handoff(
    dev_auth_services: tuple[Engine, redis.Redis, LifecycleDependencies, BackendSettings],
) -> None:
    engine, redis_client, dependencies, settings = dev_auth_services
    client = create_app(settings, lifecycle_dependencies=dependencies).test_client()
    bootstrap = client.post("/api/v1/auth/dev-session", headers={"Origin": ORIGIN})
    csrf_token = bootstrap.get_json()["csrf_token"]
    created = client.post(
        "/api/v1/sessions",
        json={"locale": "en-US", "timezone": "UTC"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token, "Idempotency-Key": "console-session-create-00000001"},
    )
    session = created.get_json()
    listing = client.get("/api/v1/console/sessions?limit=10")
    assert listing.status_code == 200
    assert any(item["session_id"] == session["session_id"] for item in listing.get_json()["items"])
    details = client.get(f"/api/v1/console/sessions/{session['session_id']}")
    assert details.status_code == 200
    assert details.get_json()["transcript"] == []
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf_token, "Idempotency-Key": "console-handoff-00000000001"}
    first = client.post(
        f"/api/v1/console/sessions/{session['session_id']}/handoff",
        json={
            "reason": "Customer requested a person",
            "priority": "HIGH",
            "expected_session_version": session["version"],
        },
        headers=headers,
    )
    assert first.status_code == 202
    assert first.get_json()["status"] == "requested"
    replay = client.post(
        f"/api/v1/console/sessions/{session['session_id']}/handoff",
        json={
            "reason": "Customer requested a person",
            "priority": "HIGH",
            "expected_session_version": session["version"],
        },
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.get_json()["id"] == first.get_json()["id"]

    relay = DomainEventRelay(
        EventStreamDependencies(engine, redis_client, dependencies.browser_sessions, settings.environment.value)
    )
    assert relay.relay_once() >= 1
    stream = client.get(
        "/api/v1/console/stream",
        headers={"Last-Event-ID": "0-0"},
        buffered=False,
    )
    assert stream.status_code == 200
    chunks = iter(stream.response)
    assert b"event: ready" in next(chunks)
    assert b"event: session.changed" in next(chunks)
    stream.close()
