"""Audited tenant data export, erasure, and retention workflows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import Engine

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.domain.models import SalesState
from knotic_api.persistence.active_state import RedisSalesStateRepository
from knotic_api.persistence.hydration import SalesStateHydrator
from knotic_api.persistence.repositories import AuditRecordCreate, ErasureOperationCreate
from knotic_api.persistence.unit_of_work import UnitOfWork

_AUDIT_METADATA_KEYS = frozenset(
    {
        "deleted_rows",
        "records",
        "reason",
        "provider_unlink_required",
        "policy_version",
        "retention_class",
        "schema_version",
    }
)


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(min_length=1, max_length=64)
    session_content_days: int = Field(default=365, ge=30, le=3_650)
    business_record_days: int = Field(default=400, ge=30, le=3_650)
    event_days: int = Field(default=400, ge=30, le=3_650)
    operation_days: int = Field(default=30, ge=1, le=365)
    idempotency_hours: int = Field(default=24, ge=24, le=720)
    batch_size: int = Field(default=100, ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_order(self) -> RetentionPolicy:
        if self.business_record_days < self.session_content_days or self.event_days < self.session_content_days:
            raise ValueError("business and event retention cannot be shorter than session content retention")
        return self


class SessionDataExport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    generated_at: AwareDatetime
    tenant_id: UUID
    session_id: UUID
    state: SalesState


@dataclass(frozen=True, slots=True)
class RetentionReport:
    minimized_sessions: int
    purged_sessions: int
    idempotency_records: int
    operations_sanitized: int


class DataLifecycleService:
    """Coordinates cache eviction with short, auditable database transactions."""

    def __init__(
        self,
        engine: Engine,
        active_states: RedisSalesStateRepository,
        hydrator: SalesStateHydrator,
        *,
        policy: RetentionPolicy,
    ) -> None:
        self._engine = engine
        self._active_states = active_states
        self._hydrator = hydrator
        self._policy = policy

    def export_session(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        correlation_id: UUID,
        at: datetime | None = None,
    ) -> SessionDataExport:
        generated_at = at or datetime.now(UTC)
        hydrated = self._hydrator.load(tenant_id, session_id)
        export = SessionDataExport(
            generated_at=generated_at,
            tenant_id=tenant_id,
            session_id=session_id,
            state=hydrated.envelope.state,
        )
        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            work.privacy.append_audit(
                self._audit(
                    actor_id=actor_id,
                    action="SESSION_EXPORTED",
                    target_id=session_id,
                    result="SUCCEEDED",
                    correlation_id=correlation_id,
                    at=generated_at,
                    metadata={"schema_version": export.schema_version, "records": 1},
                )
            )
        return export

    def request_session_erasure(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        session_id: UUID,
        correlation_id: UUID,
        at: datetime | None = None,
    ) -> UUID:
        requested_at = at or datetime.now(UTC)
        self._active_states.block_processing(tenant_id, session_id)
        operation_id = new_uuid7()
        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            operation_id = work.privacy.request_erasure(
                ErasureOperationCreate(
                    operation_id=operation_id,
                    actor_id=actor_id,
                    session_id=session_id,
                )
            )
            work.privacy.append_audit(
                self._audit(
                    actor_id=actor_id,
                    action="SESSION_ERASURE_REQUESTED",
                    target_id=session_id,
                    result="PENDING",
                    correlation_id=correlation_id,
                    at=requested_at,
                    metadata={"reason": "DATA_SUBJECT_REQUEST"},
                )
            )
        return operation_id

    def execute_session_erasure(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_id: UUID,
        correlation_id: UUID,
        provider_unlinked: bool,
        at: datetime | None = None,
    ) -> bool:
        completed_at = at or datetime.now(UTC)
        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            operation = work.privacy.get_operation(operation_id, for_update=True)
            if operation is None or operation["kind"] != "SESSION_ERASURE" or operation["session_id"] is None:
                raise ValueError("pending erasure operation was not found")
            session_id = operation["session_id"]
            if work.privacy.has_legal_hold(session_id):
                raise ValueError("session is protected by an active legal hold")
            provider_required = work.privacy.requires_provider_unlink(session_id)
            if provider_required and not provider_unlinked:
                work.privacy.mark_provider_confirmation_pending(operation_id)
                work.privacy.append_audit(
                    self._audit(
                        actor_id=actor_id,
                        action="SESSION_ERASURE_BLOCKED",
                        target_id=session_id,
                        result="PENDING_CONFIRMATION",
                        correlation_id=correlation_id,
                        at=completed_at,
                        metadata={"provider_unlink_required": True},
                    )
                )
                return False
        self._active_states.delete(tenant_id, session_id)
        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            operation = work.privacy.get_operation(operation_id, for_update=True)
            if operation is None or operation["session_id"] != session_id:
                raise ValueError("erasure operation changed before execution")
            work.privacy.append_audit(
                self._audit(
                    actor_id=actor_id,
                    action="SESSION_ERASED",
                    target_id=session_id,
                    result="SUCCEEDED",
                    correlation_id=correlation_id,
                    at=completed_at,
                    metadata={"provider_unlink_required": provider_required},
                )
            )
            work.privacy.erase_session(session_id, operation_id=operation_id)
        return True

    def processing_allowed(self, *, tenant_id: UUID, session_id: UUID) -> bool:
        with UnitOfWork(self._engine, tenant_id=tenant_id) as work:
            return not work.privacy.processing_blocked(session_id)

    def run_retention(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        correlation_id: UUID,
        now: datetime | None = None,
    ) -> RetentionReport:
        current = now or datetime.now(UTC)
        purge_before = current - timedelta(days=max(self._policy.business_record_days, self._policy.event_days))
        minimize_before = current - timedelta(days=self._policy.session_content_days)
        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            purge_ids = work.privacy.retention_candidates(
                ended_before=purge_before,
                action="RETENTION_PURGED",
                limit=self._policy.batch_size,
            )
        purged = 0
        for session_id in purge_ids:
            self._active_states.delete(tenant_id, session_id)
            with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
                work.privacy.append_audit(
                    self._audit(
                        actor_id=actor_id,
                        action="RETENTION_PURGED",
                        target_id=session_id,
                        result="SUCCEEDED",
                        correlation_id=correlation_id,
                        at=current,
                        metadata={
                            "policy_version": self._policy.policy_version,
                            "retention_class": "BUSINESS_AND_EVENTS",
                        },
                    )
                )
                work.privacy.erase_session(session_id)
            purged += 1

        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            minimize_ids = work.privacy.retention_candidates(
                ended_before=minimize_before,
                action="RETENTION_MINIMIZED",
                limit=self._policy.batch_size,
            )
        minimized = 0
        for session_id in minimize_ids:
            if session_id in purge_ids:
                continue
            self._active_states.delete(tenant_id, session_id)
            with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
                counts = work.privacy.minimize_session(session_id)
                work.privacy.append_audit(
                    self._audit(
                        actor_id=actor_id,
                        action="RETENTION_MINIMIZED",
                        target_id=session_id,
                        result="SUCCEEDED",
                        correlation_id=correlation_id,
                        at=current,
                        metadata={
                            "deleted_rows": sum(counts.values()),
                            "policy_version": self._policy.policy_version,
                            "retention_class": "SESSION_CONTENT",
                        },
                    )
                )
                minimized += 1
        with UnitOfWork(self._engine, tenant_id=tenant_id, actor_id=actor_id) as work:
            idempotency, operations = work.privacy.purge_housekeeping(
                now=current,
                operation_cutoff=current - timedelta(days=self._policy.operation_days),
            )
        return RetentionReport(
            minimized_sessions=minimized,
            purged_sessions=purged,
            idempotency_records=max(0, idempotency or 0),
            operations_sanitized=max(0, operations or 0),
        )

    def _audit(
        self,
        *,
        actor_id: UUID,
        action: str,
        target_id: UUID,
        result: str,
        correlation_id: UUID,
        at: datetime,
        metadata: dict[str, Any],
    ) -> AuditRecordCreate:
        return AuditRecordCreate(
            audit_id=new_uuid7(),
            actor_id=actor_id,
            workload="data-lifecycle",
            action=action,
            target_type="SALES_SESSION",
            target_id=target_id,
            result=result,
            correlation_id=correlation_id,
            redacted_metadata=_minimized_metadata(metadata),
            occurred_at=at,
        )


def _minimized_metadata(values: dict[str, Any]) -> dict[str, str | int | bool | None]:
    if not set(values).issubset(_AUDIT_METADATA_KEYS):
        raise ValueError("audit metadata contains a non-allowlisted field")
    minimized: dict[str, str | int | bool | None] = {}
    for key, value in values.items():
        if value is not None and not isinstance(value, str | int | bool):
            raise ValueError("audit metadata values must be scalar")
        if isinstance(value, str) and len(value) > 128:
            raise ValueError("audit metadata text exceeds the minimized limit")
        minimized[key] = value
    return minimized
