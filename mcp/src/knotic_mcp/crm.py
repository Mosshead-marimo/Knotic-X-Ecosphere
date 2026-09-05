"""CRM lead lookup/dedup (P5-T002) and lead write/activity tools (P5-T003).

``InMemoryCrmProvider`` is a deterministic sandbox fake -- the same role
:class:`knotic_mcp.knowledge.PgvectorRetrievalService` plays opposite
:class:`~knotic_mcp.knowledge.PostgresPgvectorRetrievalService` -- so this module is fully unit
testable without a live CRM. A production adapter implements :class:`CrmProviderPort` against a
real CRM's API and is wired in behind :func:`call_with_resilience` exactly the same way.

Lookup never guesses: :func:`find_lead` only ever returns a single lead when exactly one
candidate has the strongest match on the criteria the caller actually supplied. Two
equally-strong candidates -- for example the same company name attached to two different
provider records -- come back as "not found" rather than an arbitrary pick, because attaching a
conversation to the wrong lead is worse than asking the caller (or a human) to disambiguate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from .integrations import IntegrationProviderError

_LOOKUP_FIELDS = ("provider_lead_id", "email", "phone", "company")


def _normalized_email(value: str) -> str:
    return value.strip().casefold()


def _normalized_phone(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _normalized_company(value: str) -> str:
    return " ".join(value.split()).casefold()


_UNSET = object()


def _changed_optional_str(changes: dict[str, object], key: str, existing: str | None) -> str | None:
    """Returns the new value for an optional string field: unchanged if ``key`` is absent from
    ``changes``, explicitly cleared to ``None`` if present and null, or the supplied string."""
    value = changes.get(key, _UNSET)
    if value is _UNSET:
        return existing
    if value is None:
        return None
    return str(value)


@dataclass(frozen=True, slots=True)
class CrmLead:
    lead_id: UUID
    tenant_id: UUID
    company: str
    email: str | None
    phone: str | None
    provider_lead_id: str
    version: int
    created_at: datetime
    updated_at: datetime
    fields: dict[str, str] = field(default_factory=dict)

    def matches(self, lookup: dict[str, object]) -> int:
        """Count of lookup fields present on the call that agree with this lead.

        A ``provider_lead_id`` match is decisive on its own (it names an exact CRM record), so it
        is weighted heavily enough that no combination of the weaker fields can outscore it.
        """
        score = 0
        provider_lead_id = lookup.get("provider_lead_id")
        if isinstance(provider_lead_id, str) and provider_lead_id == self.provider_lead_id:
            score += 100
        email = lookup.get("email")
        if (
            isinstance(email, str)
            and self.email is not None
            and _normalized_email(email) == _normalized_email(self.email)
        ):
            score += 1
        phone = lookup.get("phone")
        if (
            isinstance(phone, str)
            and self.phone is not None
            and _normalized_phone(phone) == _normalized_phone(self.phone)
        ):
            score += 1
        company = lookup.get("company")
        if isinstance(company, str) and _normalized_company(company) == _normalized_company(self.company):
            score += 1
        return score


@dataclass(frozen=True, slots=True)
class LeadLookupResult:
    found: bool
    lead: CrmLead | None
    ambiguous: bool = False


def find_lead(candidates: list[CrmLead], *, lookup: dict[str, object]) -> LeadLookupResult:
    if not any(key in lookup and lookup[key] is not None for key in _LOOKUP_FIELDS):
        raise ValueError("lookup must supply at least one identifying field")
    scored = [(candidate, candidate.matches(lookup)) for candidate in candidates]
    scored = [item for item in scored if item[1] > 0]
    if not scored:
        return LeadLookupResult(found=False, lead=None)
    best_score = max(score for _candidate, score in scored)
    winners = [candidate for candidate, score in scored if score == best_score]
    if len(winners) > 1:
        # More than one equally-plausible match: never silently attach the conversation to a
        # guess. The caller sees a plain miss and can fall back to creating a new lead or
        # escalating to a human, but is never handed the wrong company's record.
        return LeadLookupResult(found=False, lead=None, ambiguous=True)
    return LeadLookupResult(found=True, lead=winners[0])


class CrmProviderPort(Protocol):
    def find_leads(self, *, tenant_id: UUID, lookup: dict[str, object]) -> list[CrmLead]: ...

    def create_lead(self, *, tenant_id: UUID, lead: dict[str, object]) -> CrmLead: ...

    def update_lead(
        self, *, tenant_id: UUID, lead_id: UUID, expected_version: int, changes: dict[str, object]
    ) -> CrmLead: ...

    def add_note(self, *, tenant_id: UUID, lead_id: UUID, note: str) -> str: ...

    def add_call_summary(
        self, *, tenant_id: UUID, lead_id: UUID, session_id: UUID, summary: str, outcome: str
    ) -> str: ...


class InMemoryCrmProvider:
    """Deterministic sandbox CRM. Every write is confirmed synchronously (no ``PENDING`` path),
    matching a sandbox integration's usual behavior; a production adapter that fronts an
    eventually-confirmed provider would instead return ``PENDING_CONFIRMATION`` until a webhook
    or poll confirms the write, which the tool handlers already accept as a valid outcome."""

    def __init__(self) -> None:
        self._leads: dict[UUID, CrmLead] = {}
        self._notes: dict[UUID, list[str]] = {}
        self._call_summaries: dict[UUID, list[str]] = {}

    def find_leads(self, *, tenant_id: UUID, lookup: dict[str, object]) -> list[CrmLead]:
        return [lead for lead in self._leads.values() if lead.tenant_id == tenant_id]

    def create_lead(self, *, tenant_id: UUID, lead: dict[str, object]) -> CrmLead:
        company = lead.get("company")
        if not isinstance(company, str) or not company.strip():
            raise IntegrationProviderError("INVALID_ARGUMENT", "lead.company is required.", retryable=False)
        now = datetime.now(UTC)
        lead_id = uuid4()
        record = CrmLead(
            lead_id=lead_id,
            tenant_id=tenant_id,
            company=company,
            email=lead.get("email") if isinstance(lead.get("email"), str) else None,
            phone=lead.get("phone") if isinstance(lead.get("phone"), str) else None,
            provider_lead_id=str(uuid5(NAMESPACE_URL, f"crm-lead:{lead_id}")),
            version=1,
            created_at=now,
            updated_at=now,
            fields={
                key: str(value)
                for key, value in lead.items()
                if key not in {"company", "email", "phone"} and value is not None
            },
        )
        self._leads[lead_id] = record
        return record

    def update_lead(
        self, *, tenant_id: UUID, lead_id: UUID, expected_version: int, changes: dict[str, object]
    ) -> CrmLead:
        existing = self._leads.get(lead_id)
        if existing is None or existing.tenant_id != tenant_id:
            raise IntegrationProviderError("NOT_FOUND", "The lead does not exist for this tenant.", retryable=False)
        if existing.version != expected_version:
            raise IntegrationProviderError("CONFLICT", "The lead was modified since expected_version.", retryable=False)
        updated = replace(
            existing,
            company=str(changes.get("company", existing.company)),
            email=_changed_optional_str(changes, "email", existing.email),
            phone=_changed_optional_str(changes, "phone", existing.phone),
            version=existing.version + 1,
            updated_at=datetime.now(UTC),
            fields={
                **existing.fields,
                **{
                    key: str(value)
                    for key, value in changes.items()
                    if key not in {"company", "email", "phone"} and value is not None
                },
            },
        )
        self._leads[lead_id] = updated
        return updated

    def add_note(self, *, tenant_id: UUID, lead_id: UUID, note: str) -> str:
        existing = self._leads.get(lead_id)
        if existing is None or existing.tenant_id != tenant_id:
            raise IntegrationProviderError("NOT_FOUND", "The lead does not exist for this tenant.", retryable=False)
        self._notes.setdefault(lead_id, []).append(note)
        return str(uuid5(NAMESPACE_URL, f"crm-note:{lead_id}:{len(self._notes[lead_id])}"))

    def add_call_summary(self, *, tenant_id: UUID, lead_id: UUID, session_id: UUID, summary: str, outcome: str) -> str:
        existing = self._leads.get(lead_id)
        if existing is None or existing.tenant_id != tenant_id:
            raise IntegrationProviderError("NOT_FOUND", "The lead does not exist for this tenant.", retryable=False)
        self._call_summaries.setdefault(lead_id, []).append(f"{session_id}:{outcome}:{summary}")
        return str(uuid5(NAMESPACE_URL, f"crm-activity:{lead_id}:{len(self._call_summaries[lead_id])}"))


__all__ = [
    "CrmLead",
    "CrmProviderPort",
    "InMemoryCrmProvider",
    "LeadLookupResult",
    "find_lead",
]
