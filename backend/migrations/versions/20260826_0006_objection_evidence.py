"""Add immutable objection evidence and correct active-objection uniqueness.

Revision ID: 20260826_0006
Revises: 20260822_0005
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0006"
down_revision: str | None = "20260822_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.execute("drop index if exists uq_objections_active_category")
    op.execute(
        "create unique index uq_objections_active_category on objections "
        "(tenant_id,session_id,category) where status = 'OPEN'"
    )
    op.execute("update objections set category='TIMELINE' where category='TIMING'")
    op.execute("update objections set category='IMPLEMENTATION' where category='INTEGRATION'")

    op.create_table(
        "objection_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("evidence_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("risk_flags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("policy_action", sa.Text(), nullable=False),
        sa.Column("escalation_required", sa.Boolean(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('utc', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("confidence between 0 and 1", name=op.f("ck_objection_evidence_valid_confidence")),
        sa.CheckConstraint(
            "start_offset >= 0 and end_offset > start_offset",
            name=op.f("ck_objection_evidence_valid_span"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "objection_id"],
            ["objections.tenant_id", "objections.id"],
            name=op.f("fk_objection_evidence_tenant_id_objection_id_objections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["sales_sessions.tenant_id", "sales_sessions.id"],
            name=op.f("fk_objection_evidence_tenant_id_session_id_sales_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objection_evidence")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_objection_evidence_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "objection_id",
            "source_turn_id",
            name=op.f("uq_objection_evidence_tenant_id_session_id_objection_id_source_turn_id"),
        ),
    )
    op.create_index(
        "ix_objection_evidence_tenant_session_category_detected_id",
        "objection_evidence",
        ["tenant_id", "session_id", "category", "detected_at", "id"],
    )
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table objection_evidence enable row level security")
    op.execute("alter table objection_evidence force row level security")
    op.execute(
        sa.text(f"create policy tenant_isolation on objection_evidence using ({predicate}) with check ({predicate})")
    )
    op.execute("grant select,insert on table objection_evidence to knotic_runtime")
    op.execute("revoke update,delete,truncate on table objection_evidence from knotic_runtime")
    op.execute("grant select,delete on table objection_evidence to knotic_retention")
    op.execute("grant select on table objection_evidence to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_table("objection_evidence")
    op.execute("update objections set category='TIMING' where category='TIMELINE'")
    op.execute("update objections set category='INTEGRATION' where category='IMPLEMENTATION'")
    op.execute("drop index if exists uq_objections_active_category")
    op.execute(
        "create unique index uq_objections_active_category on objections "
        "(tenant_id,session_id,category) where status = 'ACTIVE'"
    )
