from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knotic_api.domain import SalesState, SessionStatus, new_uuid7
from knotic_api.persistence import WorkflowCheckpointBusy, WorkflowCheckpointRecord, WorkflowCheckpointStatus
from knotic_api.workflow import (
    CheckpointIdentity,
    SalesGraphState,
    SemanticTurn,
    TurnExecutionStatus,
    TurnRetryPolicy,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    execute_turn,
)

NOW = datetime(2026, 9, 3, 13, 0, tzinfo=UTC)


def _state() -> SalesGraphState:
    tenant_id, session_id = new_uuid7(), new_uuid7()
    sales = SalesState(
        tenant_id=tenant_id,
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    return {
        "schema_version": 1,
        "checkpoint": CheckpointIdentity.for_state(sales),
        "sales_state": sales,
        "turn": SemanticTurn(
            tenant_id=tenant_id,
            session_id=session_id,
            turn_id=new_uuid7(),
            correlation_id=new_uuid7(),
            actor_id=new_uuid7(),
            sequence=1,
            text="Run this turn",
            locale="en-US",
            occurred_at=NOW,
        ),
    }


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.record: WorkflowCheckpointRecord | None = None
        self.committed_state: SalesGraphState | None = None
        self.failures: list[tuple[WorkflowError, bool]] = []

    def acquire(self, state: SalesGraphState, *, lease_seconds: int) -> WorkflowCheckpointRecord:
        del lease_seconds
        if self.record is None:
            self.record = WorkflowCheckpointRecord(
                checkpoint_id=new_uuid7(),
                tenant_id=state["turn"].tenant_id,
                session_id=state["turn"].session_id,
                turn_id=state["turn"].turn_id,
                input_hash=b"i" * 32,
                status=WorkflowCheckpointStatus.STARTED,
                attempt_count=1,
                state_ciphertext=None,
                state_hash=None,
                safe_error_code=None,
                safe_error_message=None,
                failed_node=None,
                lease_expires_at=NOW,
            )
        elif self.record.status == WorkflowCheckpointStatus.RETRYABLE_FAILURE:
            self.record = self._replace(
                status=WorkflowCheckpointStatus.STARTED,
                attempt_count=self.record.attempt_count + 1,
            )
        return self.record

    def load_committed(self, record: WorkflowCheckpointRecord) -> SalesGraphState:
        del record
        assert self.committed_state is not None
        return self.committed_state

    def commit(self, record: WorkflowCheckpointRecord, state: SalesGraphState) -> None:
        assert record.status == WorkflowCheckpointStatus.STARTED
        self.committed_state = state
        self.record = self._replace(status=WorkflowCheckpointStatus.COMMITTED)

    def fail(self, record: WorkflowCheckpointRecord, error: WorkflowError, *, retryable: bool) -> None:
        assert record.status == WorkflowCheckpointStatus.STARTED
        self.failures.append((error, retryable))
        self.record = self._replace(
            status=(
                WorkflowCheckpointStatus.RETRYABLE_FAILURE if retryable else WorkflowCheckpointStatus.TERMINAL_FAILURE
            ),
            safe_error_code=error.code.value,
            safe_error_message=error.safe_message,
            failed_node=error.failed_node.value,
        )

    def _replace(self, **changes: object) -> WorkflowCheckpointRecord:
        assert self.record is not None
        values = {field: getattr(self.record, field) for field in self.record.__dataclass_fields__}
        values.update(changes)
        return WorkflowCheckpointRecord(**values)


class SucceedingInvoker:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, state: SalesGraphState, *, timeout_seconds: float) -> SalesGraphState:
        assert timeout_seconds == 5
        self.calls += 1
        return state


class FailingInvoker:
    def __init__(self, error: WorkflowError, *, failures: int = 99) -> None:
        self.error = error
        self.failures = failures
        self.calls = 0

    def invoke(self, state: SalesGraphState, *, timeout_seconds: float) -> SalesGraphState:
        del timeout_seconds
        self.calls += 1
        if self.calls <= self.failures:
            raise WorkflowExecutionError(self.error)
        return state


def _policy(attempts: int = 2) -> TurnRetryPolicy:
    return TurnRetryPolicy(max_attempts=attempts, attempt_timeout_seconds=5, lease_seconds=6)


def test_committed_turn_replay_does_not_invoke_graph_or_repeat_side_effect() -> None:
    state, store, invoker = _state(), FakeCheckpointStore(), SucceedingInvoker()
    first = execute_turn(state, invoker=invoker, checkpoints=store, policy=_policy())
    replay = execute_turn(state, invoker=invoker, checkpoints=store, policy=_policy())
    assert first.status == replay.status == TurnExecutionStatus.COMMITTED
    assert not first.replayed and replay.replayed
    assert invoker.calls == 1


def test_retryable_failure_resumes_once_then_commits() -> None:
    error = WorkflowError(
        code=WorkflowErrorCode.DEPENDENCY_UNAVAILABLE,
        safe_message="Temporarily unavailable.",
        retryable=True,
        failed_node=WorkflowNode.UNDERSTAND_TURN,
    )
    store, invoker = FakeCheckpointStore(), FailingInvoker(error, failures=1)
    result = execute_turn(_state(), invoker=invoker, checkpoints=store, policy=_policy())
    assert result.status == TurnExecutionStatus.COMMITTED
    assert result.attempts == 2
    assert invoker.calls == 2
    assert store.failures == [(error, True)]


@pytest.mark.parametrize("node", tuple(WorkflowNode))
def test_fault_at_every_node_has_one_deterministic_safe_terminal_state(node: WorkflowNode) -> None:
    error = WorkflowError(
        code=WorkflowErrorCode.INVALID_STATE,
        safe_message="The turn could not be processed safely.",
        retryable=False,
        failed_node=node,
    )
    store = FakeCheckpointStore()
    result = execute_turn(_state(), invoker=FailingInvoker(error), checkpoints=store, policy=_policy())
    assert result.status == TurnExecutionStatus.TERMINAL_FAILURE
    assert result.attempts == 1
    assert result.error is not None and result.error.failed_node == node
    assert store.record is not None and store.record.status == WorkflowCheckpointStatus.TERMINAL_FAILURE


def test_timeout_exhausts_bound_then_replays_terminal_failure() -> None:
    class TimeoutInvoker:
        def invoke(self, state: SalesGraphState, *, timeout_seconds: float) -> SalesGraphState:
            del state, timeout_seconds
            raise TimeoutError

    state, store = _state(), FakeCheckpointStore()
    first = execute_turn(state, invoker=TimeoutInvoker(), checkpoints=store, policy=_policy())
    replay = execute_turn(state, invoker=TimeoutInvoker(), checkpoints=store, policy=_policy())
    assert first.status == replay.status == TurnExecutionStatus.TERMINAL_FAILURE
    assert first.attempts == replay.attempts == 2
    assert replay.replayed
    assert replay.error is not None and replay.error.code == WorkflowErrorCode.DEPENDENCY_TIMEOUT


def test_unexpired_concurrent_lease_fails_with_safe_checkpoint_conflict() -> None:
    class BusyStore(FakeCheckpointStore):
        def acquire(self, state: SalesGraphState, *, lease_seconds: int) -> WorkflowCheckpointRecord:
            del state, lease_seconds
            raise WorkflowCheckpointBusy

    with pytest.raises(WorkflowExecutionError) as caught:
        execute_turn(_state(), invoker=SucceedingInvoker(), checkpoints=BusyStore(), policy=_policy())
    assert caught.value.error.code == WorkflowErrorCode.CHECKPOINT_CONFLICT
    assert caught.value.error.retryable
