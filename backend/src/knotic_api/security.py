"""Server-side browser authentication, CSRF, rate limiting, and replay encryption."""

from __future__ import annotations

import hashlib
import hmac
import base64
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import redis
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

_ENVIRONMENT = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


class SecurityDependencyUnavailable(RuntimeError):
    """A security decision could not be made safely."""


def derive_key(master_key: bytes, purpose: bytes) -> bytes:
    if len(master_key) < 32:
        raise ValueError("security master key must contain at least 32 bytes")
    return hmac.digest(master_key, b"knotic:v1:" + purpose, "sha256")


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    tenant_id: UUID
    actor_id: UUID
    actor_type: Literal["CUSTOMER", "HUMAN_AGENT"] = "HUMAN_AGENT"
    roles: tuple[Literal["ADMIN", "SUPERVISOR", "SALES_REP", "CUSTOMER"], ...] = ()
    display_name: str = "Operator"


class BrowserSessionValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    tenant_id: UUID
    actor_id: UUID
    actor_type: Literal["CUSTOMER", "HUMAN_AGENT"]
    roles: tuple[Literal["ADMIN", "SUPERVISOR", "SALES_REP", "CUSTOMER"], ...] = ()
    display_name: str = "Operator"
    csrf_hmac: str
    csrf_ciphertext: str
    expires_at: AwareDatetime

    @field_validator("tenant_id", "actor_id")
    @classmethod
    def validate_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("browser session identifiers must be UUIDv7")
        return value


class RedisBrowserSessionStore:
    """Opaque-cookie server sessions; cookie and CSRF values are never stored."""

    def __init__(self, client: redis.Redis, *, environment: str, master_key: bytes) -> None:
        if not _ENVIRONMENT.fullmatch(environment):
            raise ValueError("invalid session environment")
        self._client = client
        self._environment = environment
        self._lookup_key = derive_key(master_key, b"browser-session-lookup")
        self._csrf_key = derive_key(master_key, b"browser-session-csrf")
        self._csrf_cipher = ReplayCipher(derive_key(master_key, b"browser-session-csrf-envelope"))

    def put(
        self,
        *,
        cookie: str,
        csrf_token: str,
        actor: AuthenticatedActor,
        expires_at: datetime,
    ) -> None:
        self._validate_token(cookie, minimum=32, maximum=512)
        self._validate_token(csrf_token, minimum=32, maximum=512)
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("browser session expiry must be timezone-aware")
        ttl = int(expires_at.timestamp() - time.time())
        if ttl < 1:
            raise ValueError("browser session expiry must be in the future")
        value = BrowserSessionValue(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type,
            roles=actor.roles,
            display_name=actor.display_name,
            csrf_hmac=self._token_hmac(self._csrf_key, csrf_token),
            csrf_ciphertext=base64.urlsafe_b64encode(
                self._csrf_cipher.encrypt(csrf_token.encode(), associated_data=self._key(cookie).encode())
            ).decode(),
            expires_at=expires_at,
        )
        try:
            self._client.set(self._key(cookie), value.model_dump_json().encode(), ex=ttl)
        except redis.RedisError as error:
            raise SecurityDependencyUnavailable("browser session could not be stored") from error

    def authenticate(self, cookie: str | None) -> tuple[AuthenticatedActor, BrowserSessionValue] | None:
        if cookie is None:
            return None
        try:
            self._validate_token(cookie, minimum=32, maximum=512)
        except ValueError:
            return None
        key = self._key(cookie)
        try:
            raw = self._client.get(key)
        except redis.RedisError as error:
            raise SecurityDependencyUnavailable("browser session could not be verified") from error
        if not isinstance(raw, bytes):
            return None
        try:
            value = BrowserSessionValue.model_validate_json(raw)
        except ValueError:
            self._safe_delete(key)
            return None
        if value.expires_at <= datetime.now(UTC):
            self._safe_delete(key)
            return None
        return (
            AuthenticatedActor(
                tenant_id=value.tenant_id,
                actor_id=value.actor_id,
                actor_type=value.actor_type,
                roles=value.roles,
                display_name=value.display_name,
            ),
            value,
        )

    def revoke(self, cookie: str | None) -> None:
        if cookie is None:
            return
        try:
            self._validate_token(cookie, minimum=32, maximum=512)
        except ValueError:
            return
        self._safe_delete(self._key(cookie))

    def csrf_token(self, cookie: str | None, value: BrowserSessionValue) -> str:
        if cookie is None:
            raise SecurityDependencyUnavailable("browser session cookie is absent")
        try:
            ciphertext = base64.urlsafe_b64decode(value.csrf_ciphertext.encode())
            token = self._csrf_cipher.decrypt(ciphertext, associated_data=self._key(cookie).encode()).decode()
        except (ValueError, UnicodeDecodeError) as error:
            raise SecurityDependencyUnavailable("browser session CSRF token is invalid") from error
        if not self.verify_csrf(value, token):
            raise SecurityDependencyUnavailable("browser session CSRF token failed integrity validation")
        return token

    def verify_csrf(self, value: BrowserSessionValue, submitted_token: str | None) -> bool:
        if submitted_token is None:
            return False
        try:
            self._validate_token(submitted_token, minimum=32, maximum=512)
        except ValueError:
            return False
        actual = self._token_hmac(self._csrf_key, submitted_token)
        return secrets.compare_digest(actual, value.csrf_hmac)

    def _key(self, cookie: str) -> str:
        digest = self._token_hmac(self._lookup_key, cookie)
        return f"knotic:{self._environment}:browser-session:{digest}"

    @staticmethod
    def _token_hmac(key: bytes, token: str) -> str:
        return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _validate_token(token: str, *, minimum: int, maximum: int) -> None:
        if (
            not minimum <= len(token) <= maximum
            or not token.isascii()
            or any(ord(character) < 33 for character in token)
        ):
            raise ValueError("invalid opaque token")

    def _safe_delete(self, key: str) -> None:
        try:
            self._client.delete(key)
        except redis.RedisError as error:
            raise SecurityDependencyUnavailable("invalid browser session could not be removed") from error


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int


class RedisRateLimiter:
    def __init__(self, client: redis.Redis, *, environment: str, master_key: bytes) -> None:
        if not _ENVIRONMENT.fullmatch(environment):
            raise ValueError("invalid rate-limit environment")
        self._client = client
        self._environment = environment
        self._scope_key = derive_key(master_key, b"rate-limit-scope")

    def check(
        self, actor: AuthenticatedActor, *, action: str, limit: int, window_seconds: int = 60
    ) -> RateLimitDecision:
        if not 1 <= limit <= 10_000 or not 1 <= window_seconds <= 3600:
            raise ValueError("invalid rate-limit policy")
        now = int(time.time())
        window = now // window_seconds
        scope = f"{actor.tenant_id}:{actor.actor_id}:{action}"
        scope_hash = hmac.new(self._scope_key, scope.encode(), hashlib.sha256).hexdigest()
        key = f"knotic:{self._environment}:rate:{scope_hash}:{window}"
        try:
            result = self._client.eval(_RATE_LIMIT, 1, key, window_seconds + 2)
        except redis.RedisError as error:
            raise SecurityDependencyUnavailable("rate limit could not be evaluated") from error
        if not isinstance(result, list) or len(result) != 2:
            raise SecurityDependencyUnavailable("rate limit returned an invalid result")
        count = int(result[0])
        ttl = max(1, int(result[1]))
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            reset_epoch=now + ttl,
        )


class ReplayCipher:
    """AES-256-GCM envelope for idempotency response bodies."""

    def __init__(self, master_key: bytes) -> None:
        self._cipher = AESGCM(derive_key(master_key, b"idempotency-response-encryption"))

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return b"\x01" + nonce + self._cipher.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if len(ciphertext) < 30 or ciphertext[0] != 1:
            raise ValueError("unsupported replay ciphertext")
        return self._cipher.decrypt(ciphertext[1:13], ciphertext[13:], associated_data)
