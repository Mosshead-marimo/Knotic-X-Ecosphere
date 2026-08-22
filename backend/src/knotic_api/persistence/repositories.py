"""Typed SQLAlchemy Core repositories with mandatory tenant predicates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, RowMapping

from knotic_api.domain.models import DomainEvent, Message, Objection, Outcome, Requirement, SalesState, ToolCall
from knotic_api.domain.types import EventType, RequirementUpdateSource, SessionStatus

from . import schema_v1 as schema


@dataclass(frozen=True, slots=True)
class LeadCreate:
    lead_id: UUID
    name_ciphertext: bytes | None = None
    email_ciphertext: bytes | None = None
    email_hmac: bytes | None = None
    phone_ciphertext: bytes | None = None
    phone_hmac: bytes | None = None
    company_ciphertext: bytes | None = None
    role_ciphertext: bytes | None = None
    crm_status: str = "NEW"


@dataclass(frozen=True, slots=True)
class SessionCreate:
    session_id: UUID
    locale: str
    timezone: str
    lead_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: UUID
    tenant_id: UUID
    status: str
    locale: str
    timezone: str
    lead_id: UUID | None
    version: int
    started_at: datetime
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None
    current_topic: str | None
    current_intent: str | None
    buying_stage: str | None
    qualification_score: int | None
    next_best_action: str | None
    summary_ciphertext: bytes | None
    latest_request_ciphertext: bytes | None
    outcome: str | None
    checkpoint_event_sequence: int
    projection_schema_version: int
    checkpointed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MeetingCreate:
    meeting_id: UUID
    session_id: UUID
    lead_id: UUID | None
    provider: str
    scheduled_at: datetime
    timezone: str
    idempotency_key_hmac: bytes


@dataclass(frozen=True, slots=True)
class FollowupCreate:
    followup_id: UUID
    session_id: UUID
    lead_id: UUID | None
    channel: str
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    record_id: UUID
    actor_id: UUID
    workload: str
    method: str
    path: str
    key_hmac: bytes
    request_hash: bytes
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ToolResultCreate:
    result_id: UUID
    tool_call_id: UUID
    schema_version: int
    result_hash: bytes
    result_ciphertext: bytes
    confirmation_status: str
    provider_timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class RequirementRevisionCommand:
    requirement: Requirement
    event_id: UUID
    correlation_id: UUID
    actor_type: Literal["CUSTOMER", "ASSISTANT", "HUMAN_AGENT", "SYSTEM", "WORKLOAD"]
    actor_id: UUID
    source: RequirementUpdateSource


@dataclass(frozen=True, slots=True)
class RequirementRevision:
    requirement_id: UUID
    event_id: UUID
    previous_value: Any
    current_value: Any
    version: int
    changed: bool


@dataclass(frozen=True, slots=True)
class AuditRecordCreate:
    audit_id: UUID
    actor_id: UUID | None
    workload: str
    action: str
    target_type: str
    target_id: UUID | None
    result: str
    correlation_id: UUID
    redacted_metadata: dict[str, str | int | bool | None]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ErasureOperationCreate:
    operation_id: UUID
    actor_id: UUID
    session_id: UUID


@dataclass(frozen=True, slots=True)
class DurableProjection:
    session: SessionRecord
    lead: RowMapping | None
    requirements: tuple[RowMapping, ...]
    requirement_changes: tuple[RowMapping, ...]
    objections: tuple[RowMapping, ...]
    competitors: tuple[RowMapping, ...]
    qualification: RowMapping | None
    outcome: RowMapping | None
    event_watermark: int


class TenantRepository:
    def __init__(self, connection: Connection, tenant_id: UUID) -> None:
        self.connection = connection
        self.tenant_id = tenant_id

    def _one_or_none(self, statement: sa.Executable) -> RowMapping | None:
        row = self.connection.execute(statement).mappings().one_or_none()
        return row

    def _require_tenant(self, record_tenant_id: UUID) -> None:
        if record_tenant_id != self.tenant_id:
            raise ValueError("record tenant does not match unit-of-work tenant")


class LeadRepository(TenantRepository):
    def create(self, command: LeadCreate) -> UUID:
        self.connection.execute(
            schema.leads.insert().values(
                id=command.lead_id,
                tenant_id=self.tenant_id,
                name_ciphertext=command.name_ciphertext,
                email_ciphertext=command.email_ciphertext,
                email_hmac=command.email_hmac,
                phone_ciphertext=command.phone_ciphertext,
                phone_hmac=command.phone_hmac,
                company_ciphertext=command.company_ciphertext,
                role_ciphertext=command.role_ciphertext,
                crm_status=command.crm_status,
            )
        )
        return command.lead_id

    def get(self, lead_id: UUID) -> RowMapping | None:
        return self._one_or_none(
            sa.select(schema.leads).where(
                schema.leads.c.tenant_id == self.tenant_id,
                schema.leads.c.id == lead_id,
            )
        )


class SessionRepository(TenantRepository):
    def create(self, command: SessionCreate) -> SessionRecord:
        row = (
            self.connection.execute(
                schema.sales_sessions.insert()
                .values(
                    id=command.session_id,
                    tenant_id=self.tenant_id,
                    lead_id=command.lead_id,
                    status=SessionStatus.CREATED.value,
                    locale=command.locale,
                    timezone=command.timezone,
                )
                .returning(schema.sales_sessions)
            )
            .mappings()
            .one()
        )
        return self._record(row)

    def get(self, session_id: UUID, *, for_update: bool = False) -> SessionRecord | None:
        statement = sa.select(schema.sales_sessions).where(
            schema.sales_sessions.c.tenant_id == self.tenant_id,
            schema.sales_sessions.c.id == session_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._one_or_none(statement)
        return None if row is None else self._record(row)

    def transition(
        self,
        session_id: UUID,
        *,
        expected_version: int,
        expected_status: SessionStatus,
        target_status: SessionStatus,
        at: datetime,
        outcome: str | None = None,
    ) -> SessionRecord | None:
        allowed = {
            SessionStatus.CREATED: {SessionStatus.ACTIVE, SessionStatus.ENDING, SessionStatus.FAILED},
            SessionStatus.ACTIVE: {SessionStatus.ENDING, SessionStatus.FAILED},
            SessionStatus.ENDING: {SessionStatus.ENDED, SessionStatus.FAILED},
            SessionStatus.ENDED: set(),
            SessionStatus.FAILED: set(),
        }
        if target_status not in allowed[expected_status]:
            raise ValueError(f"invalid session transition: {expected_status.value} -> {target_status.value}")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("transition timestamp must be timezone-aware")
        terminal = target_status in {SessionStatus.ENDED, SessionStatus.FAILED}
        if outcome is not None and not terminal:
            raise ValueError("outcome may be assigned only on a terminal transition")
        values: dict[str, Any] = {
            "status": target_status.value,
            "version": expected_version + 1,
            "updated_at": at,
            "ended_at": at if terminal else None,
        }
        if outcome is not None:
            values["outcome"] = outcome
        row = (
            self.connection.execute(
                schema.sales_sessions.update()
                .where(
                    schema.sales_sessions.c.tenant_id == self.tenant_id,
                    schema.sales_sessions.c.id == session_id,
                    schema.sales_sessions.c.version == expected_version,
                    schema.sales_sessions.c.status == expected_status.value,
                )
                .values(**values)
                .returning(schema.sales_sessions)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._record(row)

    @staticmethod
    def _record(row: RowMapping) -> SessionRecord:
        return SessionRecord(
            session_id=row["id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            locale=row["locale"],
            timezone=row["timezone"],
            lead_id=row["lead_id"],
            version=row["version"],
            started_at=row["started_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            ended_at=row["ended_at"],
            current_topic=row["current_topic"],
            current_intent=row["current_intent"],
            buying_stage=row["buying_stage"],
            qualification_score=row["qualification_score"],
            next_best_action=row["next_best_action"],
            summary_ciphertext=row["summary_ciphertext"],
            latest_request_ciphertext=row["latest_request_ciphertext"],
            outcome=row["outcome"],
            checkpoint_event_sequence=row["checkpoint_event_sequence"],
            projection_schema_version=row["projection_schema_version"],
            checkpointed_at=row["checkpointed_at"],
        )


class MessageRepository(TenantRepository):
    def append(self, message: Message, *, content_ciphertext: bytes) -> UUID:
        self._require_tenant(message.tenant_id)
        self.connection.execute(
            schema.messages.insert().values(
                id=message.message_id,
                tenant_id=self.tenant_id,
                session_id=message.session_id,
                turn_id=message.turn_id,
                response_id=message.response_id,
                sequence=message.sequence,
                speaker=message.speaker.value,
                source=message.source.value,
                content_ciphertext=content_ciphertext,
                locale=message.locale,
                interrupted=message.interrupted,
                created_at=message.created_at,
                updated_at=message.created_at,
            )
        )
        return message.message_id

    def list_for_session(self, session_id: UUID, *, limit: int = 100) -> list[RowMapping]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return list(
            self.connection.execute(
                sa.select(schema.messages)
                .where(
                    schema.messages.c.tenant_id == self.tenant_id,
                    schema.messages.c.session_id == session_id,
                )
                .order_by(schema.messages.c.created_at, schema.messages.c.id)
                .limit(limit)
            ).mappings()
        )


class RequirementRepository(TenantRepository):
    def revise_confirmed(self, command: RequirementRevisionCommand) -> RequirementRevision:
        requirement = command.requirement
        self._require_tenant(requirement.tenant_id)
        for name, identifier in {
            "event_id": command.event_id,
            "correlation_id": command.correlation_id,
            "actor_id": command.actor_id,
        }.items():
            if identifier.version != 7:
                raise ValueError(f"{name} must be UUIDv7")
        if command.actor_type not in {"CUSTOMER", "ASSISTANT", "HUMAN_AGENT", "SYSTEM", "WORKLOAD"}:
            raise ValueError("invalid requirement revision actor type")
        if not requirement.confirmed:
            raise ValueError("only confirmed requirements may become durable current values")
        self.connection.execute(
            sa.select(schema.sales_sessions.c.id)
            .where(
                schema.sales_sessions.c.tenant_id == self.tenant_id,
                schema.sales_sessions.c.id == requirement.session_id,
            )
            .with_for_update()
        ).scalar_one()
        new_value = requirement.model_dump(mode="json")["value"]
        repeated = (
            self.connection.execute(
                sa.select(schema.requirement_changes)
                .where(
                    schema.requirement_changes.c.tenant_id == self.tenant_id,
                    schema.requirement_changes.c.session_id == requirement.session_id,
                    schema.requirement_changes.c.field == requirement.field.value,
                    schema.requirement_changes.c.source_turn_id == requirement.source_turn_id,
                )
                .order_by(schema.requirement_changes.c.changed_at.desc(), schema.requirement_changes.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if repeated is not None:
            if repeated["new_value"] != new_value:
                raise ValueError("one source turn cannot confirm conflicting requirement values")
            current = self._current_row(requirement)
            return RequirementRevision(
                requirement_id=repeated["requirement_id"],
                event_id=repeated["event_id"],
                previous_value=repeated["old_value"],
                current_value=repeated["new_value"],
                version=current["version"],
                changed=False,
            )
        existing = (
            self.connection.execute(
                sa.select(schema.requirements_current)
                .where(
                    schema.requirements_current.c.tenant_id == self.tenant_id,
                    schema.requirements_current.c.session_id == requirement.session_id,
                    schema.requirements_current.c.field == requirement.field.value,
                )
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        values = self._typed_values(requirement)
        if existing is None:
            if requirement.version != 1:
                raise ValueError("the first confirmed requirement version must be 1")
            self.connection.execute(
                schema.requirements_current.insert().values(
                    id=requirement.requirement_id,
                    tenant_id=self.tenant_id,
                    session_id=requirement.session_id,
                    field=requirement.field.value,
                    confirmed_at=requirement.updated_at,
                    source_turn_id=requirement.source_turn_id,
                    confidence=requirement.confidence,
                    version=requirement.version,
                    **values,
                )
            )
            old_value: Any = None
        else:
            if requirement.version != existing["version"] + 1:
                raise ValueError("requirement version must increase exactly once")
            old_value = self._current_value(existing)
            self.connection.execute(
                schema.requirements_current.update()
                .where(
                    schema.requirements_current.c.tenant_id == self.tenant_id,
                    schema.requirements_current.c.id == existing["id"],
                )
                .values(
                    confirmed_at=requirement.updated_at,
                    source_turn_id=requirement.source_turn_id,
                    confidence=requirement.confidence,
                    version=requirement.version,
                    **values,
                )
            )
        self.connection.execute(
            schema.requirement_changes.insert().values(
                id=command.event_id,
                tenant_id=self.tenant_id,
                session_id=requirement.session_id,
                requirement_id=requirement.requirement_id if existing is None else existing["id"],
                field=requirement.field.value,
                old_value=old_value,
                new_value=new_value,
                confirmed=True,
                source_turn_id=requirement.source_turn_id,
                event_id=command.event_id,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                source=command.source.value,
                changed_at=requirement.updated_at,
            )
        )
        requirement_id = requirement.requirement_id if existing is None else existing["id"]
        event = DomainEvent(
            event_id=command.event_id,
            event_type=EventType.REQUIREMENT_UPDATED,
            occurred_at=requirement.updated_at,
            tenant_id=self.tenant_id,
            session_id=requirement.session_id,
            sequence=EventRepository(self.connection, self.tenant_id).next_sequence(requirement.session_id),
            correlation_id=command.correlation_id,
            causation_id=requirement.source_turn_id,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            payload={
                "field": requirement.field.value,
                "old_value": old_value,
                "new_value": new_value,
                "confirmed": True,
                "source": command.source.value,
                "source_turn_id": str(requirement.source_turn_id),
            },
        )
        EventRepository(self.connection, self.tenant_id).append(event)
        self.connection.execute(
            schema.sales_sessions.update()
            .where(
                schema.sales_sessions.c.tenant_id == self.tenant_id,
                schema.sales_sessions.c.id == requirement.session_id,
            )
            .values(
                version=schema.sales_sessions.c.version + 1,
                checkpoint_event_sequence=event.sequence,
                projection_schema_version=1,
                checkpointed_at=requirement.updated_at,
                updated_at=requirement.updated_at,
            )
        )
        return RequirementRevision(
            requirement_id=requirement_id,
            event_id=command.event_id,
            previous_value=old_value,
            current_value=new_value,
            version=requirement.version,
            changed=True,
        )

    def _current_row(self, requirement: Requirement) -> RowMapping:
        return (
            self.connection.execute(
                sa.select(schema.requirements_current).where(
                    schema.requirements_current.c.tenant_id == self.tenant_id,
                    schema.requirements_current.c.session_id == requirement.session_id,
                    schema.requirements_current.c.field == requirement.field.value,
                )
            )
            .mappings()
            .one()
        )

    @staticmethod
    def _typed_values(requirement: Requirement) -> dict[str, Any]:
        values: dict[str, Any] = {
            "value_integer": None,
            "value_text": None,
            "value_text_array": None,
            "value_numeric": None,
            "currency": requirement.currency,
        }
        value = requirement.value
        if type(value) is int:
            values["value_integer"] = value
        elif isinstance(value, Decimal):
            values["value_numeric"] = value
        elif isinstance(value, tuple):
            values["value_text_array"] = list(value)
        else:
            values["value_text"] = value
        return values

    @staticmethod
    def _current_value(row: RowMapping) -> Any:
        for name in ("value_integer", "value_text", "value_text_array", "value_numeric"):
            if row[name] is not None:
                value = row[name]
                return str(value) if isinstance(value, Decimal) else value
        raise RuntimeError("stored requirement has no typed value")


class ObjectionRepository(TenantRepository):
    def save(self, objection: Objection, *, detail_ciphertext: bytes) -> UUID:
        self._require_tenant(objection.tenant_id)
        statement = postgres_insert(schema.objections).values(
            id=objection.objection_id,
            tenant_id=self.tenant_id,
            session_id=objection.session_id,
            category=objection.category.value,
            detail_ciphertext=detail_ciphertext,
            status=objection.status.value,
            first_turn_id=objection.first_turn_id,
            latest_turn_id=objection.latest_turn_id,
            version=objection.version,
        )
        saved_id = self.connection.execute(
            statement.on_conflict_do_update(
                index_elements=[schema.objections.c.id],
                set_={
                    "detail_ciphertext": statement.excluded.detail_ciphertext,
                    "status": statement.excluded.status,
                    "latest_turn_id": statement.excluded.latest_turn_id,
                    "version": statement.excluded.version,
                    "updated_at": schema.utc_now,
                },
                where=(schema.objections.c.tenant_id == self.tenant_id)
                & (schema.objections.c.version < statement.excluded.version),
            ).returning(schema.objections.c.id)
        ).scalar_one_or_none()
        if saved_id is None:
            raise ValueError("objection version did not increase")
        return cast(UUID, saved_id)


class MeetingRepository(TenantRepository):
    def create(self, command: MeetingCreate) -> UUID:
        self.connection.execute(
            schema.meetings.insert().values(
                id=command.meeting_id,
                tenant_id=self.tenant_id,
                session_id=command.session_id,
                lead_id=command.lead_id,
                provider=command.provider,
                scheduled_at=command.scheduled_at,
                timezone=command.timezone,
                status="PENDING",
                idempotency_key_hmac=command.idempotency_key_hmac,
            )
        )
        return command.meeting_id


class FollowupRepository(TenantRepository):
    def create(self, command: FollowupCreate) -> UUID:
        self.connection.execute(
            schema.followups.insert().values(
                id=command.followup_id,
                tenant_id=self.tenant_id,
                session_id=command.session_id,
                lead_id=command.lead_id,
                channel=command.channel,
                scheduled_at=command.scheduled_at,
                status="PENDING",
            )
        )
        return command.followup_id


class ToolRepository(TenantRepository):
    def append_call(self, tool_call: ToolCall, *, request_hash: bytes) -> UUID:
        self._require_tenant(tool_call.tenant_id)
        self.connection.execute(
            schema.tool_calls.insert().values(
                id=tool_call.tool_call_id,
                tenant_id=self.tenant_id,
                session_id=tool_call.session_id,
                turn_id=tool_call.turn_id,
                tool_name=tool_call.logical_tool,
                tool_version=str(tool_call.tool_version),
                approval_level="NONE",
                request_schema_version=tool_call.schema_version,
                request_hash=request_hash,
                status=tool_call.status.value,
                attempt_count=tool_call.attempt_count,
                timeout_ms=tool_call.timeout_ms,
                idempotency_key_hmac=(
                    bytes.fromhex(tool_call.idempotency_key_hash) if tool_call.idempotency_key_hash else None
                ),
                safe_error_code=tool_call.safe_error_code,
                created_at=tool_call.created_at,
                updated_at=tool_call.updated_at,
                version=1,
            )
        )
        return tool_call.tool_call_id

    def append_result(self, result: ToolResultCreate) -> UUID:
        self.connection.execute(
            schema.tool_results.insert().values(
                id=result.result_id,
                tenant_id=self.tenant_id,
                tool_call_id=result.tool_call_id,
                result_schema_version=result.schema_version,
                result_hash=result.result_hash,
                result_ciphertext=result.result_ciphertext,
                confirmation_status=result.confirmation_status,
                provider_timestamp=result.provider_timestamp,
            )
        )
        return result.result_id


class OutcomeRepository(TenantRepository):
    def assign(self, outcome: Outcome) -> UUID:
        self._require_tenant(outcome.tenant_id)
        self.connection.execute(
            schema.session_outcomes.insert().values(
                id=outcome.outcome_id,
                tenant_id=self.tenant_id,
                session_id=outcome.session_id,
                outcome=outcome.outcome.value,
                source=outcome.source,
                source_reference=outcome.source_reference,
                assigned_at=outcome.assigned_at,
            )
        )
        return outcome.outcome_id


class EventRepository(TenantRepository):
    def next_sequence(self, session_id: UUID) -> int:
        latest = self.connection.scalar(
            sa.select(sa.func.coalesce(sa.func.max(schema.domain_events.c.sequence), 0)).where(
                schema.domain_events.c.tenant_id == self.tenant_id,
                schema.domain_events.c.session_id == session_id,
            )
        )
        if latest is None:
            raise RuntimeError("event sequence query returned no value")
        return int(latest) + 1

    def append(self, event: DomainEvent) -> UUID:
        self._require_tenant(event.tenant_id)
        self.connection.execute(
            schema.domain_events.insert().values(
                id=event.event_id,
                tenant_id=self.tenant_id,
                session_id=event.session_id,
                event_id=event.event_id,
                event_type=event.event_type.value,
                event_version=event.event_version,
                sequence=event.sequence,
                occurred_at=event.occurred_at,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                actor_id=event.actor_id,
                actor_type=event.actor_type,
                payload=event.payload,
                payload_schema_version=event.schema_version,
            )
        )
        return event.event_id

    def list_for_session(self, session_id: UUID, *, after_sequence: int = 0, limit: int = 100) -> list[RowMapping]:
        if after_sequence < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid event page boundary")
        return list(
            self.connection.execute(
                sa.select(schema.domain_events)
                .where(
                    schema.domain_events.c.tenant_id == self.tenant_id,
                    schema.domain_events.c.session_id == session_id,
                    schema.domain_events.c.sequence > after_sequence,
                )
                .order_by(schema.domain_events.c.sequence, schema.domain_events.c.id)
                .limit(limit)
            ).mappings()
        )


class ProjectionRepository(TenantRepository):
    """Reads one consistent durable aggregate for active-state reconstruction."""

    def load(self, session_id: UUID) -> DurableProjection | None:
        session = SessionRepository(self.connection, self.tenant_id).get(session_id, for_update=True)
        if session is None:
            return None
        lead = None if session.lead_id is None else LeadRepository(self.connection, self.tenant_id).get(session.lead_id)
        requirements = tuple(
            self.connection.execute(
                sa.select(schema.requirements_current)
                .where(
                    schema.requirements_current.c.tenant_id == self.tenant_id,
                    schema.requirements_current.c.session_id == session_id,
                )
                .order_by(schema.requirements_current.c.field, schema.requirements_current.c.id)
            ).mappings()
        )
        requirement_changes = tuple(
            self.connection.execute(
                sa.select(schema.requirement_changes)
                .where(
                    schema.requirement_changes.c.tenant_id == self.tenant_id,
                    schema.requirement_changes.c.session_id == session_id,
                )
                .order_by(schema.requirement_changes.c.changed_at, schema.requirement_changes.c.id)
            ).mappings()
        )
        objections = tuple(
            self.connection.execute(
                sa.select(schema.objections)
                .where(
                    schema.objections.c.tenant_id == self.tenant_id,
                    schema.objections.c.session_id == session_id,
                )
                .order_by(schema.objections.c.created_at, schema.objections.c.id)
            ).mappings()
        )
        competitors = tuple(
            self.connection.execute(
                sa.select(schema.session_competitors)
                .where(
                    schema.session_competitors.c.tenant_id == self.tenant_id,
                    schema.session_competitors.c.session_id == session_id,
                )
                .order_by(schema.session_competitors.c.created_at, schema.session_competitors.c.id)
            ).mappings()
        )
        qualification = (
            self.connection.execute(
                sa.select(schema.qualification_snapshots)
                .where(
                    schema.qualification_snapshots.c.tenant_id == self.tenant_id,
                    schema.qualification_snapshots.c.session_id == session_id,
                )
                .order_by(
                    schema.qualification_snapshots.c.calculated_at.desc(), schema.qualification_snapshots.c.id.desc()
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        outcome = (
            self.connection.execute(
                sa.select(schema.session_outcomes)
                .where(
                    schema.session_outcomes.c.tenant_id == self.tenant_id,
                    schema.session_outcomes.c.session_id == session_id,
                    schema.session_outcomes.c.superseded_at.is_(None),
                )
                .order_by(schema.session_outcomes.c.assigned_at.desc(), schema.session_outcomes.c.id.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        watermark = self.connection.scalar(
            sa.select(sa.func.coalesce(sa.func.max(schema.domain_events.c.sequence), 0)).where(
                schema.domain_events.c.tenant_id == self.tenant_id,
                schema.domain_events.c.session_id == session_id,
            )
        )
        if watermark is None:
            raise RuntimeError("event watermark query returned no value")
        return DurableProjection(
            session=session,
            lead=lead,
            requirements=requirements,
            requirement_changes=requirement_changes,
            objections=objections,
            competitors=competitors,
            qualification=qualification,
            outcome=outcome,
            event_watermark=int(watermark),
        )

    def checkpoint(self, state: SalesState, *, expected_version: int, event_watermark: int) -> bool:
        self._require_tenant(state.tenant_id)
        if state.version not in {expected_version, expected_version + 1}:
            raise ValueError("checkpoint state must match or immediately follow the durable version")
        if event_watermark < 0:
            raise ValueError("event watermark must not be negative")
        durable_watermark = self.connection.scalar(
            sa.select(sa.func.coalesce(sa.func.max(schema.domain_events.c.sequence), 0)).where(
                schema.domain_events.c.tenant_id == self.tenant_id,
                schema.domain_events.c.session_id == state.session_id,
            )
        )
        if durable_watermark is None or event_watermark > int(durable_watermark):
            raise ValueError("checkpoint cannot advance beyond durable event history")
        result = self.connection.execute(
            schema.sales_sessions.update()
            .where(
                schema.sales_sessions.c.tenant_id == self.tenant_id,
                schema.sales_sessions.c.id == state.session_id,
                schema.sales_sessions.c.version == expected_version,
                schema.sales_sessions.c.status == state.status.value,
            )
            .values(
                version=state.version,
                current_topic=state.current_topic,
                current_intent=state.current_intent,
                buying_stage=state.buying_stage.value,
                qualification_score=None if state.qualification is None else state.qualification.total_score,
                next_best_action=state.next_best_action.value,
                outcome=None if state.outcome is None else state.outcome.outcome.value,
                checkpoint_event_sequence=event_watermark,
                projection_schema_version=state.schema_version,
                checkpointed_at=state.updated_at,
                updated_at=state.updated_at,
            )
        )
        return result.rowcount == 1


class PrivacyRepository(TenantRepository):
    """Tenant-scoped audit, export, retention, and erasure primitives."""

    def append_audit(self, record: AuditRecordCreate) -> UUID:
        self.connection.execute(
            schema.audit_events.insert().values(
                id=record.audit_id,
                tenant_id=self.tenant_id,
                actor_id=record.actor_id,
                workload=record.workload,
                action=record.action,
                target_type=record.target_type,
                target_id=record.target_id,
                result=record.result,
                correlation_id=record.correlation_id,
                redacted_metadata=record.redacted_metadata,
                occurred_at=record.occurred_at,
            )
        )
        return record.audit_id

    def request_erasure(self, command: ErasureOperationCreate) -> UUID:
        session = SessionRepository(self.connection, self.tenant_id).get(command.session_id, for_update=True)
        if session is None:
            raise ValueError("session was not found")
        existing = self.connection.scalar(
            sa.select(schema.operations.c.id).where(
                schema.operations.c.tenant_id == self.tenant_id,
                schema.operations.c.session_id == command.session_id,
                schema.operations.c.kind == "SESSION_ERASURE",
                schema.operations.c.status.in_(["PENDING", "PENDING_CONFIRMATION", "RUNNING"]),
            )
        )
        if existing is not None:
            return cast(UUID, existing)
        self.connection.execute(
            schema.operations.insert().values(
                id=command.operation_id,
                tenant_id=self.tenant_id,
                actor_id=command.actor_id,
                session_id=command.session_id,
                kind="SESSION_ERASURE",
                status="PENDING",
            )
        )
        return command.operation_id

    def get_operation(self, operation_id: UUID, *, for_update: bool = False) -> RowMapping | None:
        statement = sa.select(schema.operations).where(
            schema.operations.c.tenant_id == self.tenant_id,
            schema.operations.c.id == operation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._one_or_none(statement)

    def processing_blocked(self, session_id: UUID) -> bool:
        blocked = self.connection.scalar(
            sa.select(
                sa.exists().where(
                    schema.operations.c.tenant_id == self.tenant_id,
                    schema.operations.c.session_id == session_id,
                    schema.operations.c.kind.in_(["SESSION_ERASURE", "LEGAL_HOLD"]),
                    schema.operations.c.status.in_(["PENDING", "PENDING_CONFIRMATION", "RUNNING", "ACTIVE"]),
                )
            )
        )
        return bool(blocked)

    def has_legal_hold(self, session_id: UUID) -> bool:
        held = self.connection.scalar(
            sa.select(
                sa.exists().where(
                    schema.operations.c.tenant_id == self.tenant_id,
                    schema.operations.c.session_id == session_id,
                    schema.operations.c.kind == "LEGAL_HOLD",
                    schema.operations.c.status == "ACTIVE",
                )
            )
        )
        return bool(held)

    def requires_provider_unlink(self, session_id: UUID) -> bool:
        link = self.connection.scalar(
            sa.select(schema.provider_links.c.id)
            .select_from(
                schema.sales_sessions.join(
                    schema.provider_links,
                    (schema.provider_links.c.tenant_id == schema.sales_sessions.c.tenant_id)
                    & (schema.provider_links.c.lead_id == schema.sales_sessions.c.lead_id),
                )
            )
            .where(
                schema.sales_sessions.c.tenant_id == self.tenant_id,
                schema.sales_sessions.c.id == session_id,
            )
            .limit(1)
        )
        return link is not None

    def mark_provider_confirmation_pending(self, operation_id: UUID) -> None:
        self.connection.execute(
            schema.operations.update()
            .where(
                schema.operations.c.tenant_id == self.tenant_id,
                schema.operations.c.id == operation_id,
                schema.operations.c.kind == "SESSION_ERASURE",
            )
            .values(status="PENDING_CONFIRMATION", updated_at=schema.utc_now)
        )

    def minimize_session(self, session_id: UUID) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in (
            schema.messages,
            schema.requirement_changes,
            schema.requirements_current,
            schema.objections,
            schema.session_competitors,
            schema.qualification_snapshots,
        ):
            result = self.connection.execute(
                table.delete().where(table.c.tenant_id == self.tenant_id, table.c.session_id == session_id)
            )
            counts[table.name] = max(0, result.rowcount or 0)
        self.connection.execute(
            schema.sales_sessions.update()
            .where(
                schema.sales_sessions.c.tenant_id == self.tenant_id,
                schema.sales_sessions.c.id == session_id,
            )
            .values(
                current_topic=None,
                current_intent=None,
                summary_ciphertext=None,
                latest_request_ciphertext=None,
                updated_at=schema.utc_now,
            )
        )
        return counts

    def erase_session(self, session_id: UUID, *, operation_id: UUID | None = None) -> dict[str, int]:
        session = SessionRepository(self.connection, self.tenant_id).get(session_id, for_update=True)
        if session is None:
            raise ValueError("session was not found")
        if self.has_legal_hold(session_id):
            raise ValueError("session is protected by an active legal hold")
        counts: dict[str, int] = {}
        tool_call_ids = sa.select(schema.tool_calls.c.id).where(
            schema.tool_calls.c.tenant_id == self.tenant_id,
            schema.tool_calls.c.session_id == session_id,
        )
        counts["tool_results"] = max(
            0,
            self.connection.execute(
                schema.tool_results.delete().where(
                    schema.tool_results.c.tenant_id == self.tenant_id,
                    schema.tool_results.c.tool_call_id.in_(tool_call_ids),
                )
            ).rowcount
            or 0,
        )
        for table in (
            schema.calls,
            schema.messages,
            schema.requirement_changes,
            schema.requirements_current,
            schema.objections,
            schema.session_competitors,
            schema.qualification_snapshots,
            schema.tool_calls,
            schema.meetings,
            schema.followups,
            schema.handoffs,
            schema.session_outcomes,
            schema.domain_events,
        ):
            result = self.connection.execute(
                table.delete().where(table.c.tenant_id == self.tenant_id, table.c.session_id == session_id)
            )
            counts[table.name] = max(0, result.rowcount or 0)
        self.connection.execute(
            schema.operations.update()
            .where(
                schema.operations.c.tenant_id == self.tenant_id,
                schema.operations.c.session_id == session_id,
            )
            .values(session_id=None, updated_at=schema.utc_now)
        )
        if operation_id is not None:
            self.connection.execute(
                schema.operations.update()
                .where(
                    schema.operations.c.tenant_id == self.tenant_id,
                    schema.operations.c.id == operation_id,
                )
                .values(status="COMPLETED", result_reference="erased", updated_at=schema.utc_now)
            )
        deleted = self.connection.execute(
            schema.sales_sessions.delete().where(
                schema.sales_sessions.c.tenant_id == self.tenant_id,
                schema.sales_sessions.c.id == session_id,
            )
        )
        counts["sales_sessions"] = max(0, deleted.rowcount or 0)
        return counts

    def retention_candidates(self, *, ended_before: datetime, action: str, limit: int) -> tuple[UUID, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("retention batch limit must be between 1 and 1000")
        held = sa.exists().where(
            schema.operations.c.tenant_id == self.tenant_id,
            schema.operations.c.session_id == schema.sales_sessions.c.id,
            schema.operations.c.kind == "LEGAL_HOLD",
            schema.operations.c.status == "ACTIVE",
        )
        processed = sa.exists().where(
            schema.audit_events.c.tenant_id == self.tenant_id,
            schema.audit_events.c.target_id == schema.sales_sessions.c.id,
            schema.audit_events.c.action == action,
            schema.audit_events.c.result == "SUCCEEDED",
        )
        return tuple(
            self.connection.scalars(
                sa.select(schema.sales_sessions.c.id)
                .where(
                    schema.sales_sessions.c.tenant_id == self.tenant_id,
                    schema.sales_sessions.c.ended_at.is_not(None),
                    schema.sales_sessions.c.ended_at < ended_before,
                    ~held,
                    ~processed,
                )
                .order_by(schema.sales_sessions.c.ended_at, schema.sales_sessions.c.id)
                .limit(limit)
            )
        )

    def purge_housekeeping(self, *, now: datetime, operation_cutoff: datetime) -> tuple[int, int]:
        idempotency = self.connection.execute(
            schema.idempotency_records.delete().where(
                schema.idempotency_records.c.tenant_id == self.tenant_id,
                schema.idempotency_records.c.expires_at < now,
            )
        ).rowcount
        operations = self.connection.execute(
            schema.operations.update()
            .where(
                schema.operations.c.tenant_id == self.tenant_id,
                schema.operations.c.status.in_(["COMPLETED", "FAILED", "CANCELLED"]),
                schema.operations.c.updated_at < operation_cutoff,
            )
            .values(result_reference=None, safe_error_detail=None, updated_at=schema.utc_now)
        ).rowcount
        return max(0, idempotency or 0), max(0, operations or 0)


class IdempotencyRepository(TenantRepository):
    def reserve(self, reservation: IdempotencyReservation) -> bool:
        statement = (
            postgres_insert(schema.idempotency_records)
            .values(
                id=reservation.record_id,
                tenant_id=self.tenant_id,
                actor_id=reservation.actor_id,
                workload=reservation.workload,
                method=reservation.method,
                path=reservation.path,
                key_hmac=reservation.key_hmac,
                request_hash=reservation.request_hash,
                status="STARTED",
                expires_at=reservation.expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    schema.idempotency_records.c.tenant_id,
                    schema.idempotency_records.c.actor_id,
                    schema.idempotency_records.c.workload,
                    schema.idempotency_records.c.method,
                    schema.idempotency_records.c.path,
                    schema.idempotency_records.c.key_hmac,
                ]
            )
            .returning(schema.idempotency_records.c.id)
        )
        return self.connection.execute(statement).scalar_one_or_none() is not None

    def get(self, reservation: IdempotencyReservation, *, for_update: bool = False) -> RowMapping | None:
        statement = sa.select(schema.idempotency_records).where(
            schema.idempotency_records.c.tenant_id == self.tenant_id,
            schema.idempotency_records.c.actor_id == reservation.actor_id,
            schema.idempotency_records.c.workload == reservation.workload,
            schema.idempotency_records.c.method == reservation.method,
            schema.idempotency_records.c.path == reservation.path,
            schema.idempotency_records.c.key_hmac == reservation.key_hmac,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._one_or_none(statement)

    def complete(
        self,
        record_id: UUID,
        *,
        response_status: int,
        response_body_ciphertext: bytes,
        response_headers: dict[str, str],
    ) -> bool:
        result = self.connection.execute(
            schema.idempotency_records.update()
            .where(
                schema.idempotency_records.c.tenant_id == self.tenant_id,
                schema.idempotency_records.c.id == record_id,
                schema.idempotency_records.c.status == "STARTED",
            )
            .values(
                status="COMPLETED",
                response_status=response_status,
                response_body_ciphertext=response_body_ciphertext,
                response_headers=response_headers,
                updated_at=schema.utc_now,
            )
        )
        return result.rowcount == 1
