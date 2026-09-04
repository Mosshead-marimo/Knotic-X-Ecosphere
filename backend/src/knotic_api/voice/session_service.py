"""Agora RTC session/token issuance, renewal, and revocation (P4-T001).

The channel name and numeric UID are always derived deterministically from the caller's own
tenant, session, and actor identifiers -- a caller has no way to request an arbitrary channel or
another actor's UID, so unauthorized cross-tenant or cross-session channel access is structurally
impossible rather than merely checked. Every issuance, renewal, revocation, and denial is recorded
through :class:`AgoraAuditSink`. The Agora App Certificate is accepted only as a constructor
argument here and is never included in any value this module returns or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn, Protocol
from uuid import UUID

import redis

from ..security import derive_key
from .agora_token import Role, build_rtc_token

_ENVIRONMENT = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class AgoraSessionDenied(Exception):
    """A token could not be issued or renewed for policy or state reasons."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AgoraSessionStoreUnavailable(RuntimeError):
    """The Agora session store could not confirm the requested operation."""


def channel_name_for(*, tenant_id: UUID, session_id: UUID) -> str:
    """Deterministic, tenant- and session-scoped Agora channel name.

    Never accepted as caller input: a client can only ever be issued a token for the channel
    belonging to its own session. Agora channel names are limited to 64 bytes, too short to hold
    both raw UUIDs (72 bytes together), so this hashes them into a fixed-length, still effectively
    collision-free (192-bit) name instead of concatenating them directly.
    """
    digest = hashlib.sha256(f"{tenant_id}:{session_id}".encode()).hexdigest()
    return f"knotic-{digest[:48]}"


def uid_for(actor_id: UUID) -> int:
    """Deterministic Agora numeric UID (1..2**32-1) derived from an actor's UUID.

    Stable per actor so reconnects and renewals reuse the same UID within a channel.
    """
    digest = hashlib.sha256(actor_id.bytes).digest()
    return int.from_bytes(digest[:4], "big") % (2**32 - 1) + 1


@dataclass(frozen=True, slots=True)
class IssuedAgoraToken:
    channel_name: str
    uid: int
    role: Role
    token: str
    app_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveAgoraSession:
    tenant_id: UUID
    session_id: UUID
    uid: int
    channel_name: str
    role: Role
    issued_at: datetime
    expires_at: datetime


class AgoraSessionStore(Protocol):
    def put_active(self, session: ActiveAgoraSession) -> None: ...

    def get_active(self, *, tenant_id: UUID, session_id: UUID) -> ActiveAgoraSession | None: ...

    def revoke(self, *, tenant_id: UUID, session_id: UUID) -> None: ...

    def is_revoked(self, *, tenant_id: UUID, session_id: UUID) -> bool: ...


class InMemoryAgoraSessionStore:
    """Deterministic test double; production uses :class:`RedisAgoraSessionStore`."""

    def __init__(self) -> None:
        self._active: dict[tuple[UUID, UUID], ActiveAgoraSession] = {}
        self._revoked: set[tuple[UUID, UUID]] = set()

    def put_active(self, session: ActiveAgoraSession) -> None:
        key = (session.tenant_id, session.session_id)
        self._active[key] = session
        self._revoked.discard(key)

    def get_active(self, *, tenant_id: UUID, session_id: UUID) -> ActiveAgoraSession | None:
        return self._active.get((tenant_id, session_id))

    def revoke(self, *, tenant_id: UUID, session_id: UUID) -> None:
        self._revoked.add((tenant_id, session_id))
        self._active.pop((tenant_id, session_id), None)

    def is_revoked(self, *, tenant_id: UUID, session_id: UUID) -> bool:
        return (tenant_id, session_id) in self._revoked


class RedisAgoraSessionStore:
    """Production Agora session store: namespaced, HMAC-keyed, TTL-bounded Redis records."""

    def __init__(self, client: redis.Redis, *, environment: str, master_key: bytes) -> None:
        if not _ENVIRONMENT.fullmatch(environment):
            raise ValueError("invalid Agora session environment")
        self._client = client
        self._environment = environment
        self._key_hmac_key = derive_key(master_key, b"agora-session-lookup")

    def put_active(self, session: ActiveAgoraSession) -> None:
        ttl = max(1, int(session.expires_at.timestamp() - time.time()))
        payload = json.dumps(
            {
                "tenant_id": str(session.tenant_id),
                "session_id": str(session.session_id),
                "uid": session.uid,
                "channel_name": session.channel_name,
                "role": session.role,
                "issued_at": session.issued_at.isoformat(),
                "expires_at": session.expires_at.isoformat(),
            }
        ).encode()
        try:
            self._client.set(self._active_key(session.tenant_id, session.session_id), payload, ex=ttl)
            self._client.delete(self._revoked_key(session.tenant_id, session.session_id))
        except redis.RedisError as error:
            raise AgoraSessionStoreUnavailable("Agora session could not be recorded") from error

    def get_active(self, *, tenant_id: UUID, session_id: UUID) -> ActiveAgoraSession | None:
        try:
            raw = self._client.get(self._active_key(tenant_id, session_id))
        except redis.RedisError as error:
            raise AgoraSessionStoreUnavailable("Agora session could not be read") from error
        if not isinstance(raw, bytes):
            return None
        document = json.loads(raw)
        return ActiveAgoraSession(
            tenant_id=UUID(document["tenant_id"]),
            session_id=UUID(document["session_id"]),
            uid=int(document["uid"]),
            channel_name=str(document["channel_name"]),
            role=document["role"],
            issued_at=datetime.fromisoformat(document["issued_at"]),
            expires_at=datetime.fromisoformat(document["expires_at"]),
        )

    def revoke(self, *, tenant_id: UUID, session_id: UUID) -> None:
        try:
            self._client.set(self._revoked_key(tenant_id, session_id), b"1", ex=86_400)
            self._client.delete(self._active_key(tenant_id, session_id))
        except redis.RedisError as error:
            raise AgoraSessionStoreUnavailable("Agora session revocation was not confirmed") from error

    def is_revoked(self, *, tenant_id: UUID, session_id: UUID) -> bool:
        try:
            return bool(self._client.exists(self._revoked_key(tenant_id, session_id)))
        except redis.RedisError as error:
            raise AgoraSessionStoreUnavailable("Agora session revocation status could not be read") from error

    def _active_key(self, tenant_id: UUID, session_id: UUID) -> str:
        return f"knotic:{self._environment}:agora-session:{self._digest(tenant_id, session_id)}"

    def _revoked_key(self, tenant_id: UUID, session_id: UUID) -> str:
        return f"knotic:{self._environment}:agora-session-revoked:{self._digest(tenant_id, session_id)}"

    def _digest(self, tenant_id: UUID, session_id: UUID) -> str:
        return hmac.new(self._key_hmac_key, f"{tenant_id}:{session_id}".encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class AgoraAuditEvent:
    occurred_at: datetime
    tenant_id: str
    actor_id: str
    session_id: str
    action: str
    channel_name: str | None
    uid: int | None
    role: str | None
    reason: str | None


class AgoraAuditSink(Protocol):
    def append(self, event: AgoraAuditEvent) -> None: ...


class InMemoryAgoraAuditSink:
    def __init__(self) -> None:
        self.events: list[AgoraAuditEvent] = []

    def append(self, event: AgoraAuditEvent) -> None:
        self.events.append(event)


class LoggingAgoraAuditSink:
    """Audit sink that writes each Agora session event as one structured log line.

    Never logs a token: only the derived channel name, UID, role, action, and reason.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def append(self, event: AgoraAuditEvent) -> None:
        self._logger.info(
            "agora_voice_session_event",
            extra={
                "agora_action": event.action,
                "agora_tenant_id": event.tenant_id,
                "agora_actor_id": event.actor_id,
                "agora_session_id": event.session_id,
                "agora_channel_name": event.channel_name,
                "agora_uid": event.uid,
                "agora_role": event.role,
                "agora_reason": event.reason,
            },
        )


class AgoraSessionTokenService:
    """Issues, renews, and revokes tenant/session-scoped Agora RTC tokens."""

    def __init__(
        self,
        *,
        app_id: str,
        app_certificate: str,
        store: AgoraSessionStore,
        audit_sink: AgoraAuditSink,
        token_ttl_seconds: int = 3600,
    ) -> None:
        if not 60 <= token_ttl_seconds <= 86_400:
            raise ValueError("token_ttl_seconds must be between 60 and 86400")
        self._app_id = app_id
        self._app_certificate = app_certificate
        self._store = store
        self._audit = audit_sink
        self._token_ttl_seconds = token_ttl_seconds

    def issue(
        self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, role: Role, now: datetime
    ) -> IssuedAgoraToken:
        return self._mint(tenant_id, actor_id, session_id, role, now, action="ISSUED")

    def renew(
        self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID, role: Role, now: datetime
    ) -> IssuedAgoraToken:
        if self._store.get_active(tenant_id=tenant_id, session_id=session_id) is None:
            self._deny(tenant_id, actor_id, session_id, reason="no active Agora session to renew")
        return self._mint(tenant_id, actor_id, session_id, role, now, action="RENEWED")

    def revoke(self, *, tenant_id: UUID, actor_id: UUID, session_id: UUID) -> None:
        self._store.revoke(tenant_id=tenant_id, session_id=session_id)
        self._audit.append(
            AgoraAuditEvent(
                occurred_at=datetime.now(UTC),
                tenant_id=str(tenant_id),
                actor_id=str(actor_id),
                session_id=str(session_id),
                action="REVOKED",
                channel_name=None,
                uid=None,
                role=None,
                reason=None,
            )
        )

    def _mint(
        self, tenant_id: UUID, actor_id: UUID, session_id: UUID, role: Role, now: datetime, *, action: str
    ) -> IssuedAgoraToken:
        if self._store.is_revoked(tenant_id=tenant_id, session_id=session_id):
            self._deny(tenant_id, actor_id, session_id, reason="Agora access for this session has been revoked")
        channel_name = channel_name_for(tenant_id=tenant_id, session_id=session_id)
        uid = uid_for(actor_id)
        salt = secrets.randbelow(99_999_998) + 1
        token = build_rtc_token(
            app_id=self._app_id,
            app_certificate=self._app_certificate,
            channel_name=channel_name,
            uid=uid,
            role=role,
            issue_ts=int(now.timestamp()),
            salt=salt,
            token_expire_seconds=self._token_ttl_seconds,
            privilege_expire_seconds=self._token_ttl_seconds,
        )
        expires_at = now + timedelta(seconds=self._token_ttl_seconds)
        self._store.put_active(
            ActiveAgoraSession(
                tenant_id=tenant_id,
                session_id=session_id,
                uid=uid,
                channel_name=channel_name,
                role=role,
                issued_at=now,
                expires_at=expires_at,
            )
        )
        self._audit.append(
            AgoraAuditEvent(
                occurred_at=now,
                tenant_id=str(tenant_id),
                actor_id=str(actor_id),
                session_id=str(session_id),
                action=action,
                channel_name=channel_name,
                uid=uid,
                role=role,
                reason=None,
            )
        )
        return IssuedAgoraToken(
            channel_name=channel_name,
            uid=uid,
            role=role,
            token=token,
            app_id=self._app_id,
            issued_at=now,
            expires_at=expires_at,
        )

    def _deny(self, tenant_id: UUID, actor_id: UUID, session_id: UUID, *, reason: str) -> NoReturn:
        self._audit.append(
            AgoraAuditEvent(
                occurred_at=datetime.now(UTC),
                tenant_id=str(tenant_id),
                actor_id=str(actor_id),
                session_id=str(session_id),
                action="DENIED",
                channel_name=None,
                uid=None,
                role=None,
                reason=reason,
            )
        )
        raise AgoraSessionDenied(reason)
