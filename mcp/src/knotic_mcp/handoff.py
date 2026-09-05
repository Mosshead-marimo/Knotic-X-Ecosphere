"""Structured, acknowledgement-gated human handoff (P5-T008)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .integrations import IntegrationProviderError


class HandoffPriority(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class HandoffStatus(StrEnum):
    REQUESTED = "REQUESTED"
    QUEUED = "QUEUED"
    CONNECTED = "CONNECTED"
    TRANSFERRED = "TRANSFERRED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    FAILED = "FAILED"


class HandoffContext(BaseModel):
    """Complete FR-13 handoff packet; missing fields fail validation before provider access."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    company: Annotated[str, StringConstraints(max_length=255)] | None
    users: int | None = Field(ge=1)
    use_cases: tuple[Annotated[str, StringConstraints(min_length=1, max_length=255)], ...]
    integrations: tuple[Annotated[str, StringConstraints(min_length=1, max_length=255)], ...]
    competitors: tuple[Annotated[str, StringConstraints(min_length=1, max_length=255)], ...]
    objections: tuple[Annotated[str, StringConstraints(min_length=1, max_length=1000)], ...]
    qualification_score: int = Field(ge=0, le=100)
    latest_request: Annotated[str, StringConstraints(max_length=2000)] | None
    summary: Annotated[str, StringConstraints(min_length=1, max_length=4000)]


@dataclass(frozen=True, slots=True)
class Agent:
    agent_id: str
    skills: frozenset[str]
    available: bool = True


class AgentDirectory:
    def __init__(self, agents: tuple[Agent, ...] = ()) -> None:
        self._agents = {agent.agent_id: agent for agent in agents}

    def route(self, reason: str) -> Agent | None:
        normalized = reason.casefold()
        skill = (
            "SECURITY"
            if "security" in normalized or "legal" in normalized
            else "ENTERPRISE"
            if "enterprise" in normalized or "negotiation" in normalized
            else "SALES"
        )
        return next(
            (
                agent
                for agent in sorted(self._agents.values(), key=lambda item: item.agent_id)
                if agent.available and skill in agent.skills
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class ProviderHandoffResult:
    provider_reference: str
    confirmed: bool


class HandoffProviderPort(Protocol):
    def request(self, *, tenant_id: UUID, handoff_id: UUID, assigned_agent: str | None) -> ProviderHandoffResult: ...

    def transfer(
        self, *, tenant_id: UUID, handoff_id: UUID, context: HandoffContext, idempotency_key: str
    ) -> ProviderHandoffResult: ...


class InMemoryHandoffProvider:
    def __init__(self) -> None:
        self.requests: dict[tuple[UUID, UUID], ProviderHandoffResult] = {}
        self.transfers: dict[tuple[UUID, str], ProviderHandoffResult] = {}

    def request(self, *, tenant_id: UUID, handoff_id: UUID, assigned_agent: str | None) -> ProviderHandoffResult:
        key = (tenant_id, handoff_id)
        result = self.requests.get(key)
        if result is None:
            result = ProviderHandoffResult(str(uuid5(NAMESPACE_URL, f"handoff:{tenant_id}:{handoff_id}")), True)
            self.requests[key] = result
        return result

    def transfer(
        self, *, tenant_id: UUID, handoff_id: UUID, context: HandoffContext, idempotency_key: str
    ) -> ProviderHandoffResult:
        del context
        key = (tenant_id, idempotency_key)
        result = self.transfers.get(key)
        if result is None:
            result = ProviderHandoffResult(str(uuid5(NAMESPACE_URL, f"transfer:{tenant_id}:{handoff_id}")), True)
            self.transfers[key] = result
        return result


@dataclass(frozen=True, slots=True)
class Handoff:
    handoff_id: UUID
    tenant_id: UUID
    reason: str
    priority: HandoffPriority
    status: HandoffStatus
    assigned_agent: str | None
    provider_reference: str
    context: HandoffContext | None
    created_at: datetime
    updated_at: datetime

    @property
    def customer_message(self) -> str:
        if self.status in {HandoffStatus.CONNECTED, HandoffStatus.TRANSFERRED}:
            return "A human specialist has accepted the handoff."
        if self.status == HandoffStatus.QUEUED:
            return "No specialist is available yet; your request is safely queued."
        return "A human handoff was requested and is awaiting acknowledgement."


class HandoffService:
    def __init__(self, *, provider: HandoffProviderPort, directory: AgentDirectory) -> None:
        self._provider = provider
        self._directory = directory
        self._records: dict[tuple[UUID, UUID], Handoff] = {}
        self.audit_events: list[tuple[UUID, str, datetime]] = []

    def request(
        self,
        *,
        tenant_id: UUID,
        reason: str,
        priority: HandoffPriority,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> Handoff:
        current = now or datetime.now(UTC)
        handoff_id = uuid5(NAMESPACE_URL, f"handoff:{tenant_id}:{idempotency_key}")
        existing = self._records.get((tenant_id, handoff_id))
        if existing is not None:
            return existing
        agent = self._directory.route(reason)
        provider = self._provider.request(
            tenant_id=tenant_id, handoff_id=handoff_id, assigned_agent=agent.agent_id if agent else None
        )
        if not provider.confirmed or not provider.provider_reference:
            raise IntegrationProviderError("PENDING_CONFIRMATION", "The handoff request is awaiting confirmation.")
        status = HandoffStatus.REQUESTED if agent is not None else HandoffStatus.QUEUED
        record = Handoff(
            handoff_id,
            tenant_id,
            reason,
            priority,
            status,
            agent.agent_id if agent else None,
            provider.provider_reference,
            None,
            current,
            current,
        )
        self._records[(tenant_id, handoff_id)] = record
        self.audit_events.append((handoff_id, "handoff.requested", current))
        return record

    def acknowledge(self, *, tenant_id: UUID, handoff_id: UUID, agent_id: str, at: datetime) -> Handoff:
        record = self._records.get((tenant_id, handoff_id))
        if record is None:
            raise IntegrationProviderError("NOT_FOUND", "The handoff request was not found.")
        if record.status == HandoffStatus.CONNECTED and record.assigned_agent == agent_id:
            return record
        if record.assigned_agent != agent_id or record.status != HandoffStatus.REQUESTED:
            raise IntegrationProviderError("CONFLICT", "The handoff cannot be acknowledged by this agent.")
        updated = replace(record, status=HandoffStatus.CONNECTED, updated_at=at)
        self._records[(tenant_id, handoff_id)] = updated
        self.audit_events.append((handoff_id, "handoff.acknowledged", at))
        return updated

    def transfer_context(
        self,
        *,
        tenant_id: UUID,
        handoff_id: UUID,
        context: HandoffContext,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> Handoff:
        current = now or datetime.now(UTC)
        record = self._records.get((tenant_id, handoff_id))
        if record is None:
            raise IntegrationProviderError("NOT_FOUND", "The handoff request was not found.")
        if record.context is not None:
            if record.context != context:
                raise IntegrationProviderError(
                    "CONFLICT", "Handoff context was already transferred with different data."
                )
            return record
        if record.status != HandoffStatus.CONNECTED:
            updated = replace(record, context=context, status=HandoffStatus.PENDING_CONFIRMATION, updated_at=current)
            self._records[(tenant_id, handoff_id)] = updated
            self.audit_events.append((handoff_id, "handoff.context_pending", current))
            return updated
        provider = self._provider.transfer(
            tenant_id=tenant_id, handoff_id=handoff_id, context=context, idempotency_key=idempotency_key
        )
        status = HandoffStatus.TRANSFERRED if provider.confirmed else HandoffStatus.PENDING_CONFIRMATION
        updated = replace(record, context=context, status=status, updated_at=current)
        self._records[(tenant_id, handoff_id)] = updated
        self.audit_events.append((handoff_id, "handoff.context_transferred", current))
        return updated


def sandbox_handoff_service() -> HandoffService:
    return HandoffService(
        provider=InMemoryHandoffProvider(),
        directory=AgentDirectory((Agent("sales-agent-1", frozenset({"SALES", "ENTERPRISE", "SECURITY"})),)),
    )


__all__ = [
    "Agent",
    "AgentDirectory",
    "Handoff",
    "HandoffContext",
    "HandoffPriority",
    "HandoffProviderPort",
    "HandoffService",
    "HandoffStatus",
    "InMemoryHandoffProvider",
    "ProviderHandoffResult",
    "sandbox_handoff_service",
]
