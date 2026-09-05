from __future__ import annotations

import hashlib
import hmac

import pytest

from knotic_mcp.integration_security import (
    EgressPolicy,
    InMemoryWebhookSecretStore,
    ProviderQuota,
    ProviderQuotaGuard,
    WebhookVerificationError,
    WebhookVerifier,
)

_SECRET = b"test-secret-at-least-32-bytes-long"
_BODY = b'{"status":"delivered"}'
_NOW = 1_788_600_000


def _signature(*, body: bytes = _BODY, timestamp: int = _NOW, secret: bytes = _SECRET) -> str:
    digest = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_webhook_signature_accepts_authentic_delivery_once() -> None:
    verifier = WebhookVerifier(InMemoryWebhookSecretStore({"messaging": _SECRET}))
    verifier.verify(provider="messaging", delivery_id="delivery-1", signature_header=_signature(), body=_BODY, now=_NOW)
    with pytest.raises(WebhookVerificationError, match="already processed"):
        verifier.verify(
            provider="messaging", delivery_id="delivery-1", signature_header=_signature(), body=_BODY, now=_NOW
        )


@pytest.mark.parametrize(
    "header,body,now",
    (
        ("malformed", _BODY, _NOW),
        (_signature(body=b"other"), _BODY, _NOW),
        (_signature(timestamp=_NOW - 301), _BODY, _NOW),
        (_signature(timestamp=_NOW + 301), _BODY, _NOW),
    ),
)
def test_webhook_attacks_fail_closed(header: str, body: bytes, now: int) -> None:
    verifier = WebhookVerifier(InMemoryWebhookSecretStore({"messaging": _SECRET}))
    with pytest.raises(WebhookVerificationError):
        verifier.verify(provider="messaging", delivery_id="attack", signature_header=header, body=body, now=now)


def test_quota_load_is_bounded_and_recovers_after_window() -> None:
    guard = ProviderQuotaGuard({"crm": ProviderQuota(requests=100, window_seconds=60)})
    assert all(guard.check(tenant_scope="tenant-a", provider="crm", now=0).allowed for _ in range(100))
    denied = guard.check(tenant_scope="tenant-a", provider="crm", now=0)
    assert not denied.allowed
    assert denied.retry_after_seconds == 60
    assert guard.check(tenant_scope="tenant-a", provider="crm", now=60).allowed
    assert guard.check(tenant_scope="tenant-b", provider="crm", now=0).allowed


def test_unknown_provider_and_non_allowlisted_egress_fail_closed() -> None:
    quota = ProviderQuotaGuard({})
    assert not quota.check(tenant_scope="tenant-a", provider="unknown", now=0).allowed
    policy = EgressPolicy({"crm": frozenset({"https://api.crm.example"})})
    assert policy.authorize(provider="crm", url="https://api.crm.example/v1/leads").endswith("/v1/leads")
    for url in (
        "http://api.crm.example/v1/leads",
        "https://api.crm.example.evil.test/v1/leads",
        "https://127.0.0.1/internal",
        "https://user:password@api.crm.example/v1/leads",
    ):
        with pytest.raises(ValueError):
            policy.authorize(provider="crm", url=url)
