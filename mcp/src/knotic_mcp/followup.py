"""Consent-aware, provider-confirmed follow-up delivery (P5-T006)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .integrations import IntegrationProviderError


class FollowupChannel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    TASK = "TASK"


class DeliveryStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    BOUNCED = "BOUNCED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_TERMINAL = frozenset(
    {DeliveryStatus.DELIVERED, DeliveryStatus.BOUNCED, DeliveryStatus.FAILED, DeliveryStatus.CANCELLED}
)
_CALLBACK_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.SCHEDULED: frozenset({DeliveryStatus.QUEUED, DeliveryStatus.CANCELLED}),
    DeliveryStatus.QUEUED: frozenset(
        {
            DeliveryStatus.SENT,
            DeliveryStatus.DELIVERED,
            DeliveryStatus.BOUNCED,
            DeliveryStatus.FAILED,
            DeliveryStatus.CANCELLED,
        }
    ),
    DeliveryStatus.SENT: frozenset(
        {DeliveryStatus.DELIVERED, DeliveryStatus.BOUNCED, DeliveryStatus.FAILED, DeliveryStatus.CANCELLED}
    ),
    DeliveryStatus.DELIVERED: frozenset(),
    DeliveryStatus.BOUNCED: frozenset(),
    DeliveryStatus.FAILED: frozenset(),
    DeliveryStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ConsentGrant:
    tenant_id: UUID
    lead_id: UUID
    channel: FollowupChannel
    granted_at: datetime
    revoked_at: datetime | None = None

    def active_at(self, at: datetime) -> bool:
        return self.granted_at <= at and (self.revoked_at is None or self.revoked_at > at)


class ConsentStore(Protocol):
    def get(self, *, tenant_id: UUID, lead_id: UUID, channel: FollowupChannel) -> ConsentGrant | None: ...


class InMemoryConsentStore:
    def __init__(self) -> None:
        self._grants: dict[tuple[UUID, UUID, FollowupChannel], ConsentGrant] = {}

    def put(self, grant: ConsentGrant) -> None:
        self._grants[(grant.tenant_id, grant.lead_id, grant.channel)] = grant

    def revoke(self, *, tenant_id: UUID, lead_id: UUID, channel: FollowupChannel, at: datetime) -> None:
        existing = self.get(tenant_id=tenant_id, lead_id=lead_id, channel=channel)
        if existing is not None:
            self.put(replace(existing, revoked_at=at))

    def get(self, *, tenant_id: UUID, lead_id: UUID, channel: FollowupChannel) -> ConsentGrant | None:
        return self._grants.get((tenant_id, lead_id, channel))


@dataclass(frozen=True, slots=True)
class ApprovedTemplate:
    template_id: str
    channel: FollowupChannel
    content_sha256: str

    @classmethod
    def from_content(cls, *, template_id: str, channel: FollowupChannel, content: str) -> ApprovedTemplate:
        return cls(template_id, channel, hashlib.sha256(content.strip().encode()).hexdigest())

    def matches(self, content: str) -> bool:
        return hashlib.sha256(content.strip().encode()).hexdigest() == self.content_sha256


class TemplateCatalog:
    def __init__(self, templates: tuple[ApprovedTemplate, ...] = ()) -> None:
        self._templates = templates

    def match(self, *, channel: FollowupChannel, content: str) -> ApprovedTemplate | None:
        return next((item for item in self._templates if item.channel == channel and item.matches(content)), None)


@dataclass(frozen=True, slots=True)
class ProviderSubmission:
    provider_reference: str
    accepted: bool


class FollowupProviderPort(Protocol):
    def submit(
        self,
        *,
        tenant_id: UUID,
        followup_id: UUID,
        lead_id: UUID,
        channel: FollowupChannel,
        scheduled_at: datetime,
        content: str,
        provider_idempotency_key: str,
    ) -> ProviderSubmission: ...


class InMemoryFollowupProvider:
    """Sandbox provider that confirms queue acceptance, never final delivery."""

    def __init__(self) -> None:
        self.submissions: dict[tuple[UUID, str], ProviderSubmission] = {}

    def submit(
        self,
        *,
        tenant_id: UUID,
        followup_id: UUID,
        lead_id: UUID,
        channel: FollowupChannel,
        scheduled_at: datetime,
        content: str,
        provider_idempotency_key: str,
    ) -> ProviderSubmission:
        key = (tenant_id, provider_idempotency_key)
        existing = self.submissions.get(key)
        if existing is not None:
            return existing
        result = ProviderSubmission(str(uuid5(NAMESPACE_URL, f"followup:{tenant_id}:{followup_id}")), True)
        self.submissions[key] = result
        return result


@dataclass(frozen=True, slots=True)
class Followup:
    followup_id: UUID
    tenant_id: UUID
    lead_id: UUID
    channel: FollowupChannel
    template_id: str
    scheduled_at: datetime
    status: DeliveryStatus
    provider_reference: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FollowupAuditEvent:
    followup_id: UUID
    event: str
    status: DeliveryStatus
    occurred_at: datetime


class FollowupService:
    def __init__(self, *, provider: FollowupProviderPort, consent: ConsentStore, templates: TemplateCatalog) -> None:
        self._provider = provider
        self._consent = consent
        self._templates = templates
        self._records: dict[tuple[UUID, UUID], Followup] = {}
        self._callbacks: dict[tuple[UUID, str], DeliveryStatus] = {}
        self.audit_events: list[FollowupAuditEvent] = []

    def create(
        self,
        *,
        tenant_id: UUID,
        lead_id: UUID,
        channel: FollowupChannel,
        scheduled_at: datetime,
        content: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> Followup:
        current = now or datetime.now(UTC)
        if scheduled_at.tzinfo is None or scheduled_at < current:
            raise IntegrationProviderError(
                "INVALID_ARGUMENT", "scheduled_at must be timezone-aware and not in the past."
            )
        template = self._templates.match(channel=channel, content=content)
        if template is None:
            raise IntegrationProviderError("POLICY_DENIED", "Follow-up content is not an approved template.")
        if channel != FollowupChannel.TASK:
            grant = self._consent.get(tenant_id=tenant_id, lead_id=lead_id, channel=channel)
            if grant is None or not grant.active_at(current):
                raise IntegrationProviderError("POLICY_DENIED", "Active channel consent is required.")

        followup_id = uuid5(NAMESPACE_URL, f"followup:{tenant_id}:{idempotency_key}")
        existing = self._records.get((tenant_id, followup_id))
        if existing is not None:
            return existing
        submission = self._provider.submit(
            tenant_id=tenant_id,
            followup_id=followup_id,
            lead_id=lead_id,
            channel=channel,
            scheduled_at=scheduled_at,
            content=content,
            provider_idempotency_key=idempotency_key,
        )
        if not submission.accepted or not submission.provider_reference:
            raise IntegrationProviderError(
                "INVALID_RESULT", "The messaging provider returned no valid acknowledgement."
            )
        record = Followup(
            followup_id=followup_id,
            tenant_id=tenant_id,
            lead_id=lead_id,
            channel=channel,
            template_id=template.template_id,
            scheduled_at=scheduled_at,
            status=DeliveryStatus.QUEUED,
            provider_reference=submission.provider_reference,
            created_at=current,
            updated_at=current,
        )
        self._records[(tenant_id, followup_id)] = record
        self.audit_events.append(FollowupAuditEvent(followup_id, "provider.accepted", record.status, current))
        return record

    def apply_callback(
        self,
        *,
        tenant_id: UUID,
        provider_reference: str,
        callback_id: str,
        status: DeliveryStatus,
        occurred_at: datetime,
    ) -> Followup:
        record = next(
            (
                item
                for item in self._records.values()
                if item.tenant_id == tenant_id and item.provider_reference == provider_reference
            ),
            None,
        )
        if record is None:
            raise IntegrationProviderError("NOT_FOUND", "No follow-up matches this provider reference.")
        callback_key = (tenant_id, callback_id)
        previous_callback = self._callbacks.get(callback_key)
        if previous_callback is not None:
            if previous_callback != status:
                raise IntegrationProviderError("CONFLICT", "Callback identifier was reused with a different status.")
            return record
        if status == record.status:
            self._callbacks[callback_key] = status
            return record
        if record.status in _TERMINAL or status not in _CALLBACK_TRANSITIONS[record.status]:
            raise IntegrationProviderError("CONFLICT", "The delivery status transition is not allowed.")
        updated = replace(record, status=status, updated_at=occurred_at)
        self._records[(tenant_id, record.followup_id)] = updated
        self._callbacks[callback_key] = status
        self.audit_events.append(FollowupAuditEvent(record.followup_id, "provider.callback", status, occurred_at))
        return updated

    def unsubscribe(self, *, tenant_id: UUID, lead_id: UUID, channel: FollowupChannel, at: datetime) -> int:
        revoke = getattr(self._consent, "revoke", None)
        if revoke is None:
            raise IntegrationProviderError("DEPENDENCY_UNAVAILABLE", "Consent store cannot process unsubscribe.")
        revoke(tenant_id=tenant_id, lead_id=lead_id, channel=channel, at=at)
        cancelled = 0
        for key, record in tuple(self._records.items()):
            if (
                record.tenant_id == tenant_id
                and record.lead_id == lead_id
                and record.channel == channel
                and record.status not in _TERMINAL
            ):
                updated = replace(record, status=DeliveryStatus.CANCELLED, updated_at=at)
                self._records[key] = updated
                self.audit_events.append(FollowupAuditEvent(record.followup_id, "consent.revoked", updated.status, at))
                cancelled += 1
        return cancelled


def sandbox_followup_service() -> FollowupService:
    email = "Thanks for speaking with us. Reply STOP to unsubscribe."
    task = "Create a sales follow-up task."
    return FollowupService(
        provider=InMemoryFollowupProvider(),
        consent=InMemoryConsentStore(),
        templates=TemplateCatalog(
            (
                ApprovedTemplate.from_content(
                    template_id="sales-thanks-email-v1", channel=FollowupChannel.EMAIL, content=email
                ),
                ApprovedTemplate.from_content(template_id="sales-task-v1", channel=FollowupChannel.TASK, content=task),
            )
        ),
    )


__all__ = [
    "ApprovedTemplate",
    "ConsentGrant",
    "DeliveryStatus",
    "Followup",
    "FollowupChannel",
    "FollowupProviderPort",
    "FollowupService",
    "InMemoryConsentStore",
    "InMemoryFollowupProvider",
    "ProviderSubmission",
    "TemplateCatalog",
    "sandbox_followup_service",
]
