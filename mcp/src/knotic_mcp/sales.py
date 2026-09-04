"""Deterministic Sales MCP read tools: authoritative pricing, qualification, and next action.

No live pricing is embedded in prompts or source code paths that reach the model; every price
comes from the versioned catalog fixture loaded here, and a price outside its effective/expiry
window is rejected rather than served stale.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

_DATA_DIR = Path(__file__).parent / "data"
_QUOTE_VALIDITY = timedelta(days=30)

_QUALIFICATION_MAXIMA: dict[str, int] = {
    "need": 25,
    "product_fit": 20,
    "deployment_fit": 15,
    "timeline": 15,
    "authority": 10,
    "budget": 5,
    "purchase_intent": 10,
}
_STAGE_CEILINGS: tuple[tuple[int, str], ...] = ((40, "NURTURE"), (60, "FOLLOWUP"), (75, "SALES_QUALIFIED"))
_BUYING_STAGES = frozenset({"NURTURE", "FOLLOWUP", "SALES_QUALIFIED", "HIGH_INTENT"})

_INTENT_POLICY: dict[str, tuple[str, str]] = {
    "DISCOVERY": ("ASK_DISCOVERY", "DISCOVERY_REQUIRED"),
    "PRICING": ("GET_PRICING", "AUTHORITATIVE_PRICE_REQUIRED"),
    "PRODUCT_QUESTION": ("RETRIEVE_PRODUCT_INFO", "PRODUCT_GROUNDING_REQUIRED"),
    "COMPETITOR_COMPARISON": ("COMPARE_COMPETITOR", "COMPARISON_GROUNDING_REQUIRED"),
    "OBJECTION": ("HANDLE_OBJECTION", "OBJECTION_POLICY_SELECTED"),
    "CHANGE_REQUIREMENT": ("UPDATE_REQUIREMENT", "CONFIRMED_REQUIREMENT_CHANGE"),
    "DEMO_REQUEST": ("OFFER_DEMO", "EXPLICIT_DEMO_INTEREST"),
    "BOOKING": ("BOOK_DEMO", "EXPLICIT_BOOKING_REQUEST"),
    "FOLLOWUP": ("CREATE_FOLLOWUP", "EXPLICIT_FOLLOWUP_REQUEST"),
    "HUMAN_HANDOFF": ("ESCALATE_HUMAN", "EXPLICIT_HUMAN_REQUEST"),
    "GENERAL_QUESTION": ("ANSWER_QUESTION", "GENERAL_RESPONSE_ALLOWED"),
    "CLOSING": ("END_CALL", "EXPLICIT_CLOSING_REQUEST"),
    "CLARIFICATION": ("ASK_DISCOVERY", "CLARIFICATION_REQUIRED"),
}
_EXPLICIT_REQUEST_OVERRIDES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("BOOK", "MEETING", "SCHEDULE A CALL"), "BOOK_DEMO", "EXPLICIT_BOOKING_REQUEST"),
    (("DEMO",), "OFFER_DEMO", "EXPLICIT_DEMO_INTEREST"),
    (("FOLLOW UP", "FOLLOWUP", "CALL ME BACK", "EMAIL ME"), "CREATE_FOLLOWUP", "EXPLICIT_FOLLOWUP_REQUEST"),
    (("HUMAN", "AGENT", "ESCALATE", "REPRESENTATIVE"), "ESCALATE_HUMAN", "EXPLICIT_HUMAN_REQUEST"),
)


def stage_for_score(score: int) -> str:
    if not 0 <= score <= 100:
        raise ValueError("qualification score must be between zero and one hundred")
    for ceiling, stage in _STAGE_CEILINGS:
        if score < ceiling:
            return stage
    return "HIGH_INTENT"


def qualify_lead(**components: int) -> dict[str, object]:
    """Sum seven bounded evidence components into a deterministic total and buying stage."""
    if set(components) != set(_QUALIFICATION_MAXIMA):
        raise ValueError("qualification requires exactly the seven scored components")
    for name, value in components.items():
        maximum = _QUALIFICATION_MAXIMA[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
            raise ValueError(f"{name} must be an integer between zero and {maximum}")
    total = sum(components.values())
    return {"total": total, "stage": stage_for_score(total), "components": dict(components)}


def decide_next_action(*, intent: str, stage: str, explicit_request: str | None) -> dict[str, object]:
    """Deterministic, enum-limited next action. Explicit customer requests override the route."""
    if stage not in _BUYING_STAGES:
        raise ValueError("stage is not a recognized buying stage")
    action, reason = _INTENT_POLICY.get(intent, ("ASK_DISCOVERY", "UNRECOGNIZED_INTENT_FALLBACK"))
    reason_codes = [reason]
    if explicit_request:
        normalized = explicit_request.strip().upper()
        for phrases, override_action, override_reason in _EXPLICIT_REQUEST_OVERRIDES:
            if any(phrase in normalized for phrase in phrases):
                action, reason_codes = override_action, [override_reason]
                break
    return {"action": action, "reason_codes": reason_codes}


@dataclass(frozen=True, slots=True)
class PricingPlan:
    plan_id: str
    display_name: str
    currency: str
    monthly_price_per_user: Decimal
    annual_price_per_user: Decimal
    minimum_users: int
    region: str
    effective_at: datetime
    expires_at: datetime | None
    source: str
    features: tuple[str, ...] = ()

    def price_per_user(self, billing_period: Literal["MONTHLY", "ANNUAL"]) -> Decimal:
        return self.monthly_price_per_user if billing_period == "MONTHLY" else self.annual_price_per_user

    def is_current(self, *, at: datetime) -> bool:
        return self.effective_at <= at and (self.expires_at is None or self.expires_at > at)


class PricingCatalog:
    """A versioned, source-controlled pricing fixture. Never a live external pricing call."""

    def __init__(self, plans: dict[str, PricingPlan], *, source_version: str) -> None:
        self._plans = plans
        self.source_version = source_version

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> PricingCatalog:
        try:
            raw_plans = payload["plans"]
            if not isinstance(raw_plans, list):
                raise ValueError("pricing catalog payload is malformed")
            plans: dict[str, PricingPlan] = {}
            for raw_item in raw_plans:
                if not isinstance(raw_item, dict):
                    raise ValueError("pricing catalog payload is malformed")
                item: dict[str, object] = raw_item
                raw_features = item.get("features", ())
                if not isinstance(raw_features, list | tuple):
                    raise ValueError("pricing catalog payload is malformed")
                plans[str(item["plan_id"])] = PricingPlan(
                    plan_id=str(item["plan_id"]),
                    display_name=str(item["display_name"]),
                    currency=str(item["currency"]),
                    monthly_price_per_user=Decimal(str(item["monthly_price_per_user"])),
                    annual_price_per_user=Decimal(str(item["annual_price_per_user"])),
                    minimum_users=int(str(item["minimum_users"])),
                    region=str(item["region"]),
                    effective_at=datetime.fromisoformat(str(item["effective_at"])),
                    expires_at=datetime.fromisoformat(str(item["expires_at"])) if item.get("expires_at") else None,
                    source=str(item["source"]),
                    features=tuple(str(feature) for feature in raw_features),
                )
        except (KeyError, InvalidOperation, ValueError) as error:
            raise ValueError("pricing catalog payload is malformed") from error
        return cls(plans, source_version=str(payload["source_version"]))

    @classmethod
    def load_default(cls) -> PricingCatalog:
        text = (_DATA_DIR / "pricing_catalog.json").read_text(encoding="utf-8")
        return cls.from_payload(json.loads(text))

    def plan(self, plan_id: str) -> PricingPlan | None:
        return self._plans.get(plan_id)


def _round_currency(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_quote(
    catalog: PricingCatalog,
    *,
    users: int,
    billing_period: Literal["MONTHLY", "ANNUAL"],
    plan_id: str | None,
    now: datetime | None = None,
) -> dict[str, object] | None:
    """Returns ``None`` (``PRICE_UNAVAILABLE``) rather than an invented or stale price."""
    now = now or datetime.now(UTC)
    plan = catalog.plan(plan_id or "PRO")
    if plan is None or not plan.is_current(at=now) or users < plan.minimum_users:
        return None
    total = plan.price_per_user(billing_period) * users
    valid_until = min(plan.expires_at, now + _QUOTE_VALIDITY) if plan.expires_at else now + _QUOTE_VALIDITY
    quote_id = uuid5(NAMESPACE_URL, f"{plan.plan_id}:{billing_period}:{users}:{plan.source}:{catalog.source_version}")
    return {
        "quote_id": str(quote_id),
        "currency": plan.currency,
        "total": _round_currency(total),
        "valid_until": valid_until.isoformat(),
        "source": f"{plan.source}@{catalog.source_version}",
    }


def compare_plans(
    catalog: PricingCatalog, *, plan_ids: list[str], users: int, now: datetime | None = None
) -> dict[str, object] | None:
    """Returns ``None`` (``PRICE_UNAVAILABLE``) if any requested plan is unknown or stale."""
    now = now or datetime.now(UTC)
    comparisons: list[dict[str, object]] = []
    for plan_id in plan_ids:
        plan = catalog.plan(plan_id)
        if plan is None or not plan.is_current(at=now) or users < plan.minimum_users:
            return None
        comparisons.append(
            {
                "plan_id": plan.plan_id,
                "display_name": plan.display_name,
                "currency": plan.currency,
                "monthly_total": _round_currency(plan.monthly_price_per_user * users),
                "annual_total": _round_currency(plan.annual_price_per_user * users),
                "region": plan.region,
                "features": list(plan.features),
            }
        )
    return {"comparisons": comparisons, "source": f"pricing-catalog@{catalog.source_version}"}


__all__ = [
    "PricingCatalog",
    "PricingPlan",
    "compare_plans",
    "decide_next_action",
    "get_quote",
    "qualify_lead",
    "stage_for_score",
]
