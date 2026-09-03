"""Durable workflow-turn checkpoint table added after the frozen v1 schema."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BYTEA, UUID

_NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = sa.MetaData(naming_convention=_NAMING_CONVENTION)
utc_now = sa.text("timezone('utc', now())")

workflow_turn_checkpoints = sa.Table(
    "workflow_turn_checkpoints",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("session_id", UUID(as_uuid=True), nullable=False),
    sa.Column("turn_id", UUID(as_uuid=True), nullable=False),
    sa.Column("input_hash", BYTEA, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("attempt_count", sa.SmallInteger, nullable=False),
    sa.Column("state_ciphertext", BYTEA),
    sa.Column("state_hash", BYTEA),
    sa.Column("safe_error_code", sa.Text),
    sa.Column("safe_error_message", sa.Text),
    sa.Column("failed_node", sa.Text),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("committed_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "session_id"],
        ["sales_sessions.tenant_id", "sales_sessions.id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("tenant_id", "id"),
    sa.UniqueConstraint("tenant_id", "session_id", "turn_id"),
    sa.CheckConstraint(
        "status in ('STARTED','RETRYABLE_FAILURE','COMMITTED','TERMINAL_FAILURE')",
        name="valid_status",
    ),
    sa.CheckConstraint("attempt_count between 1 and 3", name="valid_attempt_count"),
    sa.CheckConstraint(
        "(status = 'COMMITTED') = "
        "(state_ciphertext is not null and state_hash is not null and committed_at is not null)",
        name="committed_has_state",
    ),
)
sa.Index(
    "ix_workflow_turn_checkpoints_tenant_session_updated_id",
    workflow_turn_checkpoints.c.tenant_id,
    workflow_turn_checkpoints.c.session_id,
    workflow_turn_checkpoints.c.updated_at,
    workflow_turn_checkpoints.c.id,
)
sa.Index(
    "ix_workflow_turn_checkpoints_expired_lease",
    workflow_turn_checkpoints.c.lease_expires_at,
    workflow_turn_checkpoints.c.id,
    postgresql_where=workflow_turn_checkpoints.c.status == "STARTED",
)
