"""Add actor/source metadata and idempotent requirement revision keys.

Revision ID: 20260822_0003
Revises: 20260821_0002
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0003"
down_revision: str | None = "20260821_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    requirement_columns = {column["name"] for column in inspector.get_columns("requirement_changes")}
    if "actor_type" not in requirement_columns:
        op.add_column("requirement_changes", sa.Column("actor_type", sa.Text()))
        op.add_column("requirement_changes", sa.Column("actor_id", postgresql.UUID(as_uuid=True)))
        op.add_column("requirement_changes", sa.Column("source", sa.Text()))
        op.execute("update requirement_changes set actor_type='SYSTEM', source='WORKFLOW_CONFIRMATION'")
        op.alter_column("requirement_changes", "actor_type", nullable=False)
        op.alter_column("requirement_changes", "source", nullable=False)
    requirement_checks = {constraint["name"] for constraint in inspector.get_check_constraints("requirement_changes")}
    if op.f("ck_requirement_changes_valid_actor_type") not in requirement_checks:
        op.create_check_constraint(
            op.f("ck_requirement_changes_valid_actor_type"),
            "requirement_changes",
            "actor_type in ('CUSTOMER','ASSISTANT','HUMAN_AGENT','SYSTEM','WORKLOAD')",
        )
    if op.f("ck_requirement_changes_valid_source") not in requirement_checks:
        op.create_check_constraint(
            op.f("ck_requirement_changes_valid_source"),
            "requirement_changes",
            "source in ('CUSTOMER_CONFIRMATION','HUMAN_CORRECTION','WORKFLOW_CONFIRMATION')",
        )

    event_columns = {column["name"] for column in inspector.get_columns("domain_events")}
    if "actor_type" not in event_columns:
        op.add_column("domain_events", sa.Column("actor_type", sa.Text()))
        op.execute("update domain_events set actor_type='SYSTEM'")
        op.alter_column("domain_events", "actor_type", nullable=False)
    event_checks = {constraint["name"] for constraint in inspector.get_check_constraints("domain_events")}
    if op.f("ck_domain_events_valid_actor_type") not in event_checks:
        op.create_check_constraint(
            op.f("ck_domain_events_valid_actor_type"),
            "domain_events",
            "actor_type in ('CUSTOMER','ASSISTANT','HUMAN_AGENT','SYSTEM','WORKLOAD')",
        )
    event_indexes = {index["name"] for index in inspector.get_indexes("domain_events")}
    if "uq_domain_events_session_sequence" not in event_indexes:
        op.create_index(
            "uq_domain_events_session_sequence",
            "domain_events",
            ["tenant_id", "session_id", "sequence"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_domain_events_session_sequence", table_name="domain_events")
    op.drop_constraint(op.f("ck_domain_events_valid_actor_type"), "domain_events", type_="check")
    op.drop_column("domain_events", "actor_type")
    op.drop_constraint(op.f("ck_requirement_changes_valid_source"), "requirement_changes", type_="check")
    op.drop_constraint(op.f("ck_requirement_changes_valid_actor_type"), "requirement_changes", type_="check")
    op.drop_column("requirement_changes", "source")
    op.drop_column("requirement_changes", "actor_id")
    op.drop_column("requirement_changes", "actor_type")
