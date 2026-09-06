from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from knotic_api.persistence.schema_v1 import EXPECTED_TABLE_NAMES, TENANT_TABLE_NAMES, metadata
from knotic_api.persistence.workflow_schema import workflow_turn_checkpoints

ROOT = Path(__file__).parents[2]
EXPECTED_ENTITIES = {
    "tenants",
    "actors",
    "actor_identities",
    "leads",
    "provider_links",
    "sales_sessions",
    "calls",
    "messages",
    "requirements_current",
    "requirement_changes",
    "objections",
    "session_competitors",
    "qualification_snapshots",
    "tool_calls",
    "tool_results",
    "operations",
    "idempotency_records",
    "meetings",
    "followups",
    "handoffs",
    "session_outcomes",
    "pending_provider_updates",
    "domain_events",
    "outbox_messages",
    "inbox_receipts",
    "audit_events",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_embeddings",
    "knowledge_index_versions",
}


def test_schema_matches_the_documented_entity_catalog() -> None:
    assert EXPECTED_TABLE_NAMES == EXPECTED_ENTITIES
    assert TENANT_TABLE_NAMES == EXPECTED_ENTITIES - {"tenants"}
    assert workflow_turn_checkpoints.name == "workflow_turn_checkpoints"


def test_every_foreign_key_has_a_left_prefix_index() -> None:
    for table in metadata.tables.values():
        candidates = [tuple(index.columns.keys()) for index in table.indexes]
        candidates.extend(
            tuple(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        )
        candidates.append(tuple(table.primary_key.columns.keys()))
        for foreign_key in table.foreign_key_constraints:
            columns = tuple(foreign_key.columns.keys())
            assert any(candidate[: len(columns)] == columns for candidate in candidates), (
                f"{table.name}{columns} has no supporting left-prefix index"
            )


def _migration_config(database_url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.integration
def test_migration_constraints_rls_query_plan_and_populated_rollback() -> None:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("KNOTIC_TEST_DATABASE_URL is required for PostgreSQL integration tests")

    config = _migration_config(database_url)
    engine = sa.create_engine(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        inspector = sa.inspect(engine)
        assert EXPECTED_ENTITIES <= set(inspector.get_table_names())
        assert "workflow_turn_checkpoints" in inspector.get_table_names()
        assert "voice_control_events" in inspector.get_table_names()
        assert "voice_recovery_states" in inspector.get_table_names()
        assert "voice_consents" in inspector.get_table_names()
        assert "mcp_server_registrations" in inspector.get_table_names()
        with engine.connect() as connection:
            rls = connection.execute(
                sa.text(
                    "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname = any(:names)"
                ),
                {"names": sorted(TENANT_TABLE_NAMES)},
            ).all()
            assert len(rls) == len(TENANT_TABLE_NAMES)
            assert all(enabled and forced for _, enabled, forced in rls)
            registration_policy = connection.execute(
                sa.text(
                    "select relrowsecurity,relforcerowsecurity from pg_class where relname='mcp_server_registrations'"
                )
            ).one()
            assert registration_policy == (True, True)
            assert connection.scalar(sa.text("select exists(select 1 from pg_extension where extname='vector')"))

        tenant_id = uuid4()
        lead_id = uuid4()
        session_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
                {"id": tenant_id, "slug": f"test-{tenant_id}"},
            )
            connection.execute(
                sa.text("insert into leads(id,tenant_id) values (:id,:tenant_id)"),
                {"id": lead_id, "tenant_id": tenant_id},
            )
            connection.execute(
                sa.text(
                    "insert into sales_sessions(id,tenant_id,lead_id,status,locale,timezone) "
                    "values (:id,:tenant_id,:lead_id,'CREATED','en-US','UTC')"
                ),
                {"id": session_id, "tenant_id": tenant_id, "lead_id": lead_id},
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                sa.text(
                    "insert into sales_sessions(id,tenant_id,status,locale,timezone,qualification_score) "
                    "values (:id,:tenant_id,'CREATED','en-US','UTC',101)"
                ),
                {"id": uuid4(), "tenant_id": tenant_id},
            )

        with engine.begin() as connection:
            connection.execute(sa.text("set local enable_seqscan = off"))
            plan = "\n".join(
                row[0]
                for row in connection.execute(
                    sa.text(
                        "explain select id from sales_sessions "
                        "where tenant_id=:tenant_id and status='CREATED' order by updated_at,id"
                    ),
                    {"tenant_id": tenant_id},
                )
            )
            assert "ix_sales_sessions_tenant_status_updated_id" in plan

        # Rehearse a populated rollback and a fresh re-upgrade. The dedicated
        # integration database is intentionally disposable.
        command.downgrade(config, "base")
        assert not (EXPECTED_ENTITIES & set(sa.inspect(engine).get_table_names()))
        command.upgrade(config, "head")
        assert EXPECTED_ENTITIES <= set(sa.inspect(engine).get_table_names())
    finally:
        command.downgrade(config, "base")
        engine.dispose()


@pytest.mark.integration
def test_requirement_metadata_migration_upgrades_populated_previous_schema() -> None:
    database_url = os.getenv("KNOTIC_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("KNOTIC_TEST_DATABASE_URL is required for PostgreSQL integration tests")

    config = _migration_config(database_url)
    engine = sa.create_engine(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "20260821_0002")
    tenant_id = uuid4()
    session_id = uuid4()
    requirement_id = uuid4()
    source_turn_id = uuid4()
    event_id = uuid4()
    correlation_id = uuid4()
    try:
        # Revision 0001 historically imported live metadata. Remove newer
        # structures to reproduce an actual database created by release 0002.
        with engine.begin() as connection:
            connection.execute(sa.text("drop index if exists uq_domain_events_session_sequence"))
            connection.execute(
                sa.text("alter table requirement_changes drop constraint if exists ck_requirement_changes_valid_source")
            )
            connection.execute(
                sa.text(
                    "alter table requirement_changes drop constraint if exists ck_requirement_changes_valid_actor_type"
                )
            )
            connection.execute(
                sa.text("alter table domain_events drop constraint if exists ck_domain_events_valid_actor_type")
            )
            connection.execute(sa.text("alter table requirement_changes drop column source"))
            connection.execute(sa.text("alter table requirement_changes drop column actor_id"))
            connection.execute(sa.text("alter table requirement_changes drop column actor_type"))
            connection.execute(sa.text("alter table domain_events drop column actor_type"))
            connection.execute(
                sa.text("insert into tenants(id,slug,status) values (:id,:slug,'ACTIVE')"),
                {"id": tenant_id, "slug": f"migration-{tenant_id}"},
            )
            connection.execute(
                sa.text("select set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
            )
            connection.execute(
                sa.text(
                    "insert into sales_sessions(id,tenant_id,status,locale,timezone) "
                    "values (:id,:tenant_id,'CREATED','en-US','UTC')"
                ),
                {"id": session_id, "tenant_id": tenant_id},
            )
            connection.execute(
                sa.text(
                    "insert into requirements_current"
                    "(id,tenant_id,session_id,field,value_integer,confirmed_at,source_turn_id,version) "
                    "values (:id,:tenant_id,:session_id,'users',50,now(),:turn_id,1)"
                ),
                {
                    "id": requirement_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "turn_id": source_turn_id,
                },
            )
            connection.execute(
                sa.text(
                    "insert into requirement_changes"
                    "(id,tenant_id,session_id,requirement_id,field,old_value,new_value,confirmed,"
                    "source_turn_id,event_id) values "
                    "(:event_id,:tenant_id,:session_id,:requirement_id,'users',null,'50',true,"
                    ":turn_id,:event_id)"
                ),
                {
                    "event_id": event_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "requirement_id": requirement_id,
                    "turn_id": source_turn_id,
                },
            )
            connection.execute(
                sa.text(
                    "insert into domain_events"
                    "(id,tenant_id,session_id,event_id,event_type,event_version,sequence,occurred_at,correlation_id,"
                    "actor_id,payload,payload_schema_version) values "
                    "(:event_id,:tenant_id,:session_id,:event_id,'requirement.updated',1,1,now(),:correlation_id,"
                    "null,cast(:payload as jsonb),1)"
                ),
                {
                    "event_id": event_id,
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "correlation_id": correlation_id,
                    "payload": json.dumps({"field": "users", "old_value": None, "new_value": 50, "confirmed": True}),
                },
            )

        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.text("select set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
            )
            change = (
                connection.execute(
                    sa.text(
                        "select actor_type,actor_id,source from requirement_changes "
                        "where tenant_id=:tenant_id and event_id=:event_id"
                    ),
                    {"tenant_id": tenant_id, "event_id": event_id},
                )
                .mappings()
                .one()
            )
            event_actor = connection.scalar(
                sa.text("select actor_type from domain_events where tenant_id=:tenant_id and event_id=:event_id"),
                {"tenant_id": tenant_id, "event_id": event_id},
            )
        assert change == {"actor_type": "SYSTEM", "actor_id": None, "source": "WORKFLOW_CONFIRMATION"}
        assert event_actor == "SYSTEM"

        command.downgrade(config, "20260821_0002")
        assert "actor_type" not in {column["name"] for column in sa.inspect(engine).get_columns("domain_events")}
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.text("select set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
            )
            assert (
                connection.scalar(
                    sa.text("select count(*) from requirement_changes where tenant_id=:tenant_id"),
                    {"tenant_id": tenant_id},
                )
                == 1
            )
    finally:
        command.downgrade(config, "base")
        engine.dispose()
