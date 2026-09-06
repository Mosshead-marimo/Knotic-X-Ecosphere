"""Add durable operator role assignments and console query indexes.

Revision ID: 20260906_0012
Revises: 20260905_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_0012"
down_revision: str | None = "20260905_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.create_table(
        "operator_role_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False
        ),
        sa.CheckConstraint("role in ('ADMIN','SUPERVISOR','SALES_REP','CUSTOMER')", name="valid_role"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["actors.tenant_id", "actors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "actor_id", "role", name="uq_operator_role_assignment"),
    )
    op.create_index(
        "ix_operator_roles_tenant_role_actor", "operator_role_assignments", ["tenant_id", "role", "actor_id"]
    )
    op.create_index(
        "ix_domain_events_tenant_occurred_id",
        "domain_events",
        ["tenant_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_provider_updates_tenant_status_next",
        "pending_provider_updates",
        ["tenant_id", "status", "next_attempt_at", "id"],
    )
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table operator_role_assignments enable row level security")
    op.execute("alter table operator_role_assignments force row level security")
    op.execute(
        sa.text(
            f"create policy tenant_isolation on operator_role_assignments using ({predicate}) with check ({predicate})"
        )
    )
    op.execute("grant select,insert,update,delete on table operator_role_assignments to knotic_runtime")
    op.execute("grant select on table operator_role_assignments to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_index("ix_provider_updates_tenant_status_next", table_name="pending_provider_updates")
    op.drop_index("ix_domain_events_tenant_occurred_id", table_name="domain_events")
    op.drop_table("operator_role_assignments")
