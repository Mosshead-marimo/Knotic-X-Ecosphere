from __future__ import annotations

from uuid import uuid4

import pytest

from knotic_mcp.crm import CrmLead, InMemoryCrmProvider, find_lead
from knotic_mcp.integrations import IntegrationProviderError

_TENANT = uuid4()


def _lead(
    provider: InMemoryCrmProvider, *, company: str, email: str | None = None, phone: str | None = None
) -> CrmLead:
    lead: dict[str, object] = {"company": company}
    if email is not None:
        lead["email"] = email
    if phone is not None:
        lead["phone"] = phone
    return provider.create_lead(tenant_id=_TENANT, lead=lead)


def test_find_lead_requires_at_least_one_identifying_field() -> None:
    with pytest.raises(ValueError, match="identifying field"):
        find_lead([], lookup={"notes": "not a real lookup field"})


def test_find_lead_misses_rather_than_guessing_with_no_candidates() -> None:
    result = find_lead([], lookup={"email": "person@example.com"})
    assert result == find_lead([], lookup={"email": "person@example.com"})
    assert result.found is False
    assert result.lead is None
    assert result.ambiguous is False


def test_find_lead_matches_a_single_unique_candidate_by_email() -> None:
    provider = InMemoryCrmProvider()
    match = _lead(provider, company="Acme", email="Jane@Acme.com")
    _lead(provider, company="Other Co", email="other@example.com")
    candidates = provider.find_leads(tenant_id=_TENANT, lookup={})
    result = find_lead(candidates, lookup={"email": "jane@acme.com"})
    assert result.found is True
    assert result.lead is not None
    assert result.lead.lead_id == match.lead_id


def test_find_lead_is_ambiguous_rather_than_guessing_between_equal_matches() -> None:
    provider = InMemoryCrmProvider()
    _lead(provider, company="Acme Corp")
    _lead(provider, company="Acme Corp")
    candidates = provider.find_leads(tenant_id=_TENANT, lookup={})
    result = find_lead(candidates, lookup={"company": "Acme Corp"})
    assert result.found is False
    assert result.lead is None
    assert result.ambiguous is True


def test_find_lead_prefers_the_decisive_provider_lead_id_match() -> None:
    provider = InMemoryCrmProvider()
    weaker = _lead(provider, company="Acme Corp", email="weak@example.com")
    stronger = _lead(provider, company="Someone Else", email="strong@example.com")
    candidates = provider.find_leads(tenant_id=_TENANT, lookup={})
    result = find_lead(candidates, lookup={"provider_lead_id": stronger.provider_lead_id, "company": weaker.company})
    assert result.found is True
    assert result.lead is not None
    assert result.lead.lead_id == stronger.lead_id


def test_create_lead_requires_a_company() -> None:
    provider = InMemoryCrmProvider()
    with pytest.raises(IntegrationProviderError) as excinfo:
        provider.create_lead(tenant_id=_TENANT, lead={})
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_create_lead_persists_extra_fields_and_versions_from_one() -> None:
    provider = InMemoryCrmProvider()
    lead = provider.create_lead(tenant_id=_TENANT, lead={"company": "Acme", "industry": "software"})
    assert lead.version == 1
    assert lead.fields == {"industry": "software"}


def test_update_lead_enforces_optimistic_concurrency() -> None:
    provider = InMemoryCrmProvider()
    lead = _lead(provider, company="Acme")
    updated = provider.update_lead(
        tenant_id=_TENANT, lead_id=lead.lead_id, expected_version=1, changes={"company": "Acme Renamed"}
    )
    assert updated.version == 2
    assert updated.company == "Acme Renamed"
    with pytest.raises(IntegrationProviderError) as excinfo:
        provider.update_lead(tenant_id=_TENANT, lead_id=lead.lead_id, expected_version=1, changes={"company": "X"})
    assert excinfo.value.code == "CONFLICT"


def test_update_lead_rejects_unknown_or_cross_tenant_lead() -> None:
    provider = InMemoryCrmProvider()
    lead = _lead(provider, company="Acme")
    with pytest.raises(IntegrationProviderError) as excinfo:
        provider.update_lead(tenant_id=uuid4(), lead_id=lead.lead_id, expected_version=1, changes={"company": "X"})
    assert excinfo.value.code == "NOT_FOUND"


def test_add_note_and_add_call_summary_require_an_existing_lead() -> None:
    provider = InMemoryCrmProvider()
    with pytest.raises(IntegrationProviderError) as excinfo:
        provider.add_note(tenant_id=_TENANT, lead_id=uuid4(), note="hello")
    assert excinfo.value.code == "NOT_FOUND"
    with pytest.raises(IntegrationProviderError):
        provider.add_call_summary(
            tenant_id=_TENANT, lead_id=uuid4(), session_id=uuid4(), summary="summary", outcome="BOOKED_DEMO"
        )


def test_add_note_and_add_call_summary_return_distinct_confirmed_ids() -> None:
    provider = InMemoryCrmProvider()
    lead = _lead(provider, company="Acme")
    first_note = provider.add_note(tenant_id=_TENANT, lead_id=lead.lead_id, note="first")
    second_note = provider.add_note(tenant_id=_TENANT, lead_id=lead.lead_id, note="second")
    assert first_note != second_note
    activity_id = provider.add_call_summary(
        tenant_id=_TENANT, lead_id=lead.lead_id, session_id=uuid4(), summary="great call", outcome="BOOKED_DEMO"
    )
    assert activity_id
