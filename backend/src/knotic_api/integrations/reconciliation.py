"""Provider-truth reconciliation and constrained FR-14 outcomes (P5-T009)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from knotic_api.domain import OutcomeType


class TransactionKind(StrEnum):
    CRM = "CRM"
    CALENDAR = "CALENDAR"
    FOLLOWUP = "FOLLOWUP"
    HANDOFF = "HANDOFF"


class ProviderTruth(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    NOT_FOUND = "NOT_FOUND"


class WorkStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    DEAD_LETTER = "DEAD_LETTER"


@dataclass(frozen=True, slots=True)
class OutcomeAssignment:
    tenant_id: UUID
    session_id: UUID
    outcome: OutcomeType
    source_reference: str | None
    assigned_at: datetime
    replaces: OutcomeType | None = None


_OUTCOME_TRANSITIONS: dict[OutcomeType | None, frozenset[OutcomeType]] = {
    None: frozenset(OutcomeType),
    OutcomeType.NURTURE: frozenset(
        {
            OutcomeType.FOLLOWUP_CREATED,
            OutcomeType.LEAD_QUALIFIED,
            OutcomeType.HUMAN_ESCALATED,
            OutcomeType.ENTERPRISE_DEMO_BOOKED,
            OutcomeType.CLOSED_NO_ACTION,
        }
    ),
    OutcomeType.FOLLOWUP_CREATED: frozenset(
        {
            OutcomeType.LEAD_QUALIFIED,
            OutcomeType.HUMAN_ESCALATED,
            OutcomeType.ENTERPRISE_DEMO_BOOKED,
            OutcomeType.CLOSED_NO_ACTION,
        }
    ),
    OutcomeType.LEAD_QUALIFIED: frozenset(
        {OutcomeType.HUMAN_ESCALATED, OutcomeType.ENTERPRISE_DEMO_BOOKED, OutcomeType.CLOSED_NO_ACTION}
    ),
    OutcomeType.HUMAN_ESCALATED: frozenset({OutcomeType.ENTERPRISE_DEMO_BOOKED, OutcomeType.CLOSED_NO_ACTION}),
    OutcomeType.ENTERPRISE_DEMO_BOOKED: frozenset(),
    OutcomeType.CLOSED_NO_ACTION: frozenset(),
}


class OutcomeLedger:
    def __init__(self) -> None:
        self._active: dict[tuple[UUID, UUID], OutcomeAssignment] = {}
        self.history: list[OutcomeAssignment] = []

    def assign(
        self,
        *,
        tenant_id: UUID,
        session_id: UUID,
        outcome: OutcomeType,
        source_reference: str | None,
        assigned_at: datetime,
    ) -> OutcomeAssignment:
        key = (tenant_id, session_id)
        current = self._active.get(key)
        if current is not None and current.outcome == outcome and current.source_reference == source_reference:
            return current
        prior = current.outcome if current else None
        if outcome not in _OUTCOME_TRANSITIONS[prior]:
            raise ValueError(f"outcome transition {prior} -> {outcome} is not allowed")
        if outcome == OutcomeType.ENTERPRISE_DEMO_BOOKED and not source_reference:
            raise ValueError("a booked demo requires a provider confirmation reference")
        assignment = OutcomeAssignment(tenant_id, session_id, outcome, source_reference, assigned_at, prior)
        self._active[key] = assignment
        self.history.append(assignment)
        return assignment

    def current(self, *, tenant_id: UUID, session_id: UUID) -> OutcomeAssignment | None:
        return self._active.get((tenant_id, session_id))


@dataclass(frozen=True, slots=True)
class PendingWork:
    work_id: UUID
    tenant_id: UUID
    session_id: UUID
    kind: TransactionKind
    provider_reference: str
    status: WorkStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None = None
    expected_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class DiscrepancyAlert:
    work_id: UUID
    tenant_id: UUID
    provider_reference: str
    internal_status: str
    provider_status: ProviderTruth
    detected_at: datetime


class ProviderTruthPort(Protocol):
    def get(self, *, tenant_id: UUID, kind: TransactionKind, provider_reference: str) -> ProviderTruth: ...


class InMemoryProviderTruth:
    def __init__(self) -> None:
        self.values: dict[tuple[UUID, TransactionKind, str], ProviderTruth] = {}

    def get(self, *, tenant_id: UUID, kind: TransactionKind, provider_reference: str) -> ProviderTruth:
        return self.values.get((tenant_id, kind, provider_reference), ProviderTruth.PENDING)


_OUTCOME_FOR_KIND = {
    TransactionKind.CRM: OutcomeType.LEAD_QUALIFIED,
    TransactionKind.CALENDAR: OutcomeType.ENTERPRISE_DEMO_BOOKED,
    TransactionKind.FOLLOWUP: OutcomeType.FOLLOWUP_CREATED,
    TransactionKind.HANDOFF: OutcomeType.HUMAN_ESCALATED,
}


class ReconciliationEngine:
    def __init__(self, *, provider: ProviderTruthPort, outcomes: OutcomeLedger, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._provider = provider
        self._outcomes = outcomes
        self._max_attempts = max_attempts
        self.work: dict[tuple[UUID, UUID], PendingWork] = {}
        self.dead_letters: dict[tuple[UUID, UUID], PendingWork] = {}
        self.alerts: list[DiscrepancyAlert] = []
        self._callbacks: dict[tuple[UUID, str], ProviderTruth] = {}
        self.audit_events: list[tuple[UUID, str, datetime]] = []

    def enqueue(self, item: PendingWork) -> PendingWork:
        key = (item.tenant_id, item.work_id)
        existing = self.work.get(key) or self.dead_letters.get(key)
        if existing is not None:
            if existing.provider_reference != item.provider_reference or existing.kind != item.kind:
                raise ValueError("work identifier was reused for a different transaction")
            return existing
        self.work[key] = item
        self.audit_events.append((item.work_id, "reconciliation.enqueued", item.next_attempt_at))
        return item

    def run_due(self, *, now: datetime) -> tuple[PendingWork, ...]:
        results: list[PendingWork] = []
        due = sorted(
            (
                item
                for item in self.work.values()
                if item.next_attempt_at <= now and item.status != WorkStatus.SUCCEEDED
            ),
            key=lambda item: (item.next_attempt_at, str(item.work_id)),
        )
        for item in due:
            results.append(self._reconcile(item, now=now))
        return tuple(results)

    def _reconcile(self, item: PendingWork, *, now: datetime, known_truth: ProviderTruth | None = None) -> PendingWork:
        truth = known_truth or self._provider.get(
            tenant_id=item.tenant_id, kind=item.kind, provider_reference=item.provider_reference
        )
        attempts = item.attempts + 1
        if item.expected_confirmed and truth in {ProviderTruth.FAILED, ProviderTruth.NOT_FOUND}:
            self.alerts.append(
                DiscrepancyAlert(
                    item.work_id,
                    item.tenant_id,
                    item.provider_reference,
                    "CONFIRMED",
                    truth,
                    now,
                )
            )
        if truth == ProviderTruth.CONFIRMED:
            updated = replace(item, status=WorkStatus.SUCCEEDED, attempts=attempts, last_error=None)
            self._outcomes.assign(
                tenant_id=item.tenant_id,
                session_id=item.session_id,
                outcome=_OUTCOME_FOR_KIND[item.kind],
                source_reference=item.provider_reference,
                assigned_at=now,
            )
        elif truth == ProviderTruth.PENDING and attempts < self._max_attempts:
            delay = timedelta(seconds=min(300, 2 ** (attempts - 1)))
            updated = replace(
                item,
                status=WorkStatus.FAILED_RETRYABLE,
                attempts=attempts,
                next_attempt_at=now + delay,
                last_error="PENDING_CONFIRMATION",
            )
        else:
            terminal = attempts >= self._max_attempts or truth in {ProviderTruth.FAILED, ProviderTruth.NOT_FOUND}
            status = WorkStatus.DEAD_LETTER if terminal else WorkStatus.FAILED_RETRYABLE
            updated = replace(item, status=status, attempts=attempts, last_error=truth.value)
        key = (item.tenant_id, item.work_id)
        if updated.status == WorkStatus.DEAD_LETTER:
            self.work.pop(key, None)
            self.dead_letters[key] = updated
            self.audit_events.append((item.work_id, "reconciliation.dead_lettered", now))
        else:
            self.work[key] = updated
            self.audit_events.append((item.work_id, "reconciliation.updated", now))
        return updated

    def apply_callback(
        self, *, tenant_id: UUID, work_id: UUID, callback_id: str, truth: ProviderTruth, occurred_at: datetime
    ) -> PendingWork:
        callback_key = (tenant_id, callback_id)
        previous = self._callbacks.get(callback_key)
        if previous is not None:
            if previous != truth:
                raise ValueError("callback identifier was reused with conflicting provider truth")
            return self.work.get((tenant_id, work_id)) or self.dead_letters[(tenant_id, work_id)]
        item = self.work.get((tenant_id, work_id))
        if item is None:
            raise KeyError("pending work not found")
        self._callbacks[callback_key] = truth
        return self._reconcile(item, now=occurred_at, known_truth=truth)

    def operator_replay(self, *, tenant_id: UUID, work_id: UUID, operator_id: UUID, at: datetime) -> PendingWork:
        key = (tenant_id, work_id)
        item = self.dead_letters.pop(key, None)
        if item is None:
            raise ValueError("only dead-letter work can be replayed")
        replay = replace(item, status=WorkStatus.PENDING, next_attempt_at=at, last_error=None)
        self.work[key] = replay
        self.audit_events.append((work_id, f"reconciliation.replayed:{operator_id}", at))
        return replay


__all__ = [
    "DiscrepancyAlert",
    "InMemoryProviderTruth",
    "OutcomeAssignment",
    "OutcomeLedger",
    "PendingWork",
    "ProviderTruth",
    "ProviderTruthPort",
    "ReconciliationEngine",
    "TransactionKind",
    "WorkStatus",
]
