"""Add durable ordered voice control events.

Revision ID: 20260905_0009
Revises: 20260903_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260905_0009"
down_revision: str | None = "20260903_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.create_table(
        "voice_control_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stream_sequence", sa.BigInteger(), nullable=False),
        sa.Column("server_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(none_as_null=True), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("event_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("timezone('utc', now())")
        ),
        sa.CheckConstraint("stream_sequence > 0 and server_sequence > 0", name="valid_sequences"),
        sa.CheckConstraint("schema_version = 1", name="valid_schema_version"),
        sa.CheckConstraint("octet_length(event_hash) = 32", name="valid_event_hash"),
        sa.CheckConstraint(
            "event_type in ('CLIENT_READY','RTC_CONNECTED','RTC_RECONNECTING','RTC_DISCONNECTED',"
            "'MICROPHONE_MUTED','MICROPHONE_UNMUTED','CALL_ENDED')",
            name="valid_event_type",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["sales_sessions.tenant_id", "sales_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("tenant_id", "event_id"),
        sa.UniqueConstraint(
            "tenant_id", "session_id", "stream_id", "stream_sequence", name="uq_voice_control_events_stream_sequence"
        ),
        sa.UniqueConstraint(
            "tenant_id", "session_id", "server_sequence", name="uq_voice_control_events_server_sequence"
        ),
    )
    op.create_index(
        "ix_voice_control_events_replay", "voice_control_events", ["tenant_id", "session_id", "server_sequence"]
    )
    predicate = "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    op.execute("alter table voice_control_events enable row level security")
    op.execute("alter table voice_control_events force row level security")
    op.execute(
        sa.text(f"create policy tenant_isolation on voice_control_events using ({predicate}) with check ({predicate})")
    )
    op.execute("grant select,insert on table voice_control_events to knotic_runtime")
    op.execute("grant select,delete on table voice_control_events to knotic_retention")
    op.execute("grant select on table voice_control_events to knotic_auditor")


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_table("voice_control_events")
