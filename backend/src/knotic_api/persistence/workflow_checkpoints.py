"""Tenant-scoped durable workflow-turn checkpoint repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Connection

from .workflow_schema import workflow_turn_checkpoints


class WorkflowCheckpointStatus(StrEnum):
    STARTED = "STARTED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    COMMITTED = "COMMITTED"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


class WorkflowCheckpointBusy(RuntimeError):
    """Another worker owns the unexpired turn lease."""


@dataclass(frozen=True, slots=True)
class WorkflowCheckpointRecord:
    checkpoint_id: UUID
    tenant_id: UUID
    session_id: UUID
    turn_id: UUID
    input_hash: bytes
    status: WorkflowCheckpointStatus
    attempt_count: int
    state_ciphertext: bytes | None
    state_hash: bytes | None
    safe_error_code: str | None
    safe_error_message: str | None
    failed_node: str | None
    lease_expires_at: datetime | None


class WorkflowCheckpointRepository:
    def __init__(self, connection: Connection, tenant_id: UUID) -> None:
        self.connection = connection
        self.tenant_id = tenant_id

    def reserve(
        self,
        *,
        checkpoint_id: UUID,
        session_id: UUID,
        turn_id: UUID,
        input_hash: bytes,
        now: datetime,
        lease_seconds: int,
    ) -> WorkflowCheckpointRecord:
        if any(identifier.version != 7 for identifier in (checkpoint_id, session_id, turn_id)):
            raise ValueError("workflow checkpoint identifiers must be UUIDv7")
        if len(input_hash) != 32 or not 1 <= lease_seconds <= 30:
            raise ValueError("workflow checkpoint reservation is invalid")
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        inserted = (
            self.connection.execute(
                postgres_insert(workflow_turn_checkpoints)
                .values(
                    id=checkpoint_id,
                    tenant_id=self.tenant_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    input_hash=input_hash,
                    status=WorkflowCheckpointStatus.STARTED.value,
                    attempt_count=1,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        workflow_turn_checkpoints.c.tenant_id,
                        workflow_turn_checkpoints.c.session_id,
                        workflow_turn_checkpoints.c.turn_id,
                    ]
                )
                .returning(workflow_turn_checkpoints)
            )
            .mappings()
            .one_or_none()
        )
        if inserted is not None:
            return self._record(inserted)
        row = (
            self.connection.execute(
                sa.select(workflow_turn_checkpoints)
                .where(
                    workflow_turn_checkpoints.c.tenant_id == self.tenant_id,
                    workflow_turn_checkpoints.c.session_id == session_id,
                    workflow_turn_checkpoints.c.turn_id == turn_id,
                )
                .with_for_update()
            )
            .mappings()
            .one()
        )
        if row["input_hash"] != input_hash:
            raise ValueError("workflow turn replay conflicts with the original input")
        status = WorkflowCheckpointStatus(row["status"])
        if status in {WorkflowCheckpointStatus.COMMITTED, WorkflowCheckpointStatus.TERMINAL_FAILURE}:
            return self._record(row)
        if status == WorkflowCheckpointStatus.STARTED and row["lease_expires_at"] > now:
            raise WorkflowCheckpointBusy("workflow turn is already in progress")
        if row["attempt_count"] >= 3:
            raise ValueError("workflow turn exhausted its durable attempt budget")
        resumed = (
            self.connection.execute(
                workflow_turn_checkpoints.update()
                .where(
                    workflow_turn_checkpoints.c.tenant_id == self.tenant_id,
                    workflow_turn_checkpoints.c.id == row["id"],
                    workflow_turn_checkpoints.c.status.in_(
                        [WorkflowCheckpointStatus.STARTED.value, WorkflowCheckpointStatus.RETRYABLE_FAILURE.value]
                    ),
                )
                .values(
                    status=WorkflowCheckpointStatus.STARTED.value,
                    attempt_count=workflow_turn_checkpoints.c.attempt_count + 1,
                    lease_expires_at=lease_expires_at,
                    safe_error_code=None,
                    safe_error_message=None,
                    failed_node=None,
                    updated_at=now,
                )
                .returning(workflow_turn_checkpoints)
            )
            .mappings()
            .one()
        )
        return self._record(resumed)

    def commit(
        self,
        record: WorkflowCheckpointRecord,
        *,
        state_ciphertext: bytes,
        state_hash: bytes,
        now: datetime,
    ) -> None:
        if len(state_hash) != 32 or not state_ciphertext:
            raise ValueError("committed workflow checkpoint requires authenticated state")
        result = self.connection.execute(
            workflow_turn_checkpoints.update()
            .where(
                workflow_turn_checkpoints.c.tenant_id == self.tenant_id,
                workflow_turn_checkpoints.c.id == record.checkpoint_id,
                workflow_turn_checkpoints.c.status == WorkflowCheckpointStatus.STARTED.value,
                workflow_turn_checkpoints.c.attempt_count == record.attempt_count,
            )
            .values(
                status=WorkflowCheckpointStatus.COMMITTED.value,
                state_ciphertext=state_ciphertext,
                state_hash=state_hash,
                lease_expires_at=None,
                committed_at=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise ValueError("workflow checkpoint commit lost its lease")

    def fail(
        self,
        record: WorkflowCheckpointRecord,
        *,
        retryable: bool,
        error_code: str,
        safe_message: str,
        failed_node: str,
        now: datetime,
    ) -> None:
        status = WorkflowCheckpointStatus.RETRYABLE_FAILURE if retryable else WorkflowCheckpointStatus.TERMINAL_FAILURE
        result = self.connection.execute(
            workflow_turn_checkpoints.update()
            .where(
                workflow_turn_checkpoints.c.tenant_id == self.tenant_id,
                workflow_turn_checkpoints.c.id == record.checkpoint_id,
                workflow_turn_checkpoints.c.status == WorkflowCheckpointStatus.STARTED.value,
                workflow_turn_checkpoints.c.attempt_count == record.attempt_count,
            )
            .values(
                status=status.value,
                safe_error_code=error_code,
                safe_error_message=safe_message,
                failed_node=failed_node,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise ValueError("workflow checkpoint failure lost its lease")

    @staticmethod
    def _record(row: sa.RowMapping) -> WorkflowCheckpointRecord:
        return WorkflowCheckpointRecord(
            checkpoint_id=row["id"],
            tenant_id=row["tenant_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            input_hash=row["input_hash"],
            status=WorkflowCheckpointStatus(row["status"]),
            attempt_count=row["attempt_count"],
            state_ciphertext=row["state_ciphertext"],
            state_hash=row["state_hash"],
            safe_error_code=row["safe_error_code"],
            safe_error_message=row["safe_error_message"],
            failed_node=row["failed_node"],
            lease_expires_at=row["lease_expires_at"],
        )
