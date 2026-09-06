"""Add governed tenant MCP server registration requests.

Revision ID: 20260906_0014
Revises: 20260906_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_0014"
down_revision: str | None = "20260906_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.create_table(
        "mcp_server_registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("server_url", sa.Text(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("auth_scheme", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="REQUESTED"),
        sa.Column("safe_status_detail", sa.Text(), nullable=False),
        sa.Column("request_key_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("timezone('utc', now())"), nullable=False
        ),
        sa.CheckConstraint("transport in ('STREAMABLE_HTTP','SSE')", name="valid_transport"),
        sa.CheckConstraint("auth_scheme in ('NONE','BEARER','OAUTH2')", name="valid_auth_scheme"),
        sa.CheckConstraint(
            "status in ('REQUESTED','VALIDATING','ACTIVE','REJECTED','DEACTIVATED','FAILED')",
            name="valid_status",
        ),
        sa.CheckConstraint("cardinality(capabilities) between 1 and 5", name="valid_capability_count"),
        sa.CheckConstraint("octet_length(request_key_hmac) = 32", name="valid_request_key_hmac"),
        sa.CheckConstraint("octet_length(request_hash) = 32", name="valid_request_hash"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "requested_by"], ["actors.tenant_id", "actors.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "request_key_hmac", name="uq_mcp_registration_request_key"),
    )
    op.create_index(
        "ix_mcp_registrations_tenant_status_updated",
        "mcp_server_registrations",
        ["tenant_id", "status", "updated_at", "id"],
    )
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table mcp_server_registrations enable row level security")
    op.execute("alter table mcp_server_registrations force row level security")
    op.execute(
        sa.text(
            f"create policy tenant_isolation on mcp_server_registrations using ({predicate}) with check ({predicate})"
        )
    )
    op.execute("grant select,insert,update on table mcp_server_registrations to knotic_runtime")
    op.execute("revoke delete,truncate on table mcp_server_registrations from knotic_runtime")
    op.execute("grant select on table mcp_server_registrations to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_table("mcp_server_registrations")
