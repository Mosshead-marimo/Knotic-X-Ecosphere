"""Add durable projection checkpoint and requirement confidence metadata.

Revision ID: 20260822_0004
Revises: 20260822_0003
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0004"
down_revision: str | None = "20260822_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    session_columns = {column["name"] for column in inspector.get_columns("sales_sessions")}
    if "checkpoint_event_sequence" not in session_columns:
        op.add_column(
            "sales_sessions",
            sa.Column("checkpoint_event_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        )
        op.add_column(
            "sales_sessions",
            sa.Column("projection_schema_version", sa.Integer(), nullable=False, server_default="1"),
        )
        op.add_column("sales_sessions", sa.Column("checkpointed_at", sa.DateTime(timezone=True)))
    session_checks = {constraint["name"] for constraint in inspector.get_check_constraints("sales_sessions")}
    if op.f("ck_sales_sessions_valid_checkpoint_event_sequence") not in session_checks:
        op.create_check_constraint(
            op.f("ck_sales_sessions_valid_checkpoint_event_sequence"),
            "sales_sessions",
            "checkpoint_event_sequence >= 0",
        )
    if op.f("ck_sales_sessions_valid_projection_schema_version") not in session_checks:
        op.create_check_constraint(
            op.f("ck_sales_sessions_valid_projection_schema_version"),
            "sales_sessions",
            "projection_schema_version >= 1",
        )

    requirement_columns = {column["name"] for column in inspector.get_columns("requirements_current")}
    if "confidence" not in requirement_columns:
        op.add_column(
            "requirements_current",
            sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="1"),
        )
    requirement_checks = {constraint["name"] for constraint in inspector.get_check_constraints("requirements_current")}
    if op.f("ck_requirements_current_valid_confidence") not in requirement_checks:
        op.create_check_constraint(
            op.f("ck_requirements_current_valid_confidence"),
            "requirements_current",
            "confidence between 0 and 1",
        )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_requirements_current_valid_confidence"), "requirements_current", type_="check")
    op.drop_column("requirements_current", "confidence")
    op.drop_constraint(op.f("ck_sales_sessions_valid_projection_schema_version"), "sales_sessions", type_="check")
    op.drop_constraint(op.f("ck_sales_sessions_valid_checkpoint_event_sequence"), "sales_sessions", type_="check")
    op.drop_column("sales_sessions", "checkpointed_at")
    op.drop_column("sales_sessions", "projection_schema_version")
    op.drop_column("sales_sessions", "checkpoint_event_sequence")
