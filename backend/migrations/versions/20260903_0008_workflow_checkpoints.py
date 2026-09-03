"""Add encrypted per-turn workflow checkpoints and execution leases.

Revision ID: 20260903_0008
Revises: 20260903_0007
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_0008"
down_revision: str | None = "20260903_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.create_table(
        "workflow_turn_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False),
        sa.Column("state_ciphertext", sa.LargeBinary()),
        sa.Column("state_hash", sa.LargeBinary()),
        sa.Column("safe_error_code", sa.Text()),
        sa.Column("safe_error_message", sa.Text()),
        sa.Column("failed_node", sa.Text()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False
        ),
        sa.CheckConstraint(
            "attempt_count between 1 and 3", name=op.f("ck_workflow_turn_checkpoints_valid_attempt_count")
        ),
        sa.CheckConstraint(
            "(status = 'COMMITTED') = "
            "(state_ciphertext is not null and state_hash is not null and committed_at is not null)",
            name=op.f("ck_workflow_turn_checkpoints_committed_has_state"),
        ),
        sa.CheckConstraint(
            "status in ('STARTED','RETRYABLE_FAILURE','COMMITTED','TERMINAL_FAILURE')",
            name=op.f("ck_workflow_turn_checkpoints_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_workflow_turn_checkpoints_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["sales_sessions.tenant_id", "sales_sessions.id"],
            name=op.f("fk_workflow_turn_checkpoints_tenant_id_session_id_sales_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_turn_checkpoints")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_workflow_turn_checkpoints_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "session_id", "turn_id", name=op.f("uq_workflow_turn_checkpoints_tenant_id_session_id_turn_id")
        ),
    )
    op.create_index(
        "ix_workflow_turn_checkpoints_tenant_session_updated_id",
        "workflow_turn_checkpoints",
        ["tenant_id", "session_id", "updated_at", "id"],
    )
    op.create_index(
        "ix_workflow_turn_checkpoints_expired_lease",
        "workflow_turn_checkpoints",
        ["lease_expires_at", "id"],
        postgresql_where=sa.text("status = 'STARTED'"),
    )
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table workflow_turn_checkpoints enable row level security")
    op.execute("alter table workflow_turn_checkpoints force row level security")
    op.execute(
        sa.text(
            f"create policy tenant_isolation on workflow_turn_checkpoints using ({predicate}) with check ({predicate})"
        )
    )
    op.execute("grant select,insert,update on table workflow_turn_checkpoints to knotic_runtime")
    op.execute("revoke delete,truncate on table workflow_turn_checkpoints from knotic_runtime")
    op.execute("grant select,delete on table workflow_turn_checkpoints to knotic_retention")
    op.execute("grant select on table workflow_turn_checkpoints to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_table("workflow_turn_checkpoints")
