from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knotic_mcp.sales import (
    PricingCatalog,
    compare_plans,
    decide_next_action,
    get_quote,
    qualify_lead,
    stage_for_score,
)

_CATALOG_PAYLOAD = {
    "source_version": "test-1",
    "plans": [
        {
            "plan_id": "PRO",
            "display_name": "Professional",
            "currency": "USD",
            "monthly_price_per_user": "49.00",
            "annual_price_per_user": "490.00",
            "minimum_users": 5,
            "region": "GLOBAL",
            "effective_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2027-01-01T00:00:00+00:00",
            "source": "pricing-catalog-v1",
            "features": ["Priority support"],
        },
        {
            "plan_id": "ENTERPRISE",
            "display_name": "Enterprise",
            "currency": "USD",
            "monthly_price_per_user": "89.00",
            "annual_price_per_user": "890.00",
            "minimum_users": 25,
            "region": "GLOBAL",
            "effective_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2027-01-01T00:00:00+00:00",
            "source": "pricing-catalog-v1",
            "features": ["SSO and SCIM"],
        },
        {
            "plan_id": "EXPIRED",
            "display_name": "Legacy",
            "currency": "USD",
            "monthly_price_per_user": "9.00",
            "annual_price_per_user": "90.00",
            "minimum_users": 1,
            "region": "GLOBAL",
            "effective_at": "2020-01-01T00:00:00+00:00",
            "expires_at": "2021-01-01T00:00:00+00:00",
            "source": "pricing-catalog-v1",
            "features": [],
        },
    ],
}
_NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _catalog() -> PricingCatalog:
    return PricingCatalog.from_payload(_CATALOG_PAYLOAD)


def test_get_quote_computes_authoritative_total_with_provenance() -> None:
    quote = get_quote(_catalog(), users=10, billing_period="MONTHLY", plan_id="PRO", now=_NOW)
    assert quote is not None
    assert quote["currency"] == "USD"
    assert quote["total"] == "490.00"
    assert quote["source"] == "pricing-catalog-v1@test-1"
    valid_until = datetime.fromisoformat(quote["valid_until"])
    assert valid_until <= _NOW + timedelta(days=30)


def test_get_quote_rejects_unknown_plan_and_below_minimum_users() -> None:
    assert get_quote(_catalog(), users=10, billing_period="MONTHLY", plan_id="NONEXISTENT", now=_NOW) is None
    assert get_quote(_catalog(), users=1, billing_period="MONTHLY", plan_id="ENTERPRISE", now=_NOW) is None


def test_get_quote_rejects_stale_plan_never_fabricating_a_price() -> None:
    assert get_quote(_catalog(), users=1, billing_period="MONTHLY", plan_id="EXPIRED", now=_NOW) is None


def test_compare_plans_requires_every_requested_plan_to_be_current() -> None:
    result = compare_plans(_catalog(), plan_ids=["PRO", "ENTERPRISE"], users=25, now=_NOW)
    assert result is not None
    plan_ids = {item["plan_id"] for item in result["comparisons"]}
    assert plan_ids == {"PRO", "ENTERPRISE"}
    assert compare_plans(_catalog(), plan_ids=["PRO", "EXPIRED"], users=25, now=_NOW) is None


def test_qualify_lead_sums_components_and_derives_stage() -> None:
    result = qualify_lead(
        need=25, product_fit=20, deployment_fit=15, timeline=15, authority=10, budget=5, purchase_intent=10
    )
    assert result == {
        "total": 100,
        "stage": "HIGH_INTENT",
        "components": {
            "need": 25,
            "product_fit": 20,
            "deployment_fit": 15,
            "timeline": 15,
            "authority": 10,
            "budget": 5,
            "purchase_intent": 10,
        },
    }
    low = qualify_lead(need=0, product_fit=0, deployment_fit=0, timeline=0, authority=0, budget=0, purchase_intent=0)
    assert low["total"] == 0 and low["stage"] == "NURTURE"


def test_qualify_lead_rejects_out_of_bounds_component() -> None:
    with pytest.raises(ValueError, match="between zero"):
        qualify_lead(need=26, product_fit=0, deployment_fit=0, timeline=0, authority=0, budget=0, purchase_intent=0)


@pytest.mark.parametrize(
    ("score", "expected"), [(0, "NURTURE"), (39, "NURTURE"), (40, "FOLLOWUP"), (59, "FOLLOWUP"), (60, "SALES_QUALIFIED"), (74, "SALES_QUALIFIED"), (75, "HIGH_INTENT"), (100, "HIGH_INTENT")],
)
def test_stage_for_score_boundaries(score: int, expected: str) -> None:
    assert stage_for_score(score) == expected


def test_decide_next_action_uses_intent_policy_by_default() -> None:
    decision = decide_next_action(intent="PRICING", stage="FOLLOWUP", explicit_request=None)
    assert decision == {"action": "GET_PRICING", "reason_codes": ["AUTHORITATIVE_PRICE_REQUIRED"]}


def test_decide_next_action_lets_explicit_request_override_the_route() -> None:
    decision = decide_next_action(intent="DISCOVERY", stage="SALES_QUALIFIED", explicit_request="Can we book a call?")
    assert decision == {"action": "BOOK_DEMO", "reason_codes": ["EXPLICIT_BOOKING_REQUEST"]}


def test_decide_next_action_rejects_unrecognized_stage() -> None:
    with pytest.raises(ValueError, match="buying stage"):
        decide_next_action(intent="DISCOVERY", stage="MADE_UP_STAGE", explicit_request=None)
