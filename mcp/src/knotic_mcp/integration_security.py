"""Webhook authentication, provider quotas, and egress controls (P5-T010)."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit


class WebhookVerificationError(ValueError):
    """A safe, non-secret webhook rejection."""


class WebhookSecretStore(Protocol):
    def get(self, provider: str) -> bytes | None: ...


class InMemoryWebhookSecretStore:
    def __init__(self, values: dict[str, bytes]) -> None:
        self._values = values

    def get(self, provider: str) -> bytes | None:
        return self._values.get(provider)


class WebhookVerifier:
    """Verifies timestamped HMAC-SHA256 signatures and rejects replayed deliveries."""

    def __init__(self, secrets: WebhookSecretStore, *, tolerance_seconds: int = 300) -> None:
        if tolerance_seconds < 1:
            raise ValueError("tolerance_seconds must be positive")
        self._secrets = secrets
        self._tolerance = tolerance_seconds
        self._seen: dict[tuple[str, str], int] = {}

    def verify(self, *, provider: str, delivery_id: str, signature_header: str, body: bytes, now: int) -> None:
        secret = self._secrets.get(provider)
        if secret is None:
            raise WebhookVerificationError("webhook provider is not configured")
        timestamp, signatures = self._parse(signature_header)
        if abs(now - timestamp) > self._tolerance:
            raise WebhookVerificationError("webhook timestamp is outside the accepted window")
        self._purge(now)
        replay_key = (provider, delivery_id)
        if replay_key in self._seen:
            raise WebhookVerificationError("webhook delivery was already processed")
        expected = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            raise WebhookVerificationError("webhook signature is invalid")
        self._seen[replay_key] = timestamp

    @staticmethod
    def _parse(header: str) -> tuple[int, tuple[str, ...]]:
        parts: dict[str, list[str]] = defaultdict(list)
        for item in header.split(","):
            key, separator, value = item.strip().partition("=")
            if separator and value:
                parts[key].append(value)
        try:
            timestamp = int(parts["t"][0])
        except (KeyError, IndexError, ValueError) as error:
            raise WebhookVerificationError("webhook signature header is malformed") from error
        signatures = tuple(parts.get("v1", ()))
        if not signatures:
            raise WebhookVerificationError("webhook signature header is malformed")
        return timestamp, signatures

    def _purge(self, now: int) -> None:
        oldest = now - self._tolerance
        self._seen = {key: timestamp for key, timestamp in self._seen.items() if timestamp >= oldest}


@dataclass(frozen=True, slots=True)
class ProviderQuota:
    requests: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.requests < 1 or self.window_seconds < 1:
            raise ValueError("provider quota bounds must be positive")


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int | None


class ProviderQuotaGuard:
    def __init__(self, policies: dict[str, ProviderQuota]) -> None:
        self._policies = policies
        self._hits: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, *, tenant_scope: str, provider: str, now: float | None = None) -> QuotaDecision:
        policy = self._policies.get(provider)
        if policy is None:
            return QuotaDecision(False, 0, None)
        current = time.monotonic() if now is None else now
        hits = self._hits[(tenant_scope, provider)]
        while hits and current - hits[0] >= policy.window_seconds:
            hits.popleft()
        if len(hits) >= policy.requests:
            retry = max(1, int(policy.window_seconds - (current - hits[0])))
            return QuotaDecision(False, 0, retry)
        hits.append(current)
        return QuotaDecision(True, policy.requests - len(hits), None)


class EgressPolicy:
    """Exact HTTPS origin allowlist; redirects must be re-authorized independently."""

    def __init__(self, allowed_origins: dict[str, frozenset[str]]) -> None:
        self._origins = {
            provider: frozenset(origin.rstrip("/").casefold() for origin in origins)
            for provider, origins in allowed_origins.items()
        }

    def authorize(self, *, provider: str, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("provider egress requires an HTTPS origin without user information")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("provider egress cannot target a literal IP address")
        origin = f"https://{parsed.hostname.casefold()}"
        if parsed.port is not None:
            origin = f"{origin}:{parsed.port}"
        if origin not in self._origins.get(provider, frozenset()):
            raise ValueError("provider egress origin is not allowlisted")
        return url


__all__ = [
    "EgressPolicy",
    "InMemoryWebhookSecretStore",
    "ProviderQuota",
    "ProviderQuotaGuard",
    "QuotaDecision",
    "WebhookSecretStore",
    "WebhookVerificationError",
    "WebhookVerifier",
]
