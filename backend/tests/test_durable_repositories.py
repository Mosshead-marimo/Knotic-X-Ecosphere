from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.domain.types import SessionStatus
from knotic_api.persistence.repositories import (
    FollowupRepository,
    IdempotencyReservation,
    LeadCreate,
    MeetingCreate,
    MeetingRepository,
    SessionCreate,
)
from knotic_api.persistence.unit_of_work import UnitOfWork

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def repository_engine() -> Engine:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("KNOTIC_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()
    command.downgrade(config, "base")


def _create_tenant(engine: Engine, tenant_id: object, slug: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
            {"id": tenant_id, "slug": slug},
        )


@pytest.mark.integration
def test_partial_writes_roll_back_and_repositories_always_scope_tenant(repository_engine: Engine) -> None:
    tenant_a = new_uuid7()
    tenant_b = new_uuid7()
    _create_tenant(repository_engine, tenant_a, f"tenant-{tenant_a}")
    _create_tenant(repository_engine, tenant_b, f"tenant-{tenant_b}")
    lead_id = new_uuid7()
    session_id = new_uuid7()

    with pytest.raises(RuntimeError, match="injected"):
        with UnitOfWork(repository_engine, tenant_id=tenant_a) as work:
            work.leads.create(LeadCreate(lead_id=lead_id))
            work.sessions.create(SessionCreate(session_id=session_id, locale="en-US", timezone="UTC", lead_id=lead_id))
            raise RuntimeError("injected persistence failure")

    with repository_engine.connect() as connection:
        assert connection.scalar(sa.text("select count(*) from leads where id=:id"), {"id": lead_id}) == 0
        assert connection.scalar(sa.text("select count(*) from sales_sessions where id=:id"), {"id": session_id}) == 0

    with UnitOfWork(repository_engine, tenant_id=tenant_a) as work:
        work.leads.create(LeadCreate(lead_id=lead_id))
        created = work.sessions.create(
            SessionCreate(session_id=session_id, locale="en-US", timezone="UTC", lead_id=lead_id)
        )
        assert created.version == 1
    with UnitOfWork(repository_engine, tenant_id=tenant_b) as work:
        assert work.sessions.get(session_id) is None


@pytest.mark.integration
def test_optimistic_session_transition_rejects_stale_version(repository_engine: Engine) -> None:
    tenant_id = new_uuid7()
    _create_tenant(repository_engine, tenant_id, f"tenant-{tenant_id}")
    session_id = new_uuid7()
    now = datetime.now(UTC)
    with UnitOfWork(repository_engine, tenant_id=tenant_id) as work:
        work.sessions.create(SessionCreate(session_id=session_id, locale="en-US", timezone="UTC"))
        active = work.sessions.transition(
            session_id,
            expected_version=1,
            expected_status=SessionStatus.CREATED,
            target_status=SessionStatus.ACTIVE,
            at=now,
        )
        assert active is not None and active.version == 2
        assert (
            work.sessions.transition(
                session_id,
                expected_version=1,
                expected_status=SessionStatus.CREATED,
                target_status=SessionStatus.ACTIVE,
                at=now,
            )
            is None
        )


@pytest.mark.integration
def test_idempotency_reservation_prevents_duplicate_business_record(repository_engine: Engine) -> None:
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    session_id = new_uuid7()
    _create_tenant(repository_engine, tenant_id, f"tenant-{tenant_id}")
    with UnitOfWork(repository_engine, tenant_id=tenant_id) as work:
        work.sessions.create(SessionCreate(session_id=session_id, locale="en-US", timezone="UTC"))

    reservation = IdempotencyReservation(
        record_id=new_uuid7(),
        actor_id=actor_id,
        workload="session-api",
        method="POST",
        path=f"/api/v1/sales-sessions/{session_id}/meetings",
        key_hmac=b"k" * 32,
        request_hash=b"r" * 32,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    meeting_id = new_uuid7()
    meeting = MeetingCreate(
        meeting_id=meeting_id,
        session_id=session_id,
        lead_id=None,
        provider="calendar",
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        timezone="UTC",
        idempotency_key_hmac=reservation.key_hmac,
    )
    with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        assert isinstance(work.meetings, MeetingRepository)
        assert isinstance(work.followups, FollowupRepository)
        assert work.idempotency.reserve(reservation)
        work.meetings.create(meeting)

    replay = IdempotencyReservation(
        record_id=new_uuid7(),
        actor_id=actor_id,
        workload=reservation.workload,
        method=reservation.method,
        path=reservation.path,
        key_hmac=reservation.key_hmac,
        request_hash=b"different-request-hash-value!!",
        expires_at=reservation.expires_at,
    )
    with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        assert not work.idempotency.reserve(replay)
        existing = work.idempotency.get(replay)
        assert existing is not None
        assert existing["request_hash"] == reservation.request_hash

    with repository_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("select count(*) from meetings where tenant_id=:tenant_id and id=:id"),
                {"tenant_id": tenant_id, "id": meeting_id},
            )
            == 1
        )
