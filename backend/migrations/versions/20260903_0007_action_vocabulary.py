"""Align persisted next actions with the accepted FR-10 vocabulary.

Revision ID: 20260903_0007
Revises: 20260826_0006
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_0007"
down_revision: str | None = "20260826_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "update sales_sessions set next_best_action='CREATE_FOLLOWUP' where next_best_action='SCHEDULE_FOLLOWUP'"
    )
    op.execute("update sales_sessions set next_best_action='END_CALL' where next_best_action='END_SESSION'")


def downgrade() -> None:
    op.execute(
        "update sales_sessions set next_best_action='SCHEDULE_FOLLOWUP' where next_best_action='CREATE_FOLLOWUP'"
    )
    op.execute("update sales_sessions set next_best_action='END_SESSION' where next_best_action='END_CALL'")
