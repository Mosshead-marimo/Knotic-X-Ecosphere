"""Durable, bounded, replay-safe workflow turn execution."""

from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import TypeAdapter
from sqlalchemy.engine import Engine

from knotic_api.domain import new_uuid7
from knotic_api.persistence import WorkflowCheckpointBusy, WorkflowCheckpointRecord, WorkflowCheckpointStatus
from knotic_api.persistence.unit_of_work import UnitOfWork
from knotic_api.security import ReplayCipher

from .contracts import (
    SalesGraphState,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    validate_graph_state,
)

_STATE_ADAPTER = TypeAdapter(SalesGraphState)


class TurnExecutionStatus(StrEnum):
    COMMITTED = "COMMITTED"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


@dataclass(frozen=True, slots=True)
class TurnExecutionResult:
    status: TurnExecutionStatus
    attempts: int
    replayed: bool
    state: SalesGraphState | None = None
    error: WorkflowError | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.attempts <= 3:
            raise ValueError("turn execution attempts must be between one and three")
        if (self.status == TurnExecutionStatus.COMMITTED) != (self.state is not None):
            raise ValueError("only committed executions contain state")
        if (self.status == TurnExecutionStatus.TERMINAL_FAILURE) != (self.error is not None):
            raise ValueError("only failed executions contain an error")


@dataclass(frozen=True, slots=True)
class TurnRetryPolicy:
    max_attempts: int = 2
    attempt_timeout_seconds: float = 15.0
    lease_seconds: int = 20

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("workflow retries allow one to three total attempts")
        if not 1 <= self.attempt_timeout_seconds <= 30:
            raise ValueError("workflow attempt timeout must be between one and thirty seconds")
        if self.lease_seconds < self.attempt_timeout_seconds or self.lease_seconds > 30:
            raise ValueError("workflow lease must cover the attempt timeout and remain bounded")


class GraphInvocationPort(Protocol):
    def invoke(self, state: SalesGraphState, *, timeout_seconds: float) -> SalesGraphState: ...


class WorkflowCheckpointStore(Protocol):
    def acquire(self, state: SalesGraphState, *, lease_seconds: int) -> WorkflowCheckpointRecord: ...

    def load_committed(self, record: WorkflowCheckpointRecord) -> SalesGraphState: ...

    def commit(self, record: WorkflowCheckpointRecord, state: SalesGraphState) -> None: ...

    def fail(self, record: WorkflowCheckpointRecord, error: WorkflowError, *, retryable: bool) -> None: ...


class BoundedGraphInvoker:
    """Runs synchronous LangGraph work in a bounded shared worker pool."""

    def __init__(self, graph: Any, *, max_workers: int = 8) -> None:
        if not 1 <= max_workers <= 32:
            raise ValueError("workflow worker count must be between one and thirty-two")
        self._graph = graph
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sales-workflow")

    def invoke(self, state: SalesGraphState, *, timeout_seconds: float) -> SalesGraphState:
        future = self._pool.submit(self._graph.invoke, dict(state))
        try:
            return validate_graph_state(future.result(timeout=timeout_seconds))
        except FutureTimeout as error:
            future.cancel()
            raise TimeoutError("workflow attempt exceeded its deadline") from error

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class PostgresWorkflowCheckpointStore:
    """Short-transaction adapter for encrypted durable turn checkpoints."""

    def __init__(self, engine: Engine, *, encryption_key: bytes) -> None:
        self._engine = engine
        self._cipher = ReplayCipher(encryption_key)

    def acquire(self, state: SalesGraphState, *, lease_seconds: int) -> WorkflowCheckpointRecord:
        validated = validate_graph_state(state)
        payload = _STATE_ADAPTER.dump_json(validated)
        digest = hashlib.sha256(payload).digest()
        with UnitOfWork(
            self._engine, tenant_id=validated["turn"].tenant_id, actor_id=validated["turn"].actor_id
        ) as work:
            return work.workflow_checkpoints.reserve(
                checkpoint_id=new_uuid7(),
                session_id=validated["turn"].session_id,
                turn_id=validated["turn"].turn_id,
                input_hash=digest,
                now=datetime.now(UTC),
                lease_seconds=lease_seconds,
            )

    def load_committed(self, record: WorkflowCheckpointRecord) -> SalesGraphState:
        if record.status != WorkflowCheckpointStatus.COMMITTED:
            raise ValueError("only committed workflow checkpoints can be loaded")
        if record.state_ciphertext is None or record.state_hash is None:
            raise ValueError("committed workflow checkpoint is missing state")
        payload = self._cipher.decrypt(record.state_ciphertext, associated_data=self._associated_data(record))
        if not hmac.compare_digest(hashlib.sha256(payload).digest(), record.state_hash):
            raise ValueError("workflow checkpoint state hash did not match")
        return validate_graph_state(_STATE_ADAPTER.validate_json(payload))

    def commit(self, record: WorkflowCheckpointRecord, state: SalesGraphState) -> None:
        payload = _STATE_ADAPTER.dump_json(validate_graph_state(state))
        ciphertext = self._cipher.encrypt(payload, associated_data=self._associated_data(record))
        with UnitOfWork(self._engine, tenant_id=record.tenant_id) as work:
            work.workflow_checkpoints.commit(
                record,
                state_ciphertext=ciphertext,
                state_hash=hashlib.sha256(payload).digest(),
                now=datetime.now(UTC),
            )

    def fail(self, record: WorkflowCheckpointRecord, error: WorkflowError, *, retryable: bool) -> None:
        with UnitOfWork(self._engine, tenant_id=record.tenant_id) as work:
            work.workflow_checkpoints.fail(
                record,
                retryable=retryable,
                error_code=error.code.value,
                safe_message=error.safe_message,
                failed_node=error.failed_node.value,
                now=datetime.now(UTC),
            )

    @staticmethod
    def _associated_data(record: WorkflowCheckpointRecord) -> bytes:
        return f"knotic:workflow:v1:{record.tenant_id}:{record.session_id}:{record.turn_id}".encode()


def execute_turn(
    state: SalesGraphState,
    *,
    invoker: GraphInvocationPort,
    checkpoints: WorkflowCheckpointStore,
    policy: TurnRetryPolicy | None = None,
) -> TurnExecutionResult:
    validated = validate_graph_state(state)
    policy = policy or TurnRetryPolicy()
    while True:
        try:
            record = checkpoints.acquire(validated, lease_seconds=policy.lease_seconds)
        except WorkflowCheckpointBusy as error:
            raise WorkflowExecutionError(
                WorkflowError(
                    code=WorkflowErrorCode.CHECKPOINT_CONFLICT,
                    safe_message="This turn is already being processed.",
                    retryable=True,
                    failed_node=WorkflowNode.RECEIVE_TURN,
                )
            ) from error
        except ValueError as error:
            raise WorkflowExecutionError(
                WorkflowError(
                    code=WorkflowErrorCode.INVALID_TURN,
                    safe_message="This turn conflicts with an existing workflow record.",
                    retryable=False,
                    failed_node=WorkflowNode.RECEIVE_TURN,
                )
            ) from error
        if record.status == WorkflowCheckpointStatus.COMMITTED:
            return TurnExecutionResult(
                status=TurnExecutionStatus.COMMITTED,
                attempts=record.attempt_count,
                replayed=True,
                state=checkpoints.load_committed(record),
            )
        if record.status == WorkflowCheckpointStatus.TERMINAL_FAILURE:
            return TurnExecutionResult(
                status=TurnExecutionStatus.TERMINAL_FAILURE,
                attempts=record.attempt_count,
                replayed=True,
                error=_stored_error(record),
            )
        try:
            result = validate_graph_state(invoker.invoke(validated, timeout_seconds=policy.attempt_timeout_seconds))
        except WorkflowExecutionError as caught:
            failure = caught.error
        except TimeoutError:
            failure = WorkflowError(
                code=WorkflowErrorCode.DEPENDENCY_TIMEOUT,
                safe_message="The sales workflow timed out. Please try again.",
                retryable=True,
                failed_node=WorkflowNode.GENERATE_RESPONSE,
            )
        except Exception:
            failure = WorkflowError(
                code=WorkflowErrorCode.INVALID_STATE,
                safe_message="The sales workflow could not safely process this turn.",
                retryable=False,
                failed_node=WorkflowNode.RECEIVE_TURN,
            )
        else:
            checkpoints.commit(record, result)
            return TurnExecutionResult(
                status=TurnExecutionStatus.COMMITTED,
                attempts=record.attempt_count,
                replayed=False,
                state=result,
            )
        retryable = failure.retryable and record.attempt_count < policy.max_attempts
        checkpoints.fail(record, failure, retryable=retryable)
        if retryable:
            continue
        terminal_error = failure.model_copy(update={"retryable": False})
        return TurnExecutionResult(
            status=TurnExecutionStatus.TERMINAL_FAILURE,
            attempts=record.attempt_count,
            replayed=False,
            error=terminal_error,
        )


def _stored_error(record: WorkflowCheckpointRecord) -> WorkflowError:
    if record.safe_error_code is None or record.safe_error_message is None or record.failed_node is None:
        raise ValueError("terminal workflow checkpoint is missing its safe failure")
    return WorkflowError(
        code=WorkflowErrorCode(record.safe_error_code),
        safe_message=record.safe_error_message,
        retryable=False,
        failed_node=WorkflowNode(record.failed_node),
    )
