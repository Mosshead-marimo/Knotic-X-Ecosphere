from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from knotic_mcp.followup import (
    ApprovedTemplate,
    ConsentGrant,
    DeliveryStatus,
    FollowupChannel,
    FollowupService,
    InMemoryConsentStore,
    InMemoryFollowupProvider,
    TemplateCatalog,
)
from knotic_mcp.integrations import IntegrationProviderError

_NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
_CONTENT = "Thanks for speaking with us. Reply STOP to unsubscribe."


def _service(*, consented: bool = True) -> tuple[FollowupService, InMemoryFollowupProvider, UUID, UUID]:
    tenant_id, lead_id = uuid4(), uuid4()
    consent = InMemoryConsentStore()
    if consented:
        consent.put(ConsentGrant(tenant_id, lead_id, FollowupChannel.EMAIL, _NOW - timedelta(days=1)))
    provider = InMemoryFollowupProvider()
    service = FollowupService(
        provider=provider,
        consent=consent,
        templates=TemplateCatalog(
            (ApprovedTemplate.from_content(template_id="email-v1", channel=FollowupChannel.EMAIL, content=_CONTENT),)
        ),
    )
    return service, provider, tenant_id, lead_id


def _create(
    service: FollowupService,
    tenant_id: UUID,
    lead_id: UUID,
    *,
    key: str = "a" * 16,
    now: datetime = _NOW,
):
    return service.create(
        tenant_id=tenant_id,
        lead_id=lead_id,
        channel=FollowupChannel.EMAIL,
        scheduled_at=_NOW + timedelta(hours=1),
        content=_CONTENT,
        idempotency_key=key,
        now=now,
    )


def test_enqueue_is_pending_until_a_delivery_callback() -> None:
    service, _provider, tenant_id, lead_id = _service()
    record = _create(service, tenant_id, lead_id)
    assert record.status == DeliveryStatus.QUEUED
    delivered = service.apply_callback(
        tenant_id=record.tenant_id,
        provider_reference=str(record.provider_reference),
        callback_id="callback-1",
        status=DeliveryStatus.DELIVERED,
        occurred_at=_NOW + timedelta(hours=2),
    )
    assert delivered.status == DeliveryStatus.DELIVERED
    assert [event.event for event in service.audit_events] == ["provider.accepted", "provider.callback"]


def test_create_requires_active_channel_consent_and_approved_content() -> None:
    service, _provider, tenant_id, lead_id = _service(consented=False)
    with pytest.raises(IntegrationProviderError, match="consent") as no_consent:
        _create(service, tenant_id, lead_id)
    assert no_consent.value.code == "POLICY_DENIED"

    service, _provider, tenant_id, lead_id = _service()
    with pytest.raises(IntegrationProviderError, match="approved template") as unapproved:
        service.create(
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel=FollowupChannel.EMAIL,
            scheduled_at=_NOW + timedelta(hours=1),
            content="Unapproved content",
            idempotency_key="b" * 16,
            now=_NOW,
        )
    assert unapproved.value.code == "POLICY_DENIED"


def test_duplicate_submission_and_callback_are_idempotent() -> None:
    service, provider, tenant_id, lead_id = _service()
    first = _create(service, tenant_id, lead_id)
    second = _create(service, tenant_id, lead_id)
    assert second == first
    assert len(provider.submissions) == 1
    first_callback = service.apply_callback(
        tenant_id=first.tenant_id,
        provider_reference=str(first.provider_reference),
        callback_id="callback-1",
        status=DeliveryStatus.SENT,
        occurred_at=_NOW + timedelta(hours=2),
    )
    duplicate = service.apply_callback(
        tenant_id=first.tenant_id,
        provider_reference=str(first.provider_reference),
        callback_id="callback-1",
        status=DeliveryStatus.SENT,
        occurred_at=_NOW + timedelta(hours=2),
    )
    assert duplicate == first_callback


def test_unsubscribe_revokes_consent_and_cancels_pending_delivery() -> None:
    service, _provider, tenant_id, lead_id = _service()
    record = _create(service, tenant_id, lead_id)
    assert (
        service.unsubscribe(
            tenant_id=record.tenant_id, lead_id=record.lead_id, channel=record.channel, at=_NOW + timedelta(minutes=5)
        )
        == 1
    )
    with pytest.raises(IntegrationProviderError, match="consent"):
        _create(service, tenant_id, lead_id, key="c" * 16, now=_NOW + timedelta(minutes=10))


def test_bounce_is_terminal_and_conflicting_callbacks_are_rejected() -> None:
    service, _provider, tenant_id, lead_id = _service()
    record = _create(service, tenant_id, lead_id)
    bounced = service.apply_callback(
        tenant_id=record.tenant_id,
        provider_reference=str(record.provider_reference),
        callback_id="bounce-1",
        status=DeliveryStatus.BOUNCED,
        occurred_at=_NOW + timedelta(hours=2),
    )
    assert bounced.status == DeliveryStatus.BOUNCED
    with pytest.raises(IntegrationProviderError) as late_delivery:
        service.apply_callback(
            tenant_id=record.tenant_id,
            provider_reference=str(record.provider_reference),
            callback_id="delivery-2",
            status=DeliveryStatus.DELIVERED,
            occurred_at=_NOW + timedelta(hours=3),
        )
    assert late_delivery.value.code == "CONFLICT"


def test_past_schedule_is_rejected_before_provider_submission() -> None:
    service, provider, tenant_id, lead_id = _service()
    with pytest.raises(IntegrationProviderError) as error:
        service.create(
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel=FollowupChannel.EMAIL,
            scheduled_at=_NOW - timedelta(seconds=1),
            content=_CONTENT,
            idempotency_key="d" * 16,
            now=_NOW,
        )
    assert error.value.code == "INVALID_ARGUMENT"
    assert not provider.submissions
