"""Align durable session states with the version-1 domain model.

Revision ID: 20260821_0002
Revises: 20260821_0001
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_0002"
down_revision: str | None = "20260821_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_sales_sessions_valid_status"), "sales_sessions", type_="check")
    op.drop_constraint(op.f("ck_sales_sessions_terminal_has_end"), "sales_sessions", type_="check")
    op.create_check_constraint(
        op.f("ck_sales_sessions_valid_status"),
        "sales_sessions",
        "status in ('CREATED','ACTIVE','ENDING','ENDED','FAILED')",
    )
    op.create_check_constraint(
        op.f("ck_sales_sessions_terminal_has_end"),
        "sales_sessions",
        "(status in ('ENDED','FAILED')) = (ended_at is not null)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sales_sessions_valid_status"), "sales_sessions", type_="check")
    op.drop_constraint(op.f("ck_sales_sessions_terminal_has_end"), "sales_sessions", type_="check")
    # Preserve rollback availability for populated databases while translating
    # states that the previous application version cannot understand.
    op.execute("update sales_sessions set status='ACTIVE' where status='ENDING'")
    op.execute("update sales_sessions set status='ABANDONED' where status='FAILED'")
    op.create_check_constraint(
        op.f("ck_sales_sessions_valid_status"),
        "sales_sessions",
        "status in ('CREATED','ACTIVE','ENDED','ABANDONED')",
    )
    op.create_check_constraint(
        op.f("ck_sales_sessions_terminal_has_end"),
        "sales_sessions",
        "(status in ('ENDED','ABANDONED')) = (ended_at is not null)",
    )
