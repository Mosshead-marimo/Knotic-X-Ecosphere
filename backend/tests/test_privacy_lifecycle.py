from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import redis
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from knotic_api.domain import DomainEvent, EventType, SalesState, SessionStatus, new_uuid7
from knotic_api.persistence.active_state import CacheReadStatus, RedisSalesStateRepository
from knotic_api.persistence.hydration import SalesStateHydrator, StateFieldCipher
from knotic_api.persistence.repositories import SessionCreate
from knotic_api.persistence.unit_of_work import UnitOfWork
from knotic_api.privacy import DataLifecycleService, RetentionPolicy

ROOT = Path(__file__).parents[2]
MASTER_KEY = b"privacy-lifecycle-test-key-material-000000000000001"


@pytest.fixture(scope="module")
def privacy_services() -> tuple[Engine, redis.Redis, RedisSalesStateRepository, DataLifecycleService]:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis integration URLs are required for privacy tests")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    client = redis.Redis.from_url(redis_url, decode_responses=False)
    client.flushdb()
    active = RedisSalesStateRepository(client, environment="test", ttl_seconds=60)
    cipher = StateFieldCipher(MASTER_KEY)
    service = DataLifecycleService(
        engine,
        active,
        SalesStateHydrator(engine, active, field_cipher=cipher),
        policy=RetentionPolicy(policy_version="test-v1"),
    )
    yield engine, client, active, service
    client.flushdb()
    engine.dispose()
    command.downgrade(config, "base")


def _tenant_actor(engine: Engine) -> tuple:
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    with engine.begin() as connection:
        connection.execute(
            sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
            {"id": tenant_id, "slug": f"privacy-{tenant_id}"},
        )
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)})
        connection.execute(
            sa.text("insert into actors(id,tenant_id,actor_type,status) values (:id,:tenant_id,'SERVICE','ACTIVE')"),
            {"id": actor_id, "tenant_id": tenant_id},
        )
    return tenant_id, actor_id


def _session(engine: Engine, tenant_id: object, actor_id: object) -> tuple:
    session_id = new_uuid7()
    with UnitOfWork(engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        session = work.sessions.create(SessionCreate(session_id=session_id, locale="en-US", timezone="UTC"))
        work.events.append(
            DomainEvent(
                event_id=new_uuid7(),
                event_type=EventType.SESSION_CREATED,
                occurred_at=session.created_at,
                tenant_id=tenant_id,
                session_id=session_id,
                sequence=1,
                correlation_id=new_uuid7(),
                actor_type="WORKLOAD",
                actor_id=actor_id,
                payload={"status": "CREATED"},
            )
        )
    return session_id, session


@pytest.mark.integration
def test_export_erasure_and_processing_block_are_audited_and_tenant_scoped(
    privacy_services: tuple[Engine, redis.Redis, RedisSalesStateRepository, DataLifecycleService],
) -> None:
    engine, client, active, service = privacy_services
    tenant_id, actor_id = _tenant_actor(engine)
    session_id, session = _session(engine, tenant_id, actor_id)
    active.create(
        SalesState(
            tenant_id=tenant_id,
            session_id=session_id,
            status=SessionStatus.CREATED,
            version=1,
            created_at=session.created_at,
            updated_at=session.updated_at,
        ),
        event_watermark=1,
        fencing_token=0,
    )
    correlation_id = new_uuid7()

    exported = service.export_session(
        tenant_id=tenant_id,
        actor_id=actor_id,
        session_id=session_id,
        correlation_id=correlation_id,
    )
    assert exported.session_id == session_id and exported.state.tenant_id == tenant_id
    operation_id = service.request_session_erasure(
        tenant_id=tenant_id,
        actor_id=actor_id,
        session_id=session_id,
        correlation_id=correlation_id,
    )
    assert not service.processing_allowed(tenant_id=tenant_id, session_id=session_id)
    assert client.exists(active.key(tenant_id, session_id)) == 0
    assert active.load(tenant_id, session_id).status == CacheReadStatus.BLOCKED
    assert service.execute_session_erasure(
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation_id=operation_id,
        correlation_id=correlation_id,
        provider_unlinked=False,
    )
    with engine.connect() as connection:
        assert connection.scalar(sa.text("select count(*) from sales_sessions where id=:id"), {"id": session_id}) == 0
        operation = (
            connection.execute(
                sa.text("select status,session_id,result_reference from operations where id=:id"),
                {"id": operation_id},
            )
            .mappings()
            .one()
        )
        actions = connection.scalars(
            sa.text("select action from audit_events where tenant_id=:tenant order by occurred_at,id"),
            {"tenant": tenant_id},
        ).all()
    assert operation == {"status": "COMPLETED", "session_id": None, "result_reference": "erased"}
    assert actions == ["SESSION_EXPORTED", "SESSION_ERASURE_REQUESTED", "SESSION_ERASED"]


@pytest.mark.integration
def test_retention_minimizes_then_purges_and_respects_legal_hold(
    privacy_services: tuple[Engine, redis.Redis, RedisSalesStateRepository, DataLifecycleService],
) -> None:
    engine, _, _, service = privacy_services
    tenant_id, actor_id = _tenant_actor(engine)
    current = datetime.now(UTC)
    minimize_id, _ = _session(engine, tenant_id, actor_id)
    purge_id, _ = _session(engine, tenant_id, actor_id)
    held_id, _ = _session(engine, tenant_id, actor_id)
    with engine.begin() as connection:
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)})
        for session_id, ended_at in (
            (minimize_id, current - timedelta(days=370)),
            (purge_id, current - timedelta(days=410)),
            (held_id, current - timedelta(days=410)),
        ):
            connection.execute(
                sa.text("update sales_sessions set status='ENDED',ended_at=:ended,updated_at=:ended where id=:id"),
                {"ended": ended_at, "id": session_id},
            )
            connection.execute(
                sa.text(
                    "insert into messages(id,tenant_id,session_id,turn_id,sequence,speaker,source,"
                    "content_ciphertext,locale) values (:id,:tenant,:session,:turn,1,'CUSTOMER','TEXT',:body,'en-US')"
                ),
                {
                    "id": new_uuid7(),
                    "tenant": tenant_id,
                    "session": session_id,
                    "turn": new_uuid7(),
                    "body": b"encrypted-placeholder",
                },
            )
        connection.execute(
            sa.text(
                "insert into operations(id,tenant_id,actor_id,session_id,kind,status) "
                "values (:id,:tenant,:actor,:session,'LEGAL_HOLD','ACTIVE')"
            ),
            {"id": new_uuid7(), "tenant": tenant_id, "actor": actor_id, "session": held_id},
        )

    report = service.run_retention(
        tenant_id=tenant_id,
        actor_id=actor_id,
        correlation_id=new_uuid7(),
        now=current,
    )
    assert report.minimized_sessions == 1
    assert report.purged_sessions == 1
    with engine.begin() as connection:
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)})
        assert (
            connection.scalar(sa.text("select count(*) from messages where session_id=:id"), {"id": minimize_id}) == 0
        )
        assert connection.scalar(sa.text("select count(*) from sales_sessions where id=:id"), {"id": purge_id}) == 0
        assert connection.scalar(sa.text("select count(*) from sales_sessions where id=:id"), {"id": held_id}) == 1


@pytest.mark.integration
def test_database_roles_are_least_privilege_and_rls_remains_fail_closed(
    privacy_services: tuple[Engine, redis.Redis, RedisSalesStateRepository, DataLifecycleService],
) -> None:
    engine, _, _, _ = privacy_services
    tenant_a, actor_a = _tenant_actor(engine)
    tenant_b, actor_b = _tenant_actor(engine)
    session_b, _ = _session(engine, tenant_b, actor_b)

    with engine.begin() as connection:
        connection.execute(sa.text("set local role knotic_runtime"))
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_a)})
        assert connection.scalar(sa.text("select count(*) from sales_sessions where id=:id"), {"id": session_b}) == 0

    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(sa.text("set local role knotic_auditor"))
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_a)})
        connection.execute(
            sa.text(
                "insert into audit_events(id,tenant_id,actor_id,workload,action,target_type,result,correlation_id) "
                "values (:id,:tenant,:actor,'test','WRITE','SESSION','SUCCEEDED',:correlation)"
            ),
            {
                "id": new_uuid7(),
                "tenant": tenant_a,
                "actor": actor_a,
                "correlation": new_uuid7(),
            },
        )
