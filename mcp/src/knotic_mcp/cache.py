"""Per-tool result caching, freshness windows, stale-if-safe serving, and provider health.

Design principles (P3-T009):

- Every cache policy is per tool, not global: a side-effect tool is never cacheable at all, and
  a pricing tool is never served stale — a pricing failure must surface as ``PRICE_UNAVAILABLE``,
  never a plausible-looking but out-of-date number. Only read-only knowledge tools may serve a
  recently-expired ("stale-if-safe") result, and only while the underlying provider is unhealthy.
- A cache entry always carries its provenance (when it was computed, when it expires, and until
  when it may still be served stale) so a caller can tell fresh success from degraded service.
- Provider health is tracked independently of any one request: repeated failures open a circuit
  so the gateway fails fast (and, where eligible, falls back to a stale entry) instead of making
  every caller wait out the full timeout against a provider that is already down.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from .contracts import ToolEnvelope


@dataclass(frozen=True, slots=True)
class CachePolicy:
    cacheable: bool
    ttl_seconds: float = 0.0
    stale_grace_seconds: float = 0.0

    @property
    def allows_stale(self) -> bool:
        return self.cacheable and self.stale_grace_seconds > 0


# Deterministic, cheap-to-compute tools (lead.qualify, lead.next_action) are not cached: caching
# adds latency-hiding complexity without saving meaningful work. Side-effect tools are never
# cacheable — idempotency keys, not caches, govern their replay semantics.
TOOL_CACHE_POLICIES: dict[str, CachePolicy] = {
    "knowledge.search": CachePolicy(cacheable=True, ttl_seconds=60, stale_grace_seconds=300),
    "product.search": CachePolicy(cacheable=True, ttl_seconds=60, stale_grace_seconds=300),
    "product.get_feature": CachePolicy(cacheable=True, ttl_seconds=120, stale_grace_seconds=600),
    "product.get_integration": CachePolicy(cacheable=True, ttl_seconds=120, stale_grace_seconds=600),
    "competitor.compare": CachePolicy(cacheable=True, ttl_seconds=120, stale_grace_seconds=600),
    "security.get_information": CachePolicy(cacheable=True, ttl_seconds=120, stale_grace_seconds=600),
    # Pricing is cacheable for a short window (a quote's own ``valid_until`` already bounds its
    # correctness) but never stale-if-safe: a provider failure must surface as PRICE_UNAVAILABLE.
    "pricing.get_quote": CachePolicy(cacheable=True, ttl_seconds=30, stale_grace_seconds=0),
    "pricing.compare_plans": CachePolicy(cacheable=True, ttl_seconds=30, stale_grace_seconds=0),
}


def cache_key(*, tenant_id: UUID, tool: str, version: int, arguments: dict[str, object]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"mcp:cache:{tenant_id}:{tool}:{version}:{digest}"


@dataclass(frozen=True, slots=True)
class CacheEntry:
    envelope: ToolEnvelope
    cached_at: datetime
    fresh_until: datetime
    stale_until: datetime

    def is_fresh(self, *, at: datetime) -> bool:
        return at < self.fresh_until

    def is_within_stale_grace(self, *, at: datetime) -> bool:
        return self.fresh_until <= at < self.stale_until


class ToolResultCache(Protocol):
    def get(self, key: str) -> CacheEntry | None: ...
    def set(self, key: str, entry: CacheEntry) -> None: ...
    def invalidate(self, key: str) -> None: ...
    def invalidate_prefix(self, prefix: str) -> None: ...


class InMemoryToolResultCache:
    """Deterministic test double and single-process fallback; production uses Redis."""

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}

    def get(self, key: str) -> CacheEntry | None:
        return self._entries.get(key)

    def set(self, key: str, entry: CacheEntry) -> None:
        self._entries[key] = entry

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        for key in [item for item in self._entries if item.startswith(prefix)]:
            self._entries.pop(key, None)


class RedisToolResultCache:
    """Production result cache. Values are the same JSON the gateway already returns to callers."""

    def __init__(self, client: object, *, namespace: str = "mcp") -> None:
        self._client = client
        self._namespace = namespace

    def get(self, key: str) -> CacheEntry | None:
        raw = self._client.get(key)  # type: ignore[attr-defined]
        if raw is None:
            return None
        payload = json.loads(raw)
        return CacheEntry(
            envelope=ToolEnvelope.model_validate(payload["envelope"]),
            cached_at=datetime.fromisoformat(payload["cached_at"]),
            fresh_until=datetime.fromisoformat(payload["fresh_until"]),
            stale_until=datetime.fromisoformat(payload["stale_until"]),
        )

    def set(self, key: str, entry: CacheEntry) -> None:
        ttl = max(int((entry.stale_until - datetime.now(UTC)).total_seconds()), 1)
        payload = json.dumps(
            {
                "envelope": entry.envelope.model_dump(mode="json"),
                "cached_at": entry.cached_at.isoformat(),
                "fresh_until": entry.fresh_until.isoformat(),
                "stale_until": entry.stale_until.isoformat(),
            }
        )
        self._client.set(key, payload, ex=ttl)  # type: ignore[attr-defined]

    def invalidate(self, key: str) -> None:
        self._client.delete(key)  # type: ignore[attr-defined]

    def invalidate_prefix(self, prefix: str) -> None:
        for key in self._client.scan_iter(match=f"{prefix}*"):  # type: ignore[attr-defined]
            self._client.delete(key)  # type: ignore[attr-defined]


class ProviderHealthTracker:
    """Opens a fail-fast circuit for a named provider after repeated consecutive failures.

    Independent of any one caller's retry policy: this reflects whether the *provider itself*
    (a database, an embedding service, the pricing catalog) currently appears healthy, so it can
    be surfaced on ``/health/ready`` and used to decide whether a stale-if-safe fallback is
    appropriate even before a given call is attempted.
    """

    def __init__(self, *, failure_threshold: int = 3, open_seconds: float = 15.0) -> None:
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

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


__all__ = [
    "TOOL_CACHE_POLICIES",
    "CacheEntry",
    "CachePolicy",
    "InMemoryToolResultCache",
    "ProviderHealthTracker",
    "RedisToolResultCache",
    "ToolResultCache",
    "cache_key",
]
