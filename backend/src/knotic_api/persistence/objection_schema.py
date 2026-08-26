"""Runtime SQLAlchemy contract for objection evidence added after schema v1."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from .schema_v1 import NAMING_CONVENTION, utc_now

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
uuid = UUID(as_uuid=True)

objection_evidence = sa.Table(
    "objection_evidence",
    metadata,
    sa.Column("id", uuid, primary_key=True),
    sa.Column("tenant_id", uuid, nullable=False),
    sa.Column("session_id", uuid, nullable=False),
    sa.Column("objection_id", uuid, nullable=False),
    sa.Column("source_turn_id", uuid, nullable=False),
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("start_offset", sa.Integer, nullable=False),
    sa.Column("end_offset", sa.Integer, nullable=False),
    sa.Column("evidence_sha256", sa.LargeBinary, nullable=False),
    sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
    sa.Column("risk_flags", ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("policy_action", sa.Text, nullable=False),
    sa.Column("escalation_required", sa.Boolean, nullable=False),
    sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.UniqueConstraint("tenant_id", "id"),
    sa.UniqueConstraint("tenant_id", "session_id", "objection_id", "source_turn_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "session_id"],
        ["sales_sessions.tenant_id", "sales_sessions.id"],
        ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
        ["tenant_id", "objection_id"],
        ["objections.tenant_id", "objections.id"],
        ondelete="CASCADE",
    ),
    sa.CheckConstraint("start_offset >= 0 and end_offset > start_offset", name="valid_span"),
    sa.CheckConstraint("confidence between 0 and 1", name="valid_confidence"),
)
