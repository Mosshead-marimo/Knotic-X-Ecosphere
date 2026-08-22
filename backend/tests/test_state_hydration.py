from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
import redis
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from knotic_api.domain import (
    DomainEvent,
    EventType,
    Objection,
    ObjectionCategory,
    ObjectionStatus,
    Requirement,
    RequirementField,
    RequirementUpdateSource,
    new_uuid7,
)
from knotic_api.persistence.active_state import RedisSalesStateRepository
from knotic_api.persistence.hydration import HydrationSource, SalesStateHydrator, StateFieldCipher, StateNotFound
from knotic_api.persistence.repositories import LeadCreate, RequirementRevisionCommand, SessionCreate
from knotic_api.persistence.unit_of_work import UnitOfWork

ROOT = Path(__file__).parents[2]
MASTER_KEY = b"state-hydration-test-key-material-0000000000000001"


@pytest.fixture(scope="module")
def hydration_services() -> tuple[Engine, redis.Redis, RedisSalesStateRepository, StateFieldCipher]:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("PostgreSQL and Redis integration URLs are required for hydration tests")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    client = redis.Redis.from_url(redis_url, decode_responses=False)
    client.flushdb()
    active = RedisSalesStateRepository(client, environment="test", ttl_seconds=60)
    cipher = StateFieldCipher(MASTER_KEY)
    yield engine, client, active, cipher
    client.flushdb()
    engine.dispose()
    command.downgrade(config, "base")


def _create_tenant(engine: Engine, tenant_id: object) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
            {"id": tenant_id, "slug": f"hydration-{tenant_id}"},
        )


@pytest.mark.integration
def test_cache_loss_restart_checkpoint_and_replay_reconstruct_without_duplicate_events(
    hydration_services: tuple[Engine, redis.Redis, RedisSalesStateRepository, StateFieldCipher],
) -> None:
    engine, client, active, cipher = hydration_services
    tenant_id = new_uuid7()
    actor_id = new_uuid7()
    lead_id = new_uuid7()
    session_id = new_uuid7()
    requirement_id = new_uuid7()
    _create_tenant(engine, tenant_id)
    with UnitOfWork(engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        work.leads.create(
            LeadCreate(
                lead_id=lead_id,
                name_ciphertext=cipher.encrypt("Ada", tenant_id=tenant_id, aggregate_id=lead_id, field="name"),
                company_ciphertext=cipher.encrypt(
                    "Analytical Engines", tenant_id=tenant_id, aggregate_id=lead_id, field="company"
                ),
                role_ciphertext=cipher.encrypt("Founder", tenant_id=tenant_id, aggregate_id=lead_id, field="role"),
            )
        )
        session = work.sessions.create(
            SessionCreate(session_id=session_id, locale="en-US", timezone="UTC", lead_id=lead_id)
        )
        work.events.append(
            DomainEvent(
                event_id=new_uuid7(),
                event_type=EventType.SESSION_CREATED,
                occurred_at=session.created_at,
                tenant_id=tenant_id,
                session_id=session_id,
                sequence=1,
                correlation_id=new_uuid7(),
                actor_type="CUSTOMER",
                actor_id=actor_id,
                payload={"session_version": 1, "status": "CREATED"},
            )
        )
    users_50 = Requirement(
        requirement_id=requirement_id,
        tenant_id=tenant_id,
        session_id=session_id,
        field=RequirementField.USERS,
        value=50,
        confirmed=True,
        confidence=0.95,
        source_turn_id=new_uuid7(),
        updated_at=session.created_at + timedelta(seconds=1),
        version=1,
    )
    users_250 = users_50.model_copy(
        update={
            "value": 250,
            "source_turn_id": new_uuid7(),
            "updated_at": session.created_at + timedelta(seconds=2),
            "version": 2,
        }
    )
    for requirement in (users_50, users_250):
        with UnitOfWork(engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            work.requirements.revise_confirmed(
                RequirementRevisionCommand(
                    requirement=requirement,
                    event_id=new_uuid7(),
                    correlation_id=new_uuid7(),
                    actor_type="CUSTOMER",
                    actor_id=actor_id,
                    source=RequirementUpdateSource.CUSTOMER_CONFIRMATION,
                )
            )
    objection_id = new_uuid7()
    with UnitOfWork(engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        work.objections.save(
            Objection(
                objection_id=objection_id,
                tenant_id=tenant_id,
                session_id=session_id,
                category=ObjectionCategory.SECURITY,
                detail="Needs security review",
                status=ObjectionStatus.OPEN,
                first_turn_id=new_uuid7(),
                latest_turn_id=new_uuid7(),
                version=1,
            ),
            detail_ciphertext=cipher.encrypt(
                "Needs security review",
                tenant_id=tenant_id,
                aggregate_id=objection_id,
                field="objection_detail",
            ),
        )

    hydrator = SalesStateHydrator(engine, active, field_cipher=cipher)
    client.flushdb()
    recovered = hydrator.load(tenant_id, session_id, fencing_token=7)
    assert recovered.source == HydrationSource.DURABLE and recovered.cache_warmed
    assert recovered.envelope.state.version == 3
    assert recovered.envelope.event_watermark == 3
    assert recovered.envelope.state.customer is not None
    assert recovered.envelope.state.customer.company == "Analytical Engines"
    assert recovered.envelope.state.requirements[0].value == 250
    assert recovered.envelope.state.memory_facts[0].confidence == 0.95
    assert recovered.envelope.state.objections[0].detail == "Needs security review"

    restarted = SalesStateHydrator(engine, active, field_cipher=cipher)
    cached = restarted.load(tenant_id, session_id)
    assert cached.source == HydrationSource.CACHE

    checkpointed = recovered.envelope.state.model_copy(
        update={
            "version": 4,
            "current_topic": "security",
            "updated_at": session.created_at + timedelta(seconds=3),
        }
    )
    with UnitOfWork(engine, tenant_id=tenant_id, actor_id=actor_id) as work:
        work.events.append(
            DomainEvent(
                event_id=new_uuid7(),
                event_type=EventType.TURN_COMPLETED,
                occurred_at=checkpointed.updated_at,
                tenant_id=tenant_id,
                session_id=session_id,
                sequence=4,
                correlation_id=new_uuid7(),
                actor_type="SYSTEM",
                actor_id=actor_id,
                payload={"session_version": 4, "checkpoint": True},
            )
        )
    hydrator.checkpoint(checkpointed, expected_version=3, event_watermark=4)
    client.flushdb()
    after_loss = restarted.load(tenant_id, session_id)
    assert after_loss.envelope.state.version == 4
    assert after_loss.envelope.state.current_topic == "security"
    assert after_loss.envelope.event_watermark == 4
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("select count(*) from domain_events where tenant_id=:tenant_id and session_id=:session_id"),
                {"tenant_id": tenant_id, "session_id": session_id},
            )
            == 4
        )


@pytest.mark.integration
def test_hydration_hides_cross_tenant_session(
    hydration_services: tuple[Engine, redis.Redis, RedisSalesStateRepository, StateFieldCipher],
) -> None:
    engine, _, active, cipher = hydration_services
    with pytest.raises(StateNotFound):
        SalesStateHydrator(engine, active, field_cipher=cipher).load(new_uuid7(), new_uuid7())
