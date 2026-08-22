from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.domain.models import Requirement
from knotic_api.domain.types import RequirementField, RequirementUpdateSource, SessionStatus
from knotic_api.persistence.repositories import (
    FollowupRepository,
    IdempotencyReservation,
    LeadCreate,
    MeetingCreate,
    MeetingRepository,
    RequirementRevisionCommand,
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


def _revision_command(
    requirement: Requirement,
    *,
    actor_id: object,
    event_id: object,
    correlation_id: object,
) -> RequirementRevisionCommand:
    return RequirementRevisionCommand(
        requirement=requirement,
        event_id=event_id,
        correlation_id=correlation_id,
        actor_type="CUSTOMER",
        actor_id=actor_id,
        source=RequirementUpdateSource.CUSTOMER_CONFIRMATION,
    )


@pytest.mark.integration
def test_requirement_revision_preserves_fr05_history_event_and_replay(repository_engine: Engine) -> None:
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    session_id = new_uuid7()
    requirement_id = new_uuid7()
    turn_50 = new_uuid7()
    turn_250 = new_uuid7()
    event_50 = new_uuid7()
    event_250 = new_uuid7()
    correlation_id = new_uuid7()
    _create_tenant(repository_engine, tenant_id, f"tenant-{tenant_id}")
    with UnitOfWork(repository_engine, tenant_id=tenant_id) as work:
        work.sessions.create(SessionCreate(session_id=session_id, locale="en-US", timezone="UTC"))

    now = datetime.now(UTC)
    users_50 = Requirement(
        requirement_id=requirement_id,
        tenant_id=tenant_id,
        session_id=session_id,
        field=RequirementField.USERS,
        value=50,
        confirmed=True,
        confidence=1,
        source_turn_id=turn_50,
        updated_at=now,
        version=1,
    )
    users_250 = users_50.model_copy(
        update={"value": 250, "source_turn_id": turn_250, "updated_at": now + timedelta(seconds=1), "version": 2}
    )
    with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        first = work.requirements.revise_confirmed(
            _revision_command(users_50, actor_id=actor_id, event_id=event_50, correlation_id=correlation_id)
        )
    with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        corrected = work.requirements.revise_confirmed(
            _revision_command(users_250, actor_id=actor_id, event_id=event_250, correlation_id=correlation_id)
        )
    with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        replay = work.requirements.revise_confirmed(
            _revision_command(users_250, actor_id=actor_id, event_id=new_uuid7(), correlation_id=correlation_id)
        )

    assert first.previous_value is None and first.current_value == 50
    assert corrected.previous_value == 50 and corrected.current_value == 250
    assert replay.event_id == event_250 and not replay.changed
    with repository_engine.connect() as connection:
        current = (
            connection.execute(
                sa.text(
                    "select value_integer,version from requirements_current "
                    "where tenant_id=:tenant_id and session_id=:session_id and field='users'"
                ),
                {"tenant_id": tenant_id, "session_id": session_id},
            )
            .mappings()
            .one()
        )
        changes = (
            connection.execute(
                sa.text(
                    "select old_value,new_value,actor_type,actor_id,source,event_id from requirement_changes "
                    "where tenant_id=:tenant_id and session_id=:session_id order by changed_at,id"
                ),
                {"tenant_id": tenant_id, "session_id": session_id},
            )
            .mappings()
            .all()
        )
        events = (
            connection.execute(
                sa.text(
                    "select sequence,event_type,actor_type,actor_id,payload from domain_events "
                    "where tenant_id=:tenant_id and session_id=:session_id order by sequence"
                ),
                {"tenant_id": tenant_id, "session_id": session_id},
            )
            .mappings()
            .all()
        )

    assert current == {"value_integer": 250, "version": 2}
    assert [(row["old_value"], row["new_value"]) for row in changes] == [(None, 50), (50, 250)]
    assert all(row["actor_type"] == "CUSTOMER" and row["actor_id"] == actor_id for row in changes)
    assert all(row["source"] == "CUSTOMER_CONFIRMATION" for row in changes)
    assert [row["event_id"] for row in changes] == [event_50, event_250]
    assert [row["sequence"] for row in events] == [1, 2]
    assert all(row["event_type"] == "requirement.updated" for row in events)
    assert events[1]["payload"]["old_value"] == 50 and events[1]["payload"]["new_value"] == 250

    conflict = users_250.model_copy(update={"value": 300, "version": 3})
    with pytest.raises(ValueError, match="source turn cannot confirm conflicting"):
        with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            work.requirements.revise_confirmed(
                _revision_command(conflict, actor_id=actor_id, event_id=new_uuid7(), correlation_id=correlation_id)
            )


@pytest.mark.integration
def test_concurrent_requirement_corrections_allow_exactly_one_winner(repository_engine: Engine) -> None:
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    session_id = new_uuid7()
    requirement_id = new_uuid7()
    correlation_id = new_uuid7()
    _create_tenant(repository_engine, tenant_id, f"tenant-{tenant_id}")
    with UnitOfWork(repository_engine, tenant_id=tenant_id) as work:
        work.sessions.create(SessionCreate(session_id=session_id, locale="en-US", timezone="UTC"))
    now = datetime.now(UTC)
    original = Requirement(
        requirement_id=requirement_id,
        tenant_id=tenant_id,
        session_id=session_id,
        field=RequirementField.USERS,
        value=50,
        confirmed=True,
        confidence=1,
        source_turn_id=new_uuid7(),
        updated_at=now,
        version=1,
    )
    with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        work.requirements.revise_confirmed(
            _revision_command(original, actor_id=actor_id, event_id=new_uuid7(), correlation_id=correlation_id)
        )
    proposals = [
        original.model_copy(
            update={
                "value": value,
                "source_turn_id": new_uuid7(),
                "updated_at": now + timedelta(seconds=1),
                "version": 2,
            }
        )
        for value in (250, 300)
    ]

    def revise(requirement: Requirement) -> str:
        try:
            with UnitOfWork(repository_engine, tenant_id=tenant_id, actor_id=actor_id) as work:
                work.requirements.revise_confirmed(
                    _revision_command(
                        requirement,
                        actor_id=actor_id,
                        event_id=new_uuid7(),
                        correlation_id=correlation_id,
                    )
                )
            return "saved"
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(revise, proposals))
    assert sorted(outcomes) == ["conflict", "saved"]
    with repository_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text(
                    "select count(*) from requirement_changes where tenant_id=:tenant_id and session_id=:session_id"
                ),
                {"tenant_id": tenant_id, "session_id": session_id},
            )
            == 2
        )
        assert (
            connection.scalar(
                sa.text("select count(*) from domain_events where tenant_id=:tenant_id and session_id=:session_id"),
                {"tenant_id": tenant_id, "session_id": session_id},
            )
            == 2
        )
