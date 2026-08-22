"""Frozen SQLAlchemy metadata for the initial production schema.

This module is imported by the initial Alembic revision. Never edit it after that
revision has shipped; subsequent changes belong in new explicit migrations.
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA, JSONB, UUID
from sqlalchemy.sql.schema import SchemaItem

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

uuid = UUID(as_uuid=True)
jsonb = JSONB(none_as_null=True)
utc_now = sa.text("timezone('utc', now())")


def timestamps(*, versioned: bool = True) -> tuple[SchemaItem, ...]:
    columns: list[SchemaItem] = [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    ]
    if versioned:
        columns.append(sa.Column("version", sa.BigInteger, nullable=False, server_default="1"))
        columns.append(sa.CheckConstraint("version > 0", name="positive_version"))
    return tuple(columns)


def tenant_columns() -> tuple[SchemaItem, ...]:
    return (
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )


def session_fk(*, ondelete: str = "CASCADE") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "session_id"],
        ["sales_sessions.tenant_id", "sales_sessions.id"],
        ondelete=ondelete,
    )


tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column("id", uuid, primary_key=True),
    sa.Column("slug", sa.Text, nullable=False, unique=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("retention_policy_version", sa.Integer, nullable=False, server_default="1"),
    *timestamps(),
    sa.CheckConstraint("status in ('ACTIVE','SUSPENDED','DELETED')", name="valid_status"),
)

actors = sa.Table(
    "actors",
    metadata,
    *tenant_columns(),
    sa.Column("actor_type", sa.Text, nullable=False),
    sa.Column("display_name_ciphertext", BYTEA, nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    *timestamps(),
    sa.CheckConstraint("actor_type in ('USER','SERVICE','ANONYMOUS')", name="valid_actor_type"),
    sa.CheckConstraint("status in ('ACTIVE','DISABLED','ERASED')", name="valid_status"),
)
sa.Index("ix_actors_tenant_status_id", actors.c.tenant_id, actors.c.status, actors.c.id)

actor_identities = sa.Table(
    "actor_identities",
    metadata,
    *tenant_columns(),
    sa.Column("actor_id", uuid, nullable=False),
    sa.Column("issuer", sa.Text, nullable=False),
    sa.Column("subject_ciphertext", BYTEA, nullable=False),
    sa.Column("subject_hmac", BYTEA, nullable=False),
    *timestamps(versioned=False),
    sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["actors.tenant_id", "actors.id"], ondelete="CASCADE"),
    sa.UniqueConstraint("tenant_id", "issuer", "subject_hmac"),
)
sa.Index("ix_actor_identities_tenant_actor", actor_identities.c.tenant_id, actor_identities.c.actor_id)

leads = sa.Table(
    "leads",
    metadata,
    *tenant_columns(),
    sa.Column("name_ciphertext", BYTEA),
    sa.Column("email_ciphertext", BYTEA),
    sa.Column("email_hmac", BYTEA),
    sa.Column("phone_ciphertext", BYTEA),
    sa.Column("phone_hmac", BYTEA),
    sa.Column("company_ciphertext", BYTEA),
    sa.Column("role_ciphertext", BYTEA),
    sa.Column("crm_status", sa.Text, nullable=False, server_default="NEW"),
    sa.Column("qualification_summary", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    *timestamps(),
)
sa.Index("ix_leads_tenant_crm_status_updated_id", leads.c.tenant_id, leads.c.crm_status, leads.c.updated_at, leads.c.id)
sa.Index("ix_leads_tenant_email_hmac", leads.c.tenant_id, leads.c.email_hmac)

provider_links = sa.Table(
    "provider_links",
    metadata,
    *tenant_columns(),
    sa.Column("lead_id", uuid, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("external_id_ciphertext", BYTEA, nullable=False),
    sa.Column("external_id_hmac", BYTEA, nullable=False),
    sa.Column("sync_status", sa.Text, nullable=False, server_default="PENDING"),
    sa.Column("safe_error_code", sa.Text),
    *timestamps(),
    sa.ForeignKeyConstraint(["tenant_id", "lead_id"], ["leads.tenant_id", "leads.id"], ondelete="CASCADE"),
    sa.UniqueConstraint("tenant_id", "provider", "external_id_hmac"),
)
sa.Index("ix_provider_links_tenant_lead", provider_links.c.tenant_id, provider_links.c.lead_id)
sa.Index(
    "ix_provider_links_pending",
    provider_links.c.updated_at,
    provider_links.c.id,
    postgresql_where=provider_links.c.sync_status == "PENDING",
)

sales_sessions = sa.Table(
    "sales_sessions",
    metadata,
    *tenant_columns(),
    sa.Column("lead_id", uuid),
    sa.Column("status", sa.Text, nullable=False, server_default="CREATED"),
    sa.Column("locale", sa.Text, nullable=False),
    sa.Column("timezone", sa.Text, nullable=False),
    sa.Column("current_topic", sa.Text),
    sa.Column("current_intent", sa.Text),
    sa.Column("buying_stage", sa.Text),
    sa.Column("qualification_score", sa.SmallInteger),
    sa.Column("next_best_action", sa.Text),
    sa.Column("summary_ciphertext", BYTEA),
    sa.Column("latest_request_ciphertext", BYTEA),
    sa.Column("outcome", sa.Text),
    sa.Column("checkpoint_event_sequence", sa.BigInteger, nullable=False, server_default="0"),
    sa.Column("projection_schema_version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("checkpointed_at", sa.DateTime(timezone=True)),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    *timestamps(),
    # Deletion first nulls lead_id in application code. Composite SET NULL
    # would incorrectly attempt to null the non-null tenant_id column too.
    sa.ForeignKeyConstraint(["tenant_id", "lead_id"], ["leads.tenant_id", "leads.id"]),
    sa.CheckConstraint("status in ('CREATED','ACTIVE','ENDED','ABANDONED')", name="valid_status"),
    sa.CheckConstraint("qualification_score between 0 and 100", name="valid_qualification_score"),
    sa.CheckConstraint("checkpoint_event_sequence >= 0", name="valid_checkpoint_event_sequence"),
    sa.CheckConstraint("projection_schema_version >= 1", name="valid_projection_schema_version"),
    sa.CheckConstraint("(status in ('ENDED','ABANDONED')) = (ended_at is not null)", name="terminal_has_end"),
)
sa.Index(
    "ix_sales_sessions_tenant_status_updated_id",
    sales_sessions.c.tenant_id,
    sales_sessions.c.status,
    sales_sessions.c.updated_at,
    sales_sessions.c.id,
)
sa.Index(
    "ix_sales_sessions_tenant_lead_created_id",
    sales_sessions.c.tenant_id,
    sales_sessions.c.lead_id,
    sales_sessions.c.created_at,
    sales_sessions.c.id,
)


def session_table(name: str, *items: SchemaItem) -> sa.Table:
    return sa.Table(
        name, metadata, *tenant_columns(), sa.Column("session_id", uuid, nullable=False), *items, session_fk()
    )


calls = session_table(
    "calls",
    sa.Column("agora_channel_hmac", BYTEA, nullable=False),
    sa.Column("server_participant_ids", ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("joined_at", sa.DateTime(timezone=True)),
    sa.Column("disconnected_at", sa.DateTime(timezone=True)),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.Column("disconnect_reason", sa.Text),
    *timestamps(),
    sa.CheckConstraint("status in ('CONNECTING','ACTIVE','DISCONNECTED','ENDED','FAILED')", name="valid_status"),
)
sa.Index("ix_calls_tenant_session_created_id", calls.c.tenant_id, calls.c.session_id, calls.c.created_at, calls.c.id)
sa.Index(
    "uq_calls_one_active_session",
    calls.c.tenant_id,
    calls.c.session_id,
    unique=True,
    postgresql_where=calls.c.status.in_(["CONNECTING", "ACTIVE"]),
)

messages = session_table(
    "messages",
    sa.Column("turn_id", uuid, nullable=False),
    sa.Column("response_id", uuid),
    sa.Column("sequence", sa.BigInteger, nullable=False),
    sa.Column("speaker", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("content_ciphertext", BYTEA, nullable=False),
    sa.Column("locale", sa.Text, nullable=False),
    sa.Column("provider_event_hmac", BYTEA),
    sa.Column("interrupted", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("started_at", sa.DateTime(timezone=True)),
    sa.Column("completed_at", sa.DateTime(timezone=True)),
    *timestamps(versioned=False),
    sa.UniqueConstraint("tenant_id", "session_id", "sequence", "speaker"),
    sa.CheckConstraint("sequence >= 0", name="nonnegative_sequence"),
)
sa.Index(
    "ix_messages_tenant_session_created_id",
    messages.c.tenant_id,
    messages.c.session_id,
    messages.c.created_at,
    messages.c.id,
)

requirements_current = session_table(
    "requirements_current",
    sa.Column("field", sa.Text, nullable=False),
    sa.Column("value_integer", sa.BigInteger),
    sa.Column("value_text", sa.Text),
    sa.Column("value_text_array", ARRAY(sa.Text)),
    sa.Column("value_numeric", sa.Numeric),
    sa.Column("currency", sa.String(3)),
    sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    sa.Column("source_turn_id", uuid, nullable=False),
    sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="1"),
    *timestamps(),
    sa.UniqueConstraint("tenant_id", "session_id", "field"),
    sa.CheckConstraint(
        "num_nonnulls(value_integer,value_text,value_text_array,value_numeric) = 1", name="exactly_one_value"
    ),
    sa.CheckConstraint("currency is null or value_numeric is not null", name="currency_requires_numeric"),
    sa.CheckConstraint("confidence between 0 and 1", name="valid_confidence"),
)

requirement_changes = session_table(
    "requirement_changes",
    sa.Column("requirement_id", uuid, nullable=False),
    sa.Column("field", sa.Text, nullable=False),
    sa.Column("old_value", jsonb),
    sa.Column("new_value", jsonb, nullable=False),
    sa.Column("confirmed", sa.Boolean, nullable=False),
    sa.Column("source_turn_id", uuid, nullable=False),
    sa.Column("actor_type", sa.Text, nullable=False),
    sa.Column("actor_id", uuid),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("event_id", uuid, nullable=False, unique=True),
    sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.CheckConstraint(
        "actor_type in ('CUSTOMER','ASSISTANT','HUMAN_AGENT','SYSTEM','WORKLOAD')",
        name="valid_actor_type",
    ),
    sa.CheckConstraint(
        "source in ('CUSTOMER_CONFIRMATION','HUMAN_CORRECTION','WORKFLOW_CONFIRMATION')",
        name="valid_source",
    ),
    sa.ForeignKeyConstraint(
        ["tenant_id", "requirement_id"], ["requirements_current.tenant_id", "requirements_current.id"]
    ),
)
sa.Index(
    "ix_requirement_changes_session_changed_id",
    requirement_changes.c.tenant_id,
    requirement_changes.c.session_id,
    requirement_changes.c.changed_at,
    requirement_changes.c.id,
)
sa.Index(
    "ix_requirement_changes_tenant_requirement",
    requirement_changes.c.tenant_id,
    requirement_changes.c.requirement_id,
)

objections = session_table(
    "objections",
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("detail_ciphertext", BYTEA, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("first_turn_id", uuid, nullable=False),
    sa.Column("latest_turn_id", uuid, nullable=False),
    *timestamps(),
)
sa.Index(
    "ix_objections_tenant_session_status_category",
    objections.c.tenant_id,
    objections.c.session_id,
    objections.c.status,
    objections.c.category,
)
sa.Index(
    "uq_objections_active_category",
    objections.c.tenant_id,
    objections.c.session_id,
    objections.c.category,
    unique=True,
    postgresql_where=objections.c.status == "ACTIVE",
)

session_competitors = session_table(
    "session_competitors",
    sa.Column("normalized_name", sa.Text, nullable=False),
    sa.Column("context_ciphertext", BYTEA),
    *timestamps(),
    sa.UniqueConstraint("tenant_id", "session_id", "normalized_name"),
)

qualification_snapshots = session_table(
    "qualification_snapshots",
    sa.Column("need_score", sa.SmallInteger, nullable=False),
    sa.Column("product_fit_score", sa.SmallInteger, nullable=False),
    sa.Column("deployment_fit_score", sa.SmallInteger, nullable=False),
    sa.Column("timeline_score", sa.SmallInteger, nullable=False),
    sa.Column("authority_score", sa.SmallInteger, nullable=False),
    sa.Column("budget_score", sa.SmallInteger, nullable=False),
    sa.Column("purchase_intent_score", sa.SmallInteger, nullable=False),
    sa.Column("total_score", sa.SmallInteger, nullable=False),
    sa.Column("buying_stage", sa.Text, nullable=False),
    sa.Column("override_action", sa.Text),
    sa.Column("source_turn_id", uuid, nullable=False),
    sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.CheckConstraint("need_score between 0 and 25", name="need_range"),
    sa.CheckConstraint("product_fit_score between 0 and 20", name="product_fit_range"),
    sa.CheckConstraint("deployment_fit_score between 0 and 15", name="deployment_fit_range"),
    sa.CheckConstraint("timeline_score between 0 and 15", name="timeline_range"),
    sa.CheckConstraint("authority_score between 0 and 10", name="authority_range"),
    sa.CheckConstraint("budget_score between 0 and 5", name="budget_range"),
    sa.CheckConstraint("purchase_intent_score between 0 and 10", name="purchase_intent_range"),
    sa.CheckConstraint(
        "total_score = need_score + product_fit_score + deployment_fit_score "
        "+ timeline_score + authority_score + budget_score + purchase_intent_score",
        name="total_is_component_sum",
    ),
)
sa.Index(
    "ix_qualification_session_calculated_id",
    qualification_snapshots.c.tenant_id,
    qualification_snapshots.c.session_id,
    qualification_snapshots.c.calculated_at,
    qualification_snapshots.c.id,
)

tool_calls = session_table(
    "tool_calls",
    sa.Column("turn_id", uuid, nullable=False),
    sa.Column("tool_name", sa.Text, nullable=False),
    sa.Column("tool_version", sa.Text, nullable=False),
    sa.Column("approval_level", sa.Text, nullable=False),
    sa.Column("approval_decision", sa.Text),
    sa.Column("request_schema_version", sa.Integer, nullable=False),
    sa.Column("request_hash", BYTEA, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("timeout_ms", sa.Integer, nullable=False),
    sa.Column("provider_reference_hmac", BYTEA),
    sa.Column("idempotency_key_hmac", BYTEA),
    sa.Column("safe_error_code", sa.Text),
    *timestamps(),
)
sa.Index(
    "ix_tool_calls_session_created_id",
    tool_calls.c.tenant_id,
    tool_calls.c.session_id,
    tool_calls.c.created_at,
    tool_calls.c.id,
)
sa.Index(
    "ix_tool_calls_pending",
    tool_calls.c.updated_at,
    tool_calls.c.id,
    postgresql_where=tool_calls.c.status.in_(["PENDING", "RUNNING", "PENDING_CONFIRMATION"]),
)

tool_results = sa.Table(
    "tool_results",
    metadata,
    *tenant_columns(),
    sa.Column("tool_call_id", uuid, nullable=False),
    sa.Column("result_schema_version", sa.Integer, nullable=False),
    sa.Column("result_hash", BYTEA, nullable=False),
    sa.Column("result_ciphertext", BYTEA, nullable=False),
    sa.Column("confirmation_status", sa.Text, nullable=False),
    sa.Column("provider_timestamp", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.ForeignKeyConstraint(
        ["tenant_id", "tool_call_id"], ["tool_calls.tenant_id", "tool_calls.id"], ondelete="CASCADE"
    ),
    sa.UniqueConstraint("tenant_id", "tool_call_id"),
)

operations = sa.Table(
    "operations",
    metadata,
    *tenant_columns(),
    sa.Column("actor_id", uuid, nullable=False),
    sa.Column("session_id", uuid),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("result_reference", sa.Text),
    sa.Column("safe_error_code", sa.Text),
    sa.Column("safe_error_detail", sa.Text),
    *timestamps(),
    sa.ForeignKeyConstraint(["tenant_id", "actor_id"], ["actors.tenant_id", "actors.id"]),
    sa.ForeignKeyConstraint(["tenant_id", "session_id"], ["sales_sessions.tenant_id", "sales_sessions.id"]),
)
sa.Index(
    "ix_operations_actor_updated_id",
    operations.c.tenant_id,
    operations.c.actor_id,
    operations.c.updated_at,
    operations.c.id,
)
sa.Index(
    "ix_operations_pending",
    operations.c.status,
    operations.c.updated_at,
    operations.c.id,
    postgresql_where=operations.c.status.in_(["PENDING", "RUNNING", "PENDING_CONFIRMATION"]),
)
sa.Index("ix_operations_tenant_session", operations.c.tenant_id, operations.c.session_id)

idempotency_records = sa.Table(
    "idempotency_records",
    metadata,
    *tenant_columns(),
    sa.Column("actor_id", uuid, nullable=False),
    sa.Column("workload", sa.Text, nullable=False),
    sa.Column("method", sa.Text, nullable=False),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("key_hmac", BYTEA, nullable=False),
    sa.Column("request_hash", BYTEA, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("response_status", sa.Integer),
    sa.Column("response_body_ciphertext", BYTEA),
    sa.Column("response_headers", jsonb),
    sa.Column("operation_id", uuid),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    *timestamps(versioned=False),
    sa.UniqueConstraint("tenant_id", "actor_id", "workload", "method", "path", "key_hmac"),
)
sa.Index(
    "ix_idempotency_unexpired",
    idempotency_records.c.expires_at,
    idempotency_records.c.id,
    postgresql_where=idempotency_records.c.status.in_(["STARTED", "COMPLETED"]),
)


def business_record(name: str, *items: SchemaItem) -> sa.Table:
    return session_table(
        name,
        sa.Column("lead_id", uuid),
        *items,
        sa.ForeignKeyConstraint(["tenant_id", "lead_id"], ["leads.tenant_id", "leads.id"]),
        *timestamps(),
    )


meetings = business_record(
    "meetings",
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("timezone", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("provider_confirmation_hmac", BYTEA),
    sa.Column("idempotency_key_hmac", BYTEA, nullable=False),
    sa.Column("safe_error_code", sa.Text),
)
sa.Index(
    "uq_meetings_confirmation",
    meetings.c.tenant_id,
    meetings.c.provider,
    meetings.c.provider_confirmation_hmac,
    unique=True,
    postgresql_where=meetings.c.provider_confirmation_hmac.is_not(None),
)
sa.Index("ix_meetings_tenant_session", meetings.c.tenant_id, meetings.c.session_id)
sa.Index("ix_meetings_tenant_lead", meetings.c.tenant_id, meetings.c.lead_id)

followups = business_record(
    "followups",
    sa.Column("channel", sa.Text, nullable=False),
    sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("provider_reference_hmac", BYTEA),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("safe_error_code", sa.Text),
)
sa.Index(
    "ix_followups_pending",
    followups.c.status,
    followups.c.scheduled_at,
    followups.c.id,
    postgresql_where=followups.c.status.in_(["PENDING", "FAILED_RETRYABLE"]),
)
sa.Index("ix_followups_tenant_session", followups.c.tenant_id, followups.c.session_id)
sa.Index("ix_followups_tenant_lead", followups.c.tenant_id, followups.c.lead_id)

handoffs = session_table(
    "handoffs",
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("assigned_agent_id", uuid),
    sa.Column("context_ciphertext", BYTEA, nullable=False),
    sa.Column("provider_reference_hmac", BYTEA),
    *timestamps(),
)
sa.Index(
    "ix_handoffs_tenant_status_created_id",
    handoffs.c.tenant_id,
    handoffs.c.status,
    handoffs.c.created_at,
    handoffs.c.id,
)
sa.Index("ix_handoffs_tenant_session", handoffs.c.tenant_id, handoffs.c.session_id)

session_outcomes = session_table(
    "session_outcomes",
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("source_reference", sa.Text),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.Column("superseded_at", sa.DateTime(timezone=True)),
)
sa.Index(
    "uq_session_outcomes_active",
    session_outcomes.c.tenant_id,
    session_outcomes.c.session_id,
    unique=True,
    postgresql_where=session_outcomes.c.superseded_at.is_(None),
)

pending_provider_updates = sa.Table(
    "pending_provider_updates",
    metadata,
    *tenant_columns(),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("aggregate_type", sa.Text, nullable=False),
    sa.Column("aggregate_id", uuid, nullable=False),
    sa.Column("payload_ciphertext", BYTEA, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("lease_owner", sa.Text),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("safe_error_code", sa.Text),
    *timestamps(),
)
sa.Index(
    "ix_provider_updates_pending",
    pending_provider_updates.c.next_attempt_at,
    pending_provider_updates.c.id,
    postgresql_where=pending_provider_updates.c.status == "PENDING",
)

domain_events = session_table(
    "domain_events",
    sa.Column("event_id", uuid, nullable=False, unique=True),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("event_version", sa.Integer, nullable=False),
    sa.Column("sequence", sa.BigInteger, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("correlation_id", uuid, nullable=False),
    sa.Column("causation_id", uuid),
    sa.Column("actor_id", uuid),
    sa.Column("actor_type", sa.Text, nullable=False),
    sa.Column("payload", jsonb, nullable=False),
    sa.Column("payload_schema_version", sa.Integer, nullable=False),
    sa.CheckConstraint(
        "actor_type in ('CUSTOMER','ASSISTANT','HUMAN_AGENT','SYSTEM','WORKLOAD')",
        name="valid_actor_type",
    ),
    sa.UniqueConstraint("tenant_id", "session_id", "sequence", "event_type", "event_id"),
)
sa.Index(
    "ix_domain_events_session_occurred_id",
    domain_events.c.tenant_id,
    domain_events.c.session_id,
    domain_events.c.occurred_at,
    domain_events.c.id,
)
sa.Index(
    "uq_domain_events_session_sequence",
    domain_events.c.tenant_id,
    domain_events.c.session_id,
    domain_events.c.sequence,
    unique=True,
)

outbox_messages = sa.Table(
    "outbox_messages",
    metadata,
    *tenant_columns(),
    sa.Column("event_id", uuid, nullable=False),
    sa.Column("destination", sa.Text, nullable=False),
    sa.Column("topic", sa.Text, nullable=False),
    sa.Column("payload", jsonb, nullable=False),
    sa.Column("payload_schema_version", sa.Integer, nullable=False),
    sa.Column("payload_hash", BYTEA, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("lease_owner", sa.Text),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    *timestamps(),
    sa.UniqueConstraint("destination", "event_id"),
)
sa.Index(
    "ix_outbox_claim",
    outbox_messages.c.next_attempt_at,
    outbox_messages.c.id,
    postgresql_where=outbox_messages.c.status == "PENDING",
)

inbox_receipts = sa.Table(
    "inbox_receipts",
    metadata,
    *tenant_columns(),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("message_id", sa.Text, nullable=False),
    sa.Column("message_hash", BYTEA, nullable=False),
    sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.Column("result_reference", sa.Text),
    sa.UniqueConstraint("source", "message_id"),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
    *tenant_columns(),
    sa.Column("actor_id", uuid),
    sa.Column("workload", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", uuid),
    sa.Column("policy_version", sa.Text),
    sa.Column("approval_decision", sa.Text),
    sa.Column("idempotency_key_hmac", BYTEA),
    sa.Column("request_schema_version", sa.Integer),
    sa.Column("result", sa.Text, nullable=False),
    sa.Column("correlation_id", uuid, nullable=False),
    sa.Column("tool_call_id", uuid),
    sa.Column("provider_reference_hmac", BYTEA),
    sa.Column("redacted_metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
)
sa.Index("ix_audit_events_tenant_occurred_id", audit_events.c.tenant_id, audit_events.c.occurred_at, audit_events.c.id)

knowledge_documents = sa.Table(
    "knowledge_documents",
    metadata,
    *tenant_columns(),
    sa.Column("source_uri", sa.Text, nullable=False),
    sa.Column("source_hash", BYTEA, nullable=False),
    sa.Column("domain", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("classification", sa.Text, nullable=False),
    sa.Column("document_version", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("effective_at", sa.DateTime(timezone=True)),
    sa.Column("expires_at", sa.DateTime(timezone=True)),
    *timestamps(),
)
sa.Index(
    "uq_knowledge_document_version",
    knowledge_documents.c.tenant_id,
    knowledge_documents.c.source_hash,
    knowledge_documents.c.document_version,
    unique=True,
)
sa.Index(
    "ix_knowledge_documents_domain_status_updated",
    knowledge_documents.c.tenant_id,
    knowledge_documents.c.domain,
    knowledge_documents.c.status,
    knowledge_documents.c.updated_at,
    knowledge_documents.c.id,
)

knowledge_chunks = sa.Table(
    "knowledge_chunks",
    metadata,
    *tenant_columns(),
    sa.Column("document_id", uuid, nullable=False),
    sa.Column("document_version", sa.Integer, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("approved_text", sa.Text, nullable=False),
    sa.Column("token_count", sa.Integer, nullable=False),
    sa.Column("metadata", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("content_hash", BYTEA, nullable=False),
    *timestamps(versioned=False),
    sa.ForeignKeyConstraint(
        ["tenant_id", "document_id"], ["knowledge_documents.tenant_id", "knowledge_documents.id"], ondelete="CASCADE"
    ),
    sa.UniqueConstraint("document_id", "document_version", "ordinal"),
)
sa.Index("ix_knowledge_chunks_tenant_document", knowledge_chunks.c.tenant_id, knowledge_chunks.c.document_id)

knowledge_embeddings = sa.Table(
    "knowledge_embeddings",
    metadata,
    *tenant_columns(),
    sa.Column("chunk_id", uuid, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("revision", sa.Text, nullable=False),
    sa.Column("dimensions", sa.Integer, nullable=False),
    sa.Column("embedding", Vector(1536), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=utc_now),
    sa.ForeignKeyConstraint(
        ["tenant_id", "chunk_id"], ["knowledge_chunks.tenant_id", "knowledge_chunks.id"], ondelete="CASCADE"
    ),
    sa.UniqueConstraint("chunk_id", "model", "revision"),
    sa.CheckConstraint("dimensions = 1536", name="v1_dimensions"),
)
sa.Index("ix_knowledge_embeddings_tenant_chunk", knowledge_embeddings.c.tenant_id, knowledge_embeddings.c.chunk_id)
sa.Index(
    "ix_knowledge_embeddings_hnsw",
    knowledge_embeddings.c.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)

knowledge_index_versions = sa.Table(
    "knowledge_index_versions",
    metadata,
    sa.Column("id", uuid, primary_key=True),
    sa.Column("tenant_id", uuid),
    sa.Column("domain", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("revision", sa.Text, nullable=False),
    sa.Column("dimensions", sa.Integer, nullable=False),
    sa.Column("chunker_version", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("built_at", sa.DateTime(timezone=True)),
    sa.Column("activated_at", sa.DateTime(timezone=True)),
    sa.Column("retired_at", sa.DateTime(timezone=True)),
    sa.Column("evaluation_reference", sa.Text),
    *timestamps(),
    sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
)
sa.Index(
    "uq_knowledge_index_active",
    knowledge_index_versions.c.tenant_id,
    knowledge_index_versions.c.domain,
    unique=True,
    postgresql_where=knowledge_index_versions.c.status == "ACTIVE",
)

TENANT_TABLE_NAMES = frozenset(table.name for table in metadata.tables.values() if "tenant_id" in table.c)
EXPECTED_TABLE_NAMES = frozenset(metadata.tables)
