"""Add idempotent operator handoff fields.

Revision ID: 20260906_0013
Revises: 20260906_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_0013"
down_revision: str | None = "20260906_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.add_column("handoffs", sa.Column("priority", sa.Text(), nullable=False, server_default="NORMAL"))
    op.add_column("handoffs", sa.Column("requested_by", postgresql.UUID(as_uuid=True)))
    op.add_column("handoffs", sa.Column("request_key_hmac", sa.LargeBinary(), nullable=True))
    op.create_check_constraint("ck_handoffs_valid_priority", "handoffs", "priority in ('LOW','NORMAL','HIGH','URGENT')")
    op.create_foreign_key(
        "fk_handoffs_tenant_requested_by_actors",
        "handoffs",
        "actors",
        ["tenant_id", "requested_by"],
        ["tenant_id", "id"],
    )
    op.create_index(
        "uq_handoffs_tenant_request_key",
        "handoffs",
        ["tenant_id", "request_key_hmac"],
        unique=True,
        postgresql_where=sa.text("request_key_hmac is not null"),
    )


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.drop_index("uq_handoffs_tenant_request_key", table_name="handoffs")
    op.drop_constraint("fk_handoffs_tenant_requested_by_actors", "handoffs", type_="foreignkey")
    op.drop_constraint("ck_handoffs_valid_priority", "handoffs", type_="check")
    op.drop_column("handoffs", "request_key_hmac")
    op.drop_column("handoffs", "requested_by")
    op.drop_column("handoffs", "priority")
