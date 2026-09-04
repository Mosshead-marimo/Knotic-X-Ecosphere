"""Add explicit voice processing consent records.

Revision ID: 20260905_0011
Revises: 20260905_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0011"
down_revision: str | None = "20260905_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.create_table(
        "voice_consents",
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("media_region", sa.Text(), nullable=False),
        sa.Column("processing_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recording_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("processing_allowed and not recording_allowed", name="processing_without_recording"),
        sa.CheckConstraint("expires_at > granted_at", name="valid_expiry"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["actors.tenant_id", "actors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["sales_sessions.tenant_id", "sales_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("consent_id"),
        sa.UniqueConstraint("tenant_id", "consent_id"),
        sa.UniqueConstraint("tenant_id", "actor_id", "session_id", name="uq_voice_consents_actor_session"),
    )
    op.create_index("ix_voice_consents_expiry", "voice_consents", ["tenant_id", "expires_at"])
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table voice_consents enable row level security")
    op.execute("alter table voice_consents force row level security")
    op.execute(
        sa.text(f"create policy tenant_isolation on voice_consents using ({predicate}) with check ({predicate})")
    )
    op.execute("grant select,insert,update on table voice_consents to knotic_runtime")
    op.execute("grant select,delete on table voice_consents to knotic_retention")
    op.execute("grant select on table voice_consents to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_table("voice_consents")
