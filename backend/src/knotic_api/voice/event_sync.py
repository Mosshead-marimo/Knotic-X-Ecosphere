"""Durable, ordered synchronization for realtime voice control events (P4-T006)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.engine import Engine


class VoiceEventKind(StrEnum):
    CLIENT_READY = "CLIENT_READY"
    RTC_CONNECTED = "RTC_CONNECTED"
    RTC_RECONNECTING = "RTC_RECONNECTING"
    RTC_DISCONNECTED = "RTC_DISCONNECTED"
    MICROPHONE_MUTED = "MICROPHONE_MUTED"
    MICROPHONE_UNMUTED = "MICROPHONE_UNMUTED"
    CALL_ENDED = "CALL_ENDED"


class VoiceControlEvent(BaseModel):
    """Versioned event envelope. Payload is intentionally small and non-sensitive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    stream_id: UUID
    sequence: int = Field(ge=1, le=2_147_483_647)
    event_type: VoiceEventKind
    occurred_at: datetime
    payload: dict[str, str | int | bool | None] = Field(default_factory=dict)
    schema_version: int = Field(default=1, ge=1, le=1)

    @field_validator("event_id", "stream_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("must be UUIDv7")
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must include a timezone")
        return value.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def bound_payload(cls, value: dict[str, str | int | bool | None]) -> dict[str, str | int | bool | None]:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > 2_048:
            raise ValueError("payload exceeds 2048 bytes")
        forbidden = {"audio", "transcript", "token", "authorization", "cookie"}
        if any(key.casefold() in forbidden for key in value):
            raise ValueError("payload contains a prohibited field")
        return value

    def canonical_hash(self) -> bytes:
        canonical = self.model_dump(mode="json")
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).digest()


@dataclass(frozen=True, slots=True)
class VoiceEventAck:
    event_id: UUID
    stream_id: UUID
    acknowledged_sequence: int
    server_sequence: int
    duplicate: bool


class VoiceEventSequenceConflict(Exception):
    def __init__(self, expected_sequence: int, message: str = "voice event sequence conflict") -> None:
        super().__init__(message)
        self.expected_sequence = expected_sequence


class VoiceEventStoreUnavailable(RuntimeError):
    pass


class VoiceEventStore(Protocol):
    def append(self, *, tenant_id: UUID, session_id: UUID, event: VoiceControlEvent) -> VoiceEventAck: ...

    def replay(
        self, *, tenant_id: UUID, session_id: UUID, after_server_sequence: int, limit: int
    ) -> tuple[VoiceEventAck, ...]: ...


class InMemoryVoiceEventStore:
    """Deterministic fault-test adapter with the same ordering rules as PostgreSQL."""

    def __init__(self) -> None:
        self._streams: dict[tuple[UUID, UUID, UUID], list[tuple[VoiceControlEvent, VoiceEventAck, bytes]]] = {}
        self._server_sequences: dict[tuple[UUID, UUID], int] = {}

    def append(self, *, tenant_id: UUID, session_id: UUID, event: VoiceControlEvent) -> VoiceEventAck:
        key = (tenant_id, session_id, event.stream_id)
        records = self._streams.setdefault(key, [])
        expected = len(records) + 1
        if event.sequence <= len(records):
            existing_event, existing_ack, existing_hash = records[event.sequence - 1]
            if existing_event.event_id == event.event_id and existing_hash == event.canonical_hash():
                return replace(existing_ack, duplicate=True)
            raise VoiceEventSequenceConflict(expected, "sequence was already used by a different event")
        if event.sequence != expected:
            raise VoiceEventSequenceConflict(expected)
        session_key = (tenant_id, session_id)
        server_sequence = self._server_sequences.get(session_key, 0) + 1
        self._server_sequences[session_key] = server_sequence
        ack = VoiceEventAck(event.event_id, event.stream_id, event.sequence, server_sequence, False)
        records.append((event, ack, event.canonical_hash()))
        return ack

    def replay(
        self, *, tenant_id: UUID, session_id: UUID, after_server_sequence: int, limit: int
    ) -> tuple[VoiceEventAck, ...]:
        all_records = [
            record for key, values in self._streams.items() if key[:2] == (tenant_id, session_id) for record in values
        ]
        return tuple(
            record[1]
            for record in sorted(all_records, key=lambda item: item[1].server_sequence)
            if record[1].server_sequence > after_server_sequence
        )[:limit]


class PostgresVoiceEventStore:
    """Authoritative adapter; row locking establishes one session-wide order."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, *, tenant_id: UUID, session_id: UUID, event: VoiceControlEvent) -> VoiceEventAck:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)}
                )
                exists = connection.execute(
                    sa.text("select id from sales_sessions where tenant_id=:tenant and id=:session for update"),
                    {"tenant": tenant_id, "session": session_id},
                ).scalar_one_or_none()
                if exists is None:
                    raise LookupError("session was not found")
                last = (
                    connection.execute(
                        sa.text(
                            "select event_id,event_hash,stream_sequence,server_sequence from voice_control_events "
                            "where tenant_id=:tenant and session_id=:session and stream_id=:stream "
                            "order by stream_sequence desc limit 1"
                        ),
                        {"tenant": tenant_id, "session": session_id, "stream": event.stream_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                expected = 1 if last is None else int(last["stream_sequence"]) + 1
                if event.sequence < expected:
                    existing = (
                        connection.execute(
                            sa.text(
                                "select event_id,event_hash,server_sequence from voice_control_events "
                                "where tenant_id=:tenant and session_id=:session and stream_id=:stream "
                                "and stream_sequence=:sequence"
                            ),
                            {
                                "tenant": tenant_id,
                                "session": session_id,
                                "stream": event.stream_id,
                                "sequence": event.sequence,
                            },
                        )
                        .mappings()
                        .one()
                    )
                    if (
                        existing["event_id"] == event.event_id
                        and bytes(existing["event_hash"]) == event.canonical_hash()
                    ):
                        return VoiceEventAck(
                            event.event_id, event.stream_id, event.sequence, int(existing["server_sequence"]), True
                        )
                    raise VoiceEventSequenceConflict(expected, "sequence was already used by a different event")
                if event.sequence != expected:
                    raise VoiceEventSequenceConflict(expected)
                server_sequence = int(
                    connection.execute(
                        sa.text(
                            "select coalesce(max(server_sequence),0)+1 from voice_control_events "
                            "where tenant_id=:tenant and session_id=:session"
                        ),
                        {"tenant": tenant_id, "session": session_id},
                    ).scalar_one()
                )
                connection.execute(
                    sa.text(
                        "insert into voice_control_events "
                        "(event_id,tenant_id,session_id,stream_id,stream_sequence,server_sequence,event_type,"
                        "schema_version,occurred_at,payload,event_hash) values "
                        "(:event_id,:tenant,:session,:stream,:stream_sequence,:server_sequence,:event_type,"
                        ":schema_version,:occurred_at,cast(:payload as jsonb),:event_hash)"
                    ),
                    {
                        "event_id": event.event_id,
                        "tenant": tenant_id,
                        "session": session_id,
                        "stream": event.stream_id,
                        "stream_sequence": event.sequence,
                        "server_sequence": server_sequence,
                        "event_type": event.event_type.value,
                        "schema_version": event.schema_version,
                        "occurred_at": event.occurred_at,
                        "payload": json.dumps(event.payload, sort_keys=True),
                        "event_hash": event.canonical_hash(),
                    },
                )
                return VoiceEventAck(event.event_id, event.stream_id, event.sequence, server_sequence, False)
        except (VoiceEventSequenceConflict, LookupError):
            raise
        except sa.exc.SQLAlchemyError as error:
            raise VoiceEventStoreUnavailable("voice event store is unavailable") from error

    def replay(
        self, *, tenant_id: UUID, session_id: UUID, after_server_sequence: int, limit: int
    ) -> tuple[VoiceEventAck, ...]:
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    sa.text("select set_config('app.tenant_id', :tenant, true)"), {"tenant": str(tenant_id)}
                )
                rows = connection.execute(
                    sa.text(
                        "select event_id,stream_id,stream_sequence,server_sequence from voice_control_events "
                        "where tenant_id=:tenant and session_id=:session and server_sequence>:after "
                        "order by server_sequence limit :limit"
                    ),
                    {"tenant": tenant_id, "session": session_id, "after": after_server_sequence, "limit": limit},
                ).mappings()
                return tuple(
                    VoiceEventAck(
                        row["event_id"],
                        row["stream_id"],
                        int(row["stream_sequence"]),
                        int(row["server_sequence"]),
                        True,
                    )
                    for row in rows
                )
        except sa.exc.SQLAlchemyError as error:
            raise VoiceEventStoreUnavailable("voice event store is unavailable") from error


class VoiceEventSynchronizer:
    def __init__(self, store: VoiceEventStore) -> None:
        self._store = store

    def accept(self, *, tenant_id: UUID, session_id: UUID, event: VoiceControlEvent) -> VoiceEventAck:
        return self._store.append(tenant_id=tenant_id, session_id=session_id, event=event)

    def reconcile(
        self, *, tenant_id: UUID, session_id: UUID, after: int, limit: int = 100
    ) -> tuple[VoiceEventAck, ...]:
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("invalid replay cursor or limit")
        return self._store.replay(tenant_id=tenant_id, session_id=session_id, after_server_sequence=after, limit=limit)
