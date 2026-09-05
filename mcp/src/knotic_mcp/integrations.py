"""Shared integration adapter framework (P5-T001).

Every CRM, calendar, and handoff adapter is built from these primitives rather than rolling its
own retry loop or credential lookup, so the properties this module gives once -- least-privileged,
tenant-isolated credentials; a call that cannot be mistaken for success when it actually failed;
bounded, jittered retries that never retry a write of unknown outcome; a per-provider circuit that
fails fast instead of queueing every caller behind a provider that is already down; and an
idempotent-write helper that replays a prior result instead of re-executing a side effect -- hold
for all of them.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import UUID

from .contracts import ToolEnvelope

T = TypeVar("T")


class IntegrationProviderError(Exception):
    """A normalized provider failure.

    ``code`` must be one of the MCP contract's ``common_failures`` so a handler can surface it as
    a :class:`~knotic_mcp.contracts.ToolEnvelope` failure without inventing a new error taxonomy
    per provider. ``retryable`` is the provider's own claim about whether the *same* request may
    be safely retried; a provider that cannot tell whether a write landed must set it to ``False``.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class IntegrationCredential:
    """A least-privileged credential scoped to exactly one tenant and one provider.

    Lookups are always keyed by ``(tenant_id, provider)`` so a caller can never be handed another
    tenant's token by accident, and a credential for one provider (say, the CRM) is never usable
    against another (the calendar).
    """

    tenant_id: UUID
    provider: str
    access_token: str
    expires_at: datetime | None = None
    sandbox: bool = True

    def is_expired(self, *, at: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= at


class CredentialStore(Protocol):
    def get(self, *, tenant_id: UUID, provider: str) -> IntegrationCredential | None: ...


class InMemoryCredentialStore:
    """Deterministic test double and sandbox default; production stores credentials encrypted
    and never logs or returns the raw token outside the adapter that immediately uses it."""

    def __init__(self) -> None:
        self._credentials: dict[tuple[UUID, str], IntegrationCredential] = {}

    def put(self, credential: IntegrationCredential) -> None:
        self._credentials[(credential.tenant_id, credential.provider)] = credential

    def get(self, *, tenant_id: UUID, provider: str) -> IntegrationCredential | None:
        return self._credentials.get((tenant_id, provider))


class CredentialUnavailable(Exception):
    """No usable (present, unexpired) credential exists for this tenant and provider."""


def require_credential(store: CredentialStore, *, tenant_id: UUID, provider: str, now: datetime) -> IntegrationCredential:
    credential = store.get(tenant_id=tenant_id, provider=provider)
    if credential is None or credential.is_expired(at=now):
        raise CredentialUnavailable(f"no usable credential for tenant {tenant_id} and provider {provider}")
    return credential


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("delay bounds must be non-negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be at least base_delay_seconds")

    def delay_seconds(self, attempt: int, *, rng: random.Random) -> float:
        """Full-jitter exponential backoff; ``attempt`` is the 1-indexed attempt that just failed."""
        if attempt < 1:
            raise ValueError("attempt must be at least one")
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** (attempt - 1)))
        return rng.uniform(0, ceiling)


class CircuitBreakerOpen(IntegrationProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__("DEPENDENCY_UNAVAILABLE", f"circuit open for provider {provider}", retryable=True)
        self.provider = provider


class CircuitBreaker:
    """Per-provider fail-fast circuit, independent of any one call's own retry policy.

    Mirrors :class:`knotic_mcp.cache.ProviderHealthTracker` deliberately -- adapters live in a
    different module from the gateway's own knowledge/pricing provider health, but the behavior
    (open after N consecutive failures, half-open again after a cool-down) should be identical.
    """

    def __init__(self, *, failure_threshold: int = 3, open_seconds: float = 15.0) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least one")
        if open_seconds <= 0:
            raise ValueError("open_seconds must be positive")
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def before_call(self, provider: str) -> None:
        if self.is_open(provider):
            raise CircuitBreakerOpen(provider)

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0
        self._open_until.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= self._failure_threshold:
            self._open_until[provider] = time.monotonic() + self._open_seconds

    def is_open(self, provider: str) -> bool:
        deadline = self._open_until.get(provider)
        return deadline is not None and time.monotonic() < deadline

    def status(self) -> dict[str, str]:
        return {provider: "degraded" if self.is_open(provider) else "ok" for provider in self._failures}


def call_with_resilience(
    *,
    provider: str,
    breaker: CircuitBreaker,
    retry: RetryPolicy,
    deadline_at: datetime,
    operation: Callable[[], T],
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> T:
    """Runs ``operation`` behind a shared provider circuit breaker, jittered retries, and a hard
    deadline.

    Only an :class:`IntegrationProviderError` marked ``retryable=True`` triggers another attempt;
    anything else -- including a non-retryable provider error, or any exception the provider
    itself did not classify -- propagates immediately, so a write whose outcome is actually
    unknown is never silently retried as if a retry were free.
    """
    rng = rng or random.Random()
    attempt = 1
    while True:
        breaker.before_call(provider)
        if datetime.now(UTC) >= deadline_at:
            raise IntegrationProviderError("TIMEOUT", "The integration deadline has expired.", retryable=False)
        try:
            result = operation()
        except IntegrationProviderError as error:
            breaker.record_failure(provider)
            if not error.retryable or attempt >= retry.max_attempts:
                raise
            sleep(retry.delay_seconds(attempt, rng=rng))
            attempt += 1
            continue
        breaker.record_success(provider)
        return result


def request_fingerprint(arguments: dict[str, object]) -> str:
    """A stable hash of a tool call's arguments, used to detect a reused idempotency key applied
    to genuinely different input (see :func:`idempotent_write`)."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyConflict(Exception):
    """A previously used idempotency key was reused with different request arguments."""


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    envelope: ToolEnvelope
    request_fingerprint: str


class IdempotencyStore(Protocol):
    def get(self, *, tenant_id: UUID, tool: str, idempotency_key: str) -> IdempotentResult | None: ...
    def put(self, *, tenant_id: UUID, tool: str, idempotency_key: str, result: IdempotentResult) -> None: ...


class InMemoryIdempotencyStore:
    """Deterministic test double and single-process fallback; production keys this off Redis the
    same way :class:`knotic_mcp.cache.RedisToolResultCache` backs the read-side result cache."""

    def __init__(self) -> None:
        self._results: dict[tuple[UUID, str, str], IdempotentResult] = {}

    def get(self, *, tenant_id: UUID, tool: str, idempotency_key: str) -> IdempotentResult | None:
        return self._results.get((tenant_id, tool, idempotency_key))

    def put(self, *, tenant_id: UUID, tool: str, idempotency_key: str, result: IdempotentResult) -> None:
        self._results[(tenant_id, tool, idempotency_key)] = result


def idempotent_write(
    *,
    store: IdempotencyStore,
    tenant_id: UUID,
    tool: str,
    idempotency_key: str,
    request_fingerprint: str,
    perform: Callable[[], ToolEnvelope],
) -> ToolEnvelope:
    """Replays the prior result for a reused idempotency key instead of re-executing the write.

    A key reused with a *different* request fingerprint raises :class:`IdempotencyConflict`
    (the caller maps this to the contract's ``IDEMPOTENCY_CONFLICT`` failure) rather than either
    replaying a mismatched result or silently performing a second, different write under the same
    key.
    """
    existing = store.get(tenant_id=tenant_id, tool=tool, idempotency_key=idempotency_key)
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise IdempotencyConflict("idempotency key was reused with different arguments")
        return existing.envelope
    envelope = perform()
    store.put(
        tenant_id=tenant_id,
        tool=tool,
        idempotency_key=idempotency_key,
        result=IdempotentResult(envelope=envelope, request_fingerprint=request_fingerprint),
    )
    return envelope


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "CredentialStore",
    "CredentialUnavailable",
    "IdempotencyConflict",
    "IdempotencyStore",
    "IdempotentResult",
    "InMemoryCredentialStore",
    "InMemoryIdempotencyStore",
    "IntegrationCredential",
    "IntegrationProviderError",
    "RetryPolicy",
    "call_with_resilience",
    "idempotent_write",
    "request_fingerprint",
    "require_credential",
]
