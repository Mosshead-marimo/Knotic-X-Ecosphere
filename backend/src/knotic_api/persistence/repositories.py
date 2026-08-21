"""Typed SQLAlchemy Core repositories with mandatory tenant predicates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection, RowMapping

from knotic_api.domain.models import DomainEvent, Message, Objection, Outcome, Requirement, ToolCall
from knotic_api.domain.types import SessionStatus

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
    def replace_confirmed(self, requirement: Requirement, *, event_id: UUID) -> UUID:
        self._require_tenant(requirement.tenant_id)
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
            self.connection.execute(
                schema.requirements_current.insert().values(
                    id=requirement.requirement_id,
                    tenant_id=self.tenant_id,
                    session_id=requirement.session_id,
                    field=requirement.field.value,
                    confirmed_at=requirement.updated_at,
                    source_turn_id=requirement.source_turn_id,
                    version=requirement.version,
                    **values,
                )
            )
            old_value: Any = None
        else:
            if existing["version"] >= requirement.version:
                raise ValueError("requirement version must increase")
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
                    version=requirement.version,
                    **values,
                )
            )
        self.connection.execute(
            schema.requirement_changes.insert().values(
                id=event_id,
                tenant_id=self.tenant_id,
                session_id=requirement.session_id,
                requirement_id=requirement.requirement_id if existing is None else existing["id"],
                field=requirement.field.value,
                old_value=old_value,
                new_value=requirement.model_dump(mode="json")["value"],
                confirmed=True,
                source_turn_id=requirement.source_turn_id,
                event_id=event_id,
                changed_at=requirement.updated_at,
            )
        )
        return requirement.requirement_id if existing is None else existing["id"]

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
        result = self.connection.execute(
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
            )
        )
        if result.rowcount != 1:
            raise ValueError("objection version did not increase")
        return objection.objection_id


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
