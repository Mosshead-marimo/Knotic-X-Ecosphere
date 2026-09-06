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

    # A second bootstrap call must not fail even though the demo tenant row already exists.
    second_bootstrap = client.post("/api/v1/auth/dev-session", headers={"Origin": ORIGIN})
    assert second_bootstrap.status_code == 200

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
