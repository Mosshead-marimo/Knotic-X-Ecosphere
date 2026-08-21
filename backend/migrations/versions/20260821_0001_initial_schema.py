"""Create the initial tenant-isolated durable schema.

Revision ID: 20260821_0001
Revises: None
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from knotic_api.persistence.schema_v1 import TENANT_TABLE_NAMES, metadata

revision: str = "20260821_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("select pg_advisory_xact_lock(4815162342)")
    op.execute("create extension if not exists vector")
    metadata.create_all(bind=bind, checkfirst=False)

    for table_name in sorted(TENANT_TABLE_NAMES):
        op.execute(sa.text(f'alter table "{table_name}" enable row level security'))
        op.execute(sa.text(f'alter table "{table_name}" force row level security'))
        qualifier = "tenant_id is null or " if table_name == "knowledge_index_versions" else ""
        predicate = f"({qualifier}tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
        op.execute(
            sa.text(f'create policy tenant_isolation on "{table_name}" using {predicate} with check {predicate}')
        )


def downgrade() -> None:
    op.execute("select pg_advisory_xact_lock(4815162342)")
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)
