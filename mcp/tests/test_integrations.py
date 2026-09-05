from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knotic_mcp.contracts import ToolEnvelope, ToolStatus
from knotic_mcp.integrations import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CredentialUnavailable,
    IdempotencyConflict,
    IdempotentResult,
    InMemoryCredentialStore,
    InMemoryIdempotencyStore,
    IntegrationCredential,
    IntegrationProviderError,
    RetryPolicy,
    call_with_resilience,
    idempotent_write,
    request_fingerprint,
    require_credential,
)

_TENANT = uuid4()
_NOW = datetime(2026, 9, 5, tzinfo=UTC)
# call_with_resilience checks the *real* wall clock (datetime.now(UTC)), not this module's fixed
# _NOW fixture, so a "comfortably in the future" deadline must be derived from the real clock too.
_FAR_DEADLINE = datetime.now(UTC) + timedelta(minutes=5)


def _envelope() -> ToolEnvelope:
    return ToolEnvelope(
        tool_call_id=uuid4(),
        tool="crm.get_lead",
        version=1,
        status=ToolStatus.SUCCEEDED,
        data={"found": False, "lead": None},
        started_at=_NOW,
        completed_at=_NOW,
    )


def test_require_credential_rejects_missing_and_expired() -> None:
    store = InMemoryCredentialStore()
    with pytest.raises(CredentialUnavailable):
        require_credential(store, tenant_id=_TENANT, provider="crm", now=_NOW)

    expired_marker = "expired-value"
    store.put(
        IntegrationCredential(
            tenant_id=_TENANT, provider="crm", access_token=expired_marker, expires_at=_NOW - timedelta(seconds=1)
        )
    )
    with pytest.raises(CredentialUnavailable):
        require_credential(store, tenant_id=_TENANT, provider="crm", now=_NOW)


def test_require_credential_isolates_by_tenant_and_provider() -> None:
    store = InMemoryCredentialStore()
    value = "value"
    store.put(IntegrationCredential(tenant_id=_TENANT, provider="crm", access_token=value))
    other_tenant, other_provider = uuid4(), "calendar"
    with pytest.raises(CredentialUnavailable):
        require_credential(store, tenant_id=other_tenant, provider="crm", now=_NOW)
    with pytest.raises(CredentialUnavailable):
        require_credential(store, tenant_id=_TENANT, provider=other_provider, now=_NOW)
    assert require_credential(store, tenant_id=_TENANT, provider="crm", now=_NOW).access_token == value


def test_retry_policy_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="non-negative"):
        RetryPolicy(base_delay_seconds=-1)
    with pytest.raises(ValueError, match="max_delay_seconds"):
        RetryPolicy(base_delay_seconds=2, max_delay_seconds=1)


def test_call_with_resilience_retries_only_retryable_failures_then_succeeds() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise IntegrationProviderError("TIMEOUT", "slow provider", retryable=True)
        return "ok"

    result = call_with_resilience(
        provider="crm_provider",
        breaker=CircuitBreaker(),
        retry=RetryPolicy(max_attempts=5, base_delay_seconds=0, max_delay_seconds=0),
        deadline_at=_FAR_DEADLINE,
        operation=operation,
        sleep=lambda _seconds: None,
    )
    assert result == "ok"
    assert attempts == 3


def test_call_with_resilience_never_retries_a_non_retryable_error() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise IntegrationProviderError("CONFLICT", "version mismatch", retryable=False)

    with pytest.raises(IntegrationProviderError, match="version mismatch"):
        call_with_resilience(
            provider="crm_provider",
            breaker=CircuitBreaker(),
            retry=RetryPolicy(max_attempts=5, base_delay_seconds=0, max_delay_seconds=0),
            deadline_at=_FAR_DEADLINE,
            operation=operation,
            sleep=lambda _seconds: None,
        )
    assert attempts == 1


def test_call_with_resilience_raises_timeout_past_the_deadline() -> None:
    with pytest.raises(IntegrationProviderError, match="deadline"):
        call_with_resilience(
            provider="crm_provider",
            breaker=CircuitBreaker(),
            retry=RetryPolicy(),
            deadline_at=_NOW - timedelta(seconds=1),
            operation=lambda: "unreachable",
            sleep=lambda _seconds: None,
        )


def test_circuit_breaker_opens_after_threshold_and_fails_fast() -> None:
    breaker = CircuitBreaker(failure_threshold=2, open_seconds=60)
    breaker.record_failure("crm_provider")
    assert not breaker.is_open("crm_provider")
    breaker.record_failure("crm_provider")
    assert breaker.is_open("crm_provider")
    with pytest.raises(CircuitBreakerOpen):
        breaker.before_call("crm_provider")
    breaker.record_success("crm_provider")
    assert not breaker.is_open("crm_provider")


def test_circuit_breaker_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError, match="open_seconds"):
        CircuitBreaker(open_seconds=0)


def test_idempotent_write_replays_prior_result_without_reperforming() -> None:
    store = InMemoryIdempotencyStore()
    calls = 0

    def perform() -> ToolEnvelope:
        nonlocal calls
        calls += 1
        return _envelope()

    fingerprint = request_fingerprint({"lookup": {"email": "a@example.com"}})
    first = idempotent_write(
        store=store,
        tenant_id=_TENANT,
        tool="crm.get_lead",
        idempotency_key="key-1",
        request_fingerprint=fingerprint,
        perform=perform,
    )
    second = idempotent_write(
        store=store,
        tenant_id=_TENANT,
        tool="crm.get_lead",
        idempotency_key="key-1",
        request_fingerprint=fingerprint,
        perform=perform,
    )
    assert first == second
    assert calls == 1


def test_idempotent_write_rejects_a_reused_key_with_different_arguments() -> None:
    store = InMemoryIdempotencyStore()
    original_fingerprint = request_fingerprint({"lead": {"company": "A"}})
    store.put(
        tenant_id=_TENANT,
        tool="crm.create_lead",
        idempotency_key="key-1",
        result=IdempotentResult(envelope=_envelope(), request_fingerprint=original_fingerprint),
    )
    with pytest.raises(IdempotencyConflict):
        idempotent_write(
            store=store,
            tenant_id=_TENANT,
            tool="crm.create_lead",
            idempotency_key="key-1",
            request_fingerprint=request_fingerprint({"lead": {"company": "B"}}),
            perform=lambda: _envelope(),
        )


def test_request_fingerprint_is_stable_and_order_independent() -> None:
    first = request_fingerprint({"a": 1, "b": 2})
    second = request_fingerprint({"b": 2, "a": 1})
    assert first == second
    assert first != request_fingerprint({"a": 1, "b": 3})
