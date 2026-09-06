from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from knotic_mcp.handoff import (
    Agent,
    AgentDirectory,
    HandoffContext,
    HandoffPriority,
    HandoffService,
    HandoffStatus,
    InMemoryHandoffProvider,
)
from knotic_mcp.integrations import IntegrationProviderError

_NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def _context() -> HandoffContext:
    return HandoffContext(
        company="Acme",
        users=500,
        use_cases=("sales automation",),
        integrations=("CRM",),
        competitors=("OtherCo",),
        objections=("Security review required",),
        qualification_score=82,
        latest_request="Talk to security",
        summary="Enterprise buyer needs a security specialist.",
    )


def test_handoff_requires_complete_fr13_context() -> None:
    payload = _context().model_dump()
    del payload["latest_request"]
    with pytest.raises(ValidationError):
        HandoffContext.model_validate(payload)


def test_unavailable_agent_uses_fallback_queue_without_claiming_acceptance() -> None:
    service = HandoffService(provider=InMemoryHandoffProvider(), directory=AgentDirectory())
    record = service.request(
        tenant_id=uuid4(), reason="security review", priority=HandoffPriority.URGENT, idempotency_key="a" * 16, now=_NOW
    )
    assert record.status == HandoffStatus.QUEUED
    assert "queued" in record.customer_message
    assert "accepted" not in record.customer_message


def test_acknowledgement_is_required_before_connected_status() -> None:
    tenant_id = uuid4()
    service = HandoffService(
        provider=InMemoryHandoffProvider(),
        directory=AgentDirectory((Agent("security-1", frozenset({"SECURITY"})),)),
    )
    requested = service.request(
        tenant_id=tenant_id,
        reason="legal and security",
        priority=HandoffPriority.URGENT,
        idempotency_key="b" * 16,
        now=_NOW,
    )
    assert requested.status == HandoffStatus.REQUESTED
    connected = service.acknowledge(
        tenant_id=tenant_id, handoff_id=requested.handoff_id, agent_id="security-1", at=_NOW + timedelta(seconds=10)
    )
    assert connected.status == HandoffStatus.CONNECTED
    assert "accepted" in connected.customer_message


def test_context_transfer_is_pending_before_ack_and_idempotent_after_confirmation() -> None:
    tenant_id = uuid4()
    provider = InMemoryHandoffProvider()
    service = HandoffService(provider=provider, directory=AgentDirectory((Agent("sales-1", frozenset({"SALES"})),)))
    requested = service.request(
        tenant_id=tenant_id,
        reason="explicit request",
        priority=HandoffPriority.HIGH,
        idempotency_key="c" * 16,
        now=_NOW,
    )
    pending = service.transfer_context(
        tenant_id=tenant_id,
        handoff_id=requested.handoff_id,
        context=_context(),
        idempotency_key="d" * 16,
        now=_NOW,
    )
    assert pending.status == HandoffStatus.PENDING_CONFIRMATION
    assert not provider.transfers

    second = service.request(
        tenant_id=tenant_id,
        reason="explicit request",
        priority=HandoffPriority.HIGH,
        idempotency_key="e" * 16,
        now=_NOW,
    )
    service.acknowledge(tenant_id=tenant_id, handoff_id=second.handoff_id, agent_id="sales-1", at=_NOW)
    transferred = service.transfer_context(
        tenant_id=tenant_id,
        handoff_id=second.handoff_id,
        context=_context(),
        idempotency_key="f" * 16,
        now=_NOW,
    )
    assert transferred.status == HandoffStatus.TRANSFERRED
    assert len(provider.transfers) == 1


def test_wrong_agent_and_duplicate_request_are_safe() -> None:
    tenant_id = uuid4()
    provider = InMemoryHandoffProvider()
    service = HandoffService(provider=provider, directory=AgentDirectory((Agent("sales-1", frozenset({"SALES"})),)))
    first = service.request(
        tenant_id=tenant_id,
        reason="explicit request",
        priority=HandoffPriority.HIGH,
        idempotency_key="e" * 16,
        now=_NOW,
    )
    duplicate = service.request(
        tenant_id=tenant_id,
        reason="explicit request",
        priority=HandoffPriority.HIGH,
        idempotency_key="e" * 16,
        now=_NOW,
    )
    assert duplicate == first
    with pytest.raises(IntegrationProviderError) as error:
        service.acknowledge(tenant_id=tenant_id, handoff_id=first.handoff_id, agent_id="wrong-agent", at=_NOW)
    assert error.value.code == "CONFLICT"
