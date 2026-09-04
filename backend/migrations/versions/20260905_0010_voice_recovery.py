"""Add durable voice recovery checkpoints.

Revision ID: 20260905_0010
Revises: 20260905_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0010"
down_revision: str | None = "20260905_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.create_table(
        "voice_recovery_states",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("fault", sa.Text()),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("safe_message", sa.Text()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("timezone('utc', now())"),
        ),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("status in ('ACTIVE','RECOVERING','ENDED','FAILED')", name="valid_status"),
        sa.CheckConstraint(
            "fault is null or fault in ('AGORA_TOKEN','AGORA_CONNECTION','SPEECH_INPUT','SPEECH_OUTPUT',"
            "'NETWORK','BACKEND','REDIS','POLICY')",
            name="valid_fault",
        ),
        sa.CheckConstraint("attempt between 0 and 6", name="valid_attempt"),
        sa.CheckConstraint("version > 0", name="positive_version"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["sales_sessions.tenant_id", "sales_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "session_id"),
    )
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table voice_recovery_states enable row level security")
    op.execute("alter table voice_recovery_states force row level security")
    op.execute(
        sa.text(f"create policy tenant_isolation on voice_recovery_states using ({predicate}) with check ({predicate})")
    )
    op.execute("grant select,insert,update on table voice_recovery_states to knotic_runtime")
    op.execute("grant select,delete on table voice_recovery_states to knotic_retention")
    op.execute("grant select on table voice_recovery_states to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_table("voice_recovery_states")
