"""Bounded realtime voice recovery and graceful termination policy (P4-T007)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Engine


class VoiceFault(StrEnum):
    AGORA_TOKEN = "AGORA_TOKEN"  # noqa: S105 - failure category, never a credential
    AGORA_CONNECTION = "AGORA_CONNECTION"
    SPEECH_INPUT = "SPEECH_INPUT"
    SPEECH_OUTPUT = "SPEECH_OUTPUT"
    NETWORK = "NETWORK"
    BACKEND = "BACKEND"
    REDIS = "REDIS"
    POLICY = "POLICY"


class RecoveryAction(StrEnum):
    RETRY = "RETRY"
    FAILOVER = "FAILOVER"
    TERMINATE = "TERMINATE"


class RecoveryStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RECOVERING = "RECOVERING"
    ENDED = "ENDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    max_attempts: int = 3
    overall_timeout_seconds: int = 30
    speech_failover_enabled: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if not 5 <= self.overall_timeout_seconds <= 120:
            raise ValueError("overall_timeout_seconds must be between 5 and 120")


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    tenant_id: UUID
    session_id: UUID
    status: RecoveryStatus
    fault: VoiceFault | None
    attempt: int
    deadline: datetime | None
    safe_message: str | None
    updated_at: datetime
    version: int = 1


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    snapshot: RecoverySnapshot


class RecoveryStore(Protocol):
    def load(self, *, tenant_id: UUID, session_id: UUID) -> RecoverySnapshot | None: ...

    def save(self, snapshot: RecoverySnapshot) -> None: ...


class InMemoryRecoveryStore:
    def __init__(self) -> None:
        self._records: dict[tuple[UUID, UUID], RecoverySnapshot] = {}

    def load(self, *, tenant_id: UUID, session_id: UUID) -> RecoverySnapshot | None:
        return self._records.get((tenant_id, session_id))

    def save(self, snapshot: RecoverySnapshot) -> None:
        self._records[(snapshot.tenant_id, snapshot.session_id)] = snapshot


class RecoveryStoreUnavailable(RuntimeError):
    pass


class PostgresRecoveryStore:
    """Tenant-isolated recovery checkpoint adapter with optimistic version checks."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self, *, tenant_id: UUID, session_id: UUID) -> RecoverySnapshot | None:
        try:
            with self._engine.begin() as connection:
                self._set_tenant(connection, tenant_id)
                row = (
                    connection.execute(
                        sa.text(
                            "select status,fault,attempt,deadline,safe_message,updated_at,version "
                            "from voice_recovery_states where tenant_id=:tenant and session_id=:session"
                        ),
                        {"tenant": tenant_id, "session": session_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    return None
                return RecoverySnapshot(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    status=RecoveryStatus(row["status"]),
                    fault=VoiceFault(row["fault"]) if row["fault"] else None,
                    attempt=int(row["attempt"]),
                    deadline=row["deadline"],
                    safe_message=row["safe_message"],
                    updated_at=row["updated_at"],
                    version=int(row["version"]),
                )
        except sa.exc.SQLAlchemyError as error:
            raise RecoveryStoreUnavailable("voice recovery store is unavailable") from error

    def save(self, snapshot: RecoverySnapshot) -> None:
        values = {
            "tenant": snapshot.tenant_id,
            "session": snapshot.session_id,
            "status": snapshot.status.value,
            "fault": snapshot.fault.value if snapshot.fault else None,
            "attempt": snapshot.attempt,
            "deadline": snapshot.deadline,
            "safe_message": snapshot.safe_message,
            "updated_at": snapshot.updated_at,
            "version": snapshot.version,
        }
        try:
            with self._engine.begin() as connection:
                self._set_tenant(connection, snapshot.tenant_id)
                if snapshot.version == 1:
                    connection.execute(
                        sa.text(
                            "insert into voice_recovery_states "
                            "(tenant_id,session_id,status,fault,attempt,deadline,safe_message,updated_at,version) "
                            "values (:tenant,:session,:status,:fault,:attempt,:deadline,:safe_message,"
                            ":updated_at,:version)"
                        ),
                        values,
                    )
                else:
                    result = connection.execute(
                        sa.text(
                            "update voice_recovery_states set status=:status,fault=:fault,attempt=:attempt,"
                            "deadline=:deadline,safe_message=:safe_message,updated_at=:updated_at,version=:version "
                            "where tenant_id=:tenant and session_id=:session and version=:previous_version"
                        ),
                        {**values, "previous_version": snapshot.version - 1},
                    )
                    if result.rowcount != 1:
                        raise RecoveryStoreUnavailable("voice recovery state changed concurrently")
        except RecoveryStoreUnavailable:
            raise
        except sa.exc.SQLAlchemyError as error:
            raise RecoveryStoreUnavailable("voice recovery store is unavailable") from error

    @staticmethod
    def _set_tenant(connection: sa.Connection, tenant_id: UUID) -> None:
        connection.execute(sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)})


_SAFE_MESSAGES = {
    VoiceFault.AGORA_TOKEN: "Call access is being refreshed.",
    VoiceFault.AGORA_CONNECTION: "The call is reconnecting.",
    VoiceFault.SPEECH_INPUT: "I am having trouble hearing you. Please hold for a moment.",
    VoiceFault.SPEECH_OUTPUT: "Audio playback is recovering. Please hold for a moment.",
    VoiceFault.NETWORK: "The network connection is recovering.",
    VoiceFault.BACKEND: "The conversation service is recovering.",
    VoiceFault.REDIS: "The conversation state service is recovering.",
    VoiceFault.POLICY: "The call cannot continue safely.",
}


class VoiceRecoveryCoordinator:
    """Persists every recovery decision so another process can resume it safely."""

    def __init__(self, store: RecoveryStore, policy: RecoveryPolicy | None = None) -> None:
        self._store = store
        self._policy = policy or RecoveryPolicy()

    def record_failure(
        self, *, tenant_id: UUID, session_id: UUID, fault: VoiceFault, now: datetime
    ) -> RecoveryDecision:
        current = self._store.load(tenant_id=tenant_id, session_id=session_id)
        if current is not None and current.status in {RecoveryStatus.ENDED, RecoveryStatus.FAILED}:
            return RecoveryDecision(RecoveryAction.TERMINATE, current)
        attempt = 1 if current is None or current.fault != fault else current.attempt + 1
        deadline = (
            now + timedelta(seconds=self._policy.overall_timeout_seconds)
            if current is None or current.deadline is None or current.fault != fault
            else current.deadline
        )
        exhausted = attempt > self._policy.max_attempts or now >= deadline or fault == VoiceFault.POLICY
        if exhausted:
            status, action = RecoveryStatus.FAILED, RecoveryAction.TERMINATE
        elif fault in {VoiceFault.SPEECH_INPUT, VoiceFault.SPEECH_OUTPUT} and self._policy.speech_failover_enabled:
            action = RecoveryAction.FAILOVER if attempt == self._policy.max_attempts else RecoveryAction.RETRY
            status = RecoveryStatus.RECOVERING
        else:
            status, action = RecoveryStatus.RECOVERING, RecoveryAction.RETRY
        snapshot = RecoverySnapshot(
            tenant_id=tenant_id,
            session_id=session_id,
            status=status,
            fault=fault,
            attempt=attempt,
            deadline=deadline,
            safe_message=_SAFE_MESSAGES[fault],
            updated_at=now.astimezone(UTC),
            version=1 if current is None else current.version + 1,
        )
        self._store.save(snapshot)
        return RecoveryDecision(action, snapshot)

    def record_recovered(self, *, tenant_id: UUID, session_id: UUID, now: datetime) -> RecoverySnapshot:
        current = self._require(tenant_id, session_id)
        if current.status != RecoveryStatus.RECOVERING:
            raise ValueError("only a recovering call can return to active")
        recovered = replace(
            current,
            status=RecoveryStatus.ACTIVE,
            fault=None,
            attempt=0,
            deadline=None,
            safe_message=None,
            updated_at=now.astimezone(UTC),
            version=current.version + 1,
        )
        self._store.save(recovered)
        return recovered

    def terminate(self, *, tenant_id: UUID, session_id: UUID, now: datetime, failed: bool = False) -> RecoverySnapshot:
        current = self._store.load(tenant_id=tenant_id, session_id=session_id)
        snapshot = RecoverySnapshot(
            tenant_id=tenant_id,
            session_id=session_id,
            status=RecoveryStatus.FAILED if failed else RecoveryStatus.ENDED,
            fault=current.fault if current else None,
            attempt=current.attempt if current else 0,
            deadline=None,
            safe_message="The call ended safely."
            if not failed
            else "The call ended because recovery was unsuccessful.",
            updated_at=now.astimezone(UTC),
            version=1 if current is None else current.version + 1,
        )
        self._store.save(snapshot)
        return snapshot

    def _require(self, tenant_id: UUID, session_id: UUID) -> RecoverySnapshot:
        snapshot = self._store.load(tenant_id=tenant_id, session_id=session_id)
        if snapshot is None:
            raise LookupError("recovery state was not found")
        return snapshot
