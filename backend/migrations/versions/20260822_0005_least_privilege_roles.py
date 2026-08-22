"""Create least-privilege database group roles.

Revision ID: 20260822_0005
Revises: 20260822_0004
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260822_0005"
down_revision: str | None = "20260822_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = ("knotic_runtime", "knotic_retention", "knotic_auditor")
_APPEND_ONLY = ("requirement_changes", "domain_events", "audit_events", "inbox_receipts")


def upgrade() -> None:
    for role in _ROLES:
        op.execute(
            f"""
            do $$ begin
              if not exists (select 1 from pg_roles where rolname = '{role}') then
                create role {role} nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
              end if;
            end $$
            """  # noqa: S608 - role is selected only from the static _ROLES allowlist
        )
        op.execute(f"grant {role} to current_user")
        op.execute(f"grant usage on schema public to {role}")

    op.execute("revoke all on all tables in schema public from public")
    op.execute("grant select,insert,update,delete on all tables in schema public to knotic_runtime")
    for table in _APPEND_ONLY:
        op.execute(f"revoke update,delete,truncate on table {table} from knotic_runtime")
    op.execute("revoke insert,update,delete,truncate on table alembic_version from knotic_runtime")

    op.execute("grant select,update,delete on all tables in schema public to knotic_retention")
    op.execute("grant insert on table audit_events,operations to knotic_retention")
    op.execute("revoke update,delete,truncate on table audit_events from knotic_retention")
    op.execute("revoke insert,update,delete,truncate on table alembic_version from knotic_retention")

    op.execute("grant select on all tables in schema public to knotic_auditor")
    op.execute("revoke insert,update,delete,truncate on all tables in schema public from knotic_auditor")
    op.execute("alter default privileges in schema public grant select on tables to knotic_auditor")
    op.execute(
        "alter default privileges in schema public grant select,insert,update,delete on tables to knotic_runtime"
    )


def downgrade() -> None:
    op.execute("alter default privileges in schema public revoke select on tables from knotic_auditor")
    op.execute(
        "alter default privileges in schema public revoke select,insert,update,delete on tables from knotic_runtime"
    )
    for role in reversed(_ROLES):
        op.execute(f"revoke all privileges on all tables in schema public from {role}")
        op.execute(f"revoke all privileges on schema public from {role}")
        op.execute(f"revoke {role} from current_user")
        op.execute(f"drop role if exists {role}")
