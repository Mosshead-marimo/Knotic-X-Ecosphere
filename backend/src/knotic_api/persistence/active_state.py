"""Redis-backed, rebuildable active SalesState projection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

import redis
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from knotic_api.domain.models import SalesState

_ENVIRONMENT = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_COMPARE_AND_SET = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {-1, -1} end
local ok, current = pcall(cjson.decode, raw)
if not ok then redis.call('DEL', KEYS[1]); return {-2, -2} end
local current_version = tonumber(current['state_version'])
local current_fence = tonumber(current['fencing_token'])
if current_version == nil or current_fence == nil then
  redis.call('DEL', KEYS[1])
  return {-2, -2}
end
local expected_version = tonumber(ARGV[1])
local proposed_fence = tonumber(ARGV[2])
if proposed_fence < current_fence then return {-3, current_fence} end
if current_version ~= expected_version then return {0, current_version} end
redis.call('SET', KEYS[1], ARGV[3], 'EX', ARGV[4])
return {1, tonumber(ARGV[5])}
"""
_COMPARE_AND_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_COMPARE_AND_REPLACE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""
_READ_STATE = """
if redis.call('EXISTS', KEYS[2]) == 1 then return '__KNOTIC_BLOCKED__' end
local raw = redis.call('GET', KEYS[1])
if raw then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return raw
"""
_BLOCK_PROCESSING = """
redis.call('SET', KEYS[2], '1', 'EX', ARGV[1])
return redis.call('DEL', KEYS[1])
"""


class ActiveStateError(RuntimeError):
    """Base class for safe active-state persistence failures."""


class ActiveStateUnavailable(ActiveStateError):
    """Redis could not confirm the requested operation."""


class ConcurrentStateUpdate(ActiveStateError):
    def __init__(self, *, expected_version: int, observed_version: int | None) -> None:
        super().__init__("active state changed concurrently")
        self.expected_version = expected_version
        self.observed_version = observed_version


class StaleFencingToken(ActiveStateError):
    def __init__(self, *, proposed_token: int, observed_token: int) -> None:
        super().__init__("active state writer lease is stale")
        self.proposed_token = proposed_token
        self.observed_token = observed_token


class CorruptActiveState(ActiveStateError):
    """The stored projection cannot be trusted and must be rebuilt."""


class CacheReadStatus(StrEnum):
    HIT = "HIT"
    MISS = "MISS"
    CORRUPT = "CORRUPT"
    BLOCKED = "BLOCKED"


class ActiveStateEnvelope(BaseModel):
    """Versioned cache value; durable PostgreSQL remains authoritative."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    tenant_id: UUID
    session_id: UUID
    state_version: int = Field(ge=1)
    event_watermark: int = Field(ge=0)
    fencing_token: int = Field(ge=0)
    updated_at: AwareDatetime
    state: SalesState

    @model_validator(mode="after")
    def validate_projection_identity(self) -> ActiveStateEnvelope:
        if self.tenant_id != self.state.tenant_id or self.session_id != self.state.session_id:
            raise ValueError("active-state envelope identity does not match its state")
        if self.state_version != self.state.version:
            raise ValueError("active-state envelope version does not match its state")
        if self.updated_at != self.state.updated_at:
            raise ValueError("active-state envelope timestamp does not match its state")
        return self


@dataclass(frozen=True, slots=True)
class ActiveStateRead:
    status: CacheReadStatus
    envelope: ActiveStateEnvelope | None = None
    migrated: bool = False


PositiveSeconds = Annotated[int, Field(ge=1, le=604_800)]


class RedisSalesStateRepository:
    """Atomic, version-checked Redis projection repository."""

    def __init__(
        self,
        client: redis.Redis,
        *,
        environment: str,
        ttl_seconds: PositiveSeconds = 86_400,
        max_payload_bytes: int = 512 * 1024,
    ) -> None:
        if not _ENVIRONMENT.fullmatch(environment):
            raise ValueError("environment must be lowercase and safe for Redis keys")
        if not 1 <= ttl_seconds <= 604_800:
            raise ValueError("ttl_seconds must be between 1 and 604800")
        if not 1024 <= max_payload_bytes <= 2 * 1024 * 1024:
            raise ValueError("max_payload_bytes must be between 1 KiB and 2 MiB")
        self._client = client
        self._environment = environment
        self._ttl_seconds = ttl_seconds
        self._max_payload_bytes = max_payload_bytes

    def key(self, tenant_id: UUID, session_id: UUID) -> str:
        self._require_uuid7(tenant_id)
        self._require_uuid7(session_id)
        return f"knotic:{self._environment}:{{{tenant_id}:{session_id}}}:state:v1"

    def privacy_block_key(self, tenant_id: UUID, session_id: UUID) -> str:
        self._require_uuid7(tenant_id)
        self._require_uuid7(session_id)
        return f"knotic:{self._environment}:{{{tenant_id}:{session_id}}}:privacy-block:v1"

    def load(self, tenant_id: UUID, session_id: UUID) -> ActiveStateRead:
        return self._load(tenant_id, session_id, allow_migration_retry=True)

    def _load(self, tenant_id: UUID, session_id: UUID, *, allow_migration_retry: bool) -> ActiveStateRead:
        key = self.key(tenant_id, session_id)
        try:
            raw = self._client.eval(
                _READ_STATE,
                2,
                key,
                self.privacy_block_key(tenant_id, session_id),
                self._ttl_seconds,
            )
        except redis.RedisError as error:
            raise ActiveStateUnavailable("active state read was not confirmed") from error
        if raw is None:
            return ActiveStateRead(CacheReadStatus.MISS)
        if raw == b"__KNOTIC_BLOCKED__":
            return ActiveStateRead(CacheReadStatus.BLOCKED)
        if not isinstance(raw, bytes) or len(raw) > self._max_payload_bytes:
            self._discard_corrupt(key, raw)
            return ActiveStateRead(CacheReadStatus.CORRUPT)
        try:
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError("active state envelope must be an object")
            migrated = document.get("schema_version") == 0
            if migrated:
                document = self._migrate_v0(document)
            envelope = ActiveStateEnvelope.model_validate(document)
        except (ValidationError, ValueError):
            self._discard_corrupt(key, raw)
            return ActiveStateRead(CacheReadStatus.CORRUPT)
        if envelope.tenant_id != tenant_id or envelope.session_id != session_id:
            self._discard_corrupt(key, raw)
            return ActiveStateRead(CacheReadStatus.CORRUPT)
        if migrated:
            payload = self._serialize(envelope)
            try:
                replaced = self._client.eval(_COMPARE_AND_REPLACE, 1, key, raw, payload, self._ttl_seconds)
            except redis.RedisError as error:
                raise ActiveStateUnavailable("active state migration was not confirmed") from error
            if int(replaced) != 1:
                if allow_migration_retry:
                    return self._load(tenant_id, session_id, allow_migration_retry=False)
                raise ActiveStateUnavailable("active state changed repeatedly during schema migration")
        return ActiveStateRead(CacheReadStatus.HIT, envelope, migrated=migrated)

    def create(
        self,
        state: SalesState,
        *,
        event_watermark: int,
        fencing_token: int,
    ) -> ActiveStateEnvelope:
        envelope = self._envelope(state, event_watermark=event_watermark, fencing_token=fencing_token)
        payload = self._serialize(envelope)
        try:
            created = self._client.set(
                self.key(state.tenant_id, state.session_id),
                payload,
                ex=self._ttl_seconds,
                nx=True,
            )
        except redis.RedisError as error:
            raise ActiveStateUnavailable("active state creation was not confirmed") from error
        if created is not True:
            raise ConcurrentStateUpdate(expected_version=0, observed_version=None)
        return envelope

    def compare_and_set(
        self,
        state: SalesState,
        *,
        expected_version: int,
        event_watermark: int,
        fencing_token: int,
    ) -> ActiveStateEnvelope:
        if state.version != expected_version + 1:
            raise ValueError("new state version must be exactly expected_version + 1")
        envelope = self._envelope(state, event_watermark=event_watermark, fencing_token=fencing_token)
        payload = self._serialize(envelope)
        try:
            result = self._client.eval(
                _COMPARE_AND_SET,
                1,
                self.key(state.tenant_id, state.session_id),
                expected_version,
                fencing_token,
                payload,
                self._ttl_seconds,
                state.version,
            )
        except redis.RedisError as error:
            raise ActiveStateUnavailable("active state update was not confirmed") from error
        if not isinstance(result, list) or len(result) != 2:
            raise ActiveStateUnavailable("active state update returned an invalid result")
        code, observed = (int(result[0]), int(result[1]))
        if code == 1:
            return envelope
        if code == -2:
            raise CorruptActiveState("active state was corrupt and has been discarded")
        if code == -3:
            raise StaleFencingToken(proposed_token=fencing_token, observed_token=observed)
        raise ConcurrentStateUpdate(
            expected_version=expected_version,
            observed_version=None if code == -1 else observed,
        )

    def delete(self, tenant_id: UUID, session_id: UUID) -> None:
        try:
            self._client.delete(self.key(tenant_id, session_id))
        except redis.RedisError as error:
            raise ActiveStateUnavailable("active state deletion was not confirmed") from error

    def block_processing(self, tenant_id: UUID, session_id: UUID, *, ttl_seconds: int = 604_800) -> None:
        if not 86_400 <= ttl_seconds <= 2_592_000:
            raise ValueError("privacy processing blocks must last between 1 and 30 days")
        try:
            self._client.eval(
                _BLOCK_PROCESSING,
                2,
                self.key(tenant_id, session_id),
                self.privacy_block_key(tenant_id, session_id),
                ttl_seconds,
            )
        except redis.RedisError as error:
            raise ActiveStateUnavailable("privacy processing block was not confirmed") from error

    def _envelope(
        self,
        state: SalesState,
        *,
        event_watermark: int,
        fencing_token: int,
    ) -> ActiveStateEnvelope:
        return ActiveStateEnvelope(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            state_version=state.version,
            event_watermark=event_watermark,
            fencing_token=fencing_token,
            updated_at=state.updated_at,
            state=state,
        )

    def _serialize(self, envelope: ActiveStateEnvelope) -> bytes:
        payload = envelope.model_dump_json().encode()
        if len(payload) > self._max_payload_bytes:
            raise ValueError("active state exceeds the configured payload limit")
        return payload

    def _discard_corrupt(self, key: str, observed_payload: bytes | str) -> None:
        try:
            self._client.eval(_COMPARE_AND_DELETE, 1, key, observed_payload)
        except redis.RedisError as error:
            raise ActiveStateUnavailable("corrupt active state could not be discarded") from error

    @staticmethod
    def _migrate_v0(document: dict[str, object]) -> dict[str, object]:
        """Upgrade the pre-watermark envelope without mutating its SalesState."""

        required = {"tenant_id", "session_id", "state_version", "updated_at", "state"}
        if not required.issubset(document):
            raise ValueError("legacy active state is missing required fields")
        return {
            **document,
            "schema_version": 1,
            "event_watermark": 0,
            "fencing_token": 0,
        }

    @staticmethod
    def _require_uuid7(value: UUID) -> None:
        if value.version != 7 or value.variant != "specified in RFC 4122":
            raise ValueError("Redis state keys require UUIDv7 identifiers")
