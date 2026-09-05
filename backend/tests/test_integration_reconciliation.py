from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knotic_api.domain import OutcomeType
from knotic_api.integrations import (
    InMemoryProviderTruth,
    OutcomeLedger,
    PendingWork,
    ProviderTruth,
    ReconciliationEngine,
    TransactionKind,
    WorkStatus,
)

_NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def _work(kind: TransactionKind = TransactionKind.CALENDAR, *, expected: bool = False) -> PendingWork:
    return PendingWork(
        uuid4(), uuid4(), uuid4(), kind, "provider-1", WorkStatus.PENDING, 0, _NOW, expected_confirmed=expected
    )


def test_all_fr14_outcomes_are_supported_and_invalid_regression_is_blocked() -> None:
    for outcome in OutcomeType:
        ledger = OutcomeLedger()
        reference = "provider-1" if outcome == OutcomeType.ENTERPRISE_DEMO_BOOKED else None
        assert (
            ledger.assign(
                tenant_id=uuid4(), session_id=uuid4(), outcome=outcome, source_reference=reference, assigned_at=_NOW
            ).outcome
            == outcome
        )

    tenant_id, session_id = uuid4(), uuid4()
    ledger = OutcomeLedger()
    ledger.assign(
        tenant_id=tenant_id,
        session_id=session_id,
        outcome=OutcomeType.ENTERPRISE_DEMO_BOOKED,
        source_reference="event-1",
        assigned_at=_NOW,
    )
    with pytest.raises(ValueError, match="not allowed"):
        ledger.assign(
            tenant_id=tenant_id,
            session_id=session_id,
            outcome=OutcomeType.NURTURE,
            source_reference=None,
            assigned_at=_NOW,
        )


def test_confirmed_provider_truth_completes_work_and_assigns_outcome() -> None:
    truth, ledger = InMemoryProviderTruth(), OutcomeLedger()
    engine = ReconciliationEngine(provider=truth, outcomes=ledger)
    item = engine.enqueue(_work(TransactionKind.CALENDAR))
    truth.values[(item.tenant_id, item.kind, item.provider_reference)] = ProviderTruth.CONFIRMED
    result = engine.run_due(now=_NOW)
    assert result[0].status == WorkStatus.SUCCEEDED
    assert (
        ledger.current(tenant_id=item.tenant_id, session_id=item.session_id).outcome
        == OutcomeType.ENTERPRISE_DEMO_BOOKED
    )  # type: ignore[union-attr]


def test_stale_pending_moves_to_dead_letter_and_operator_can_replay() -> None:
    engine = ReconciliationEngine(provider=InMemoryProviderTruth(), outcomes=OutcomeLedger(), max_attempts=2)
    item = engine.enqueue(_work())
    first = engine.run_due(now=_NOW)[0]
    assert first.status == WorkStatus.FAILED_RETRYABLE
    dead = engine.run_due(now=first.next_attempt_at)[0]
    assert dead.status == WorkStatus.DEAD_LETTER
    replay = engine.operator_replay(
        tenant_id=item.tenant_id, work_id=item.work_id, operator_id=uuid4(), at=_NOW + timedelta(minutes=1)
    )
    assert replay.status == WorkStatus.PENDING
    assert engine.audit_events[-1][1].startswith("reconciliation.replayed:")


def test_duplicate_callback_is_idempotent_and_conflict_fails_closed() -> None:
    engine = ReconciliationEngine(provider=InMemoryProviderTruth(), outcomes=OutcomeLedger())
    item = engine.enqueue(_work(TransactionKind.FOLLOWUP))
    first = engine.apply_callback(
        tenant_id=item.tenant_id,
        work_id=item.work_id,
        callback_id="callback-1",
        truth=ProviderTruth.CONFIRMED,
        occurred_at=_NOW,
    )
    duplicate = engine.apply_callback(
        tenant_id=item.tenant_id,
        work_id=item.work_id,
        callback_id="callback-1",
        truth=ProviderTruth.CONFIRMED,
        occurred_at=_NOW,
    )
    assert duplicate == first
    with pytest.raises(ValueError, match="conflicting"):
        engine.apply_callback(
            tenant_id=item.tenant_id,
            work_id=item.work_id,
            callback_id="callback-1",
            truth=ProviderTruth.FAILED,
            occurred_at=_NOW,
        )


def test_provider_discrepancy_creates_visible_alert() -> None:
    truth = InMemoryProviderTruth()
    engine = ReconciliationEngine(provider=truth, outcomes=OutcomeLedger())
    item = engine.enqueue(_work(expected=True))
    truth.values[(item.tenant_id, item.kind, item.provider_reference)] = ProviderTruth.NOT_FOUND
    result = engine.run_due(now=_NOW)[0]
    assert result.status == WorkStatus.DEAD_LETTER
    assert engine.alerts[0].provider_status == ProviderTruth.NOT_FOUND
