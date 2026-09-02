from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knotic_api.domain import (
    BuyingStage,
    Customer,
    EventType,
    Requirement,
    RequirementField,
    SalesState,
    SessionStatus,
    new_uuid7,
)
from knotic_api.workflow import (
    CheckpointIdentity,
    SalesGraphState,
    SalesRoute,
    SemanticTurn,
    buying_stage_for_score,
    update_qualification_node,
)

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


def _requirement(tenant_id: object, session_id: object, field: RequirementField, value: object) -> Requirement:
    return Requirement.model_validate(
        {
            "requirement_id": new_uuid7(),
            "tenant_id": tenant_id,
            "session_id": session_id,
            "field": field,
            "value": value,
            "currency": "USD" if field == RequirementField.BUDGET else None,
            "confirmed": True,
            "confidence": 1,
            "source_turn_id": new_uuid7(),
            "updated_at": NOW,
            "version": 1,
        }
    )


def _state(route: SalesRoute | None = None) -> SalesGraphState:
    tenant_id = new_uuid7()
    session_id = new_uuid7()
    turn_id = new_uuid7()
    requirements = (
        _requirement(tenant_id, session_id, RequirementField.USE_CASES, ("support",)),
        _requirement(tenant_id, session_id, RequirementField.INTEGRATIONS, ("salesforce",)),
        _requirement(tenant_id, session_id, RequirementField.USERS, 250),
        _requirement(tenant_id, session_id, RequirementField.TIMELINE, "this quarter"),
        _requirement(tenant_id, session_id, RequirementField.BUDGET, Decimal("12500.00")),
    )
    sales = SalesState(
        tenant_id=tenant_id,
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        version=1,
        customer=Customer(customer_id=new_uuid7(), tenant_id=tenant_id, role="VP Sales"),
        requirements=requirements,
        latest_request="Book a demo",
        created_at=NOW,
        updated_at=NOW,
    )
    state: SalesGraphState = {
        "schema_version": 1,
        "checkpoint": CheckpointIdentity.for_state(sales),
        "sales_state": sales,
        "turn": SemanticTurn(
            tenant_id=tenant_id,
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=new_uuid7(),
            actor_id=new_uuid7(),
            sequence=1,
            text="Book a demo",
            locale="en-US",
            occurred_at=NOW,
        ),
    }
    if route is not None:
        state["route"] = route
    return state


@pytest.mark.parametrize(
    ("score", "stage"),
    (
        (39, BuyingStage.NURTURE),
        (40, BuyingStage.FOLLOWUP),
        (59, BuyingStage.FOLLOWUP),
        (60, BuyingStage.SALES_QUALIFIED),
        (74, BuyingStage.SALES_QUALIFIED),
        (75, BuyingStage.HIGH_INTENT),
    ),
)
def test_fr09_stage_boundaries(score: int, stage: BuyingStage) -> None:
    assert buying_stage_for_score(score) == stage


@given(st.integers(min_value=0, max_value=100))
def test_every_valid_score_maps_to_exactly_one_stage(score: int) -> None:
    stage = buying_stage_for_score(score)
    assert stage in set(BuyingStage)
    assert (score < 40) == (stage == BuyingStage.NURTURE)


def test_complete_evidence_scores_100_and_explicit_booking_override_is_audited() -> None:
    result = update_qualification_node(_state(SalesRoute.BOOKING))
    assessment = result["qualification_history"][-1]
    assert assessment.qualification.total_score == 100
    assert len(assessment.evidence) == 7
    assert assessment.override is not None
    assert assessment.override.reason_code == "EXPLICIT_BOOKING"
    assert result["sales_state"].buying_stage == BuyingStage.HIGH_INTENT
    assert result["emitted_events"][-1].event_type == EventType.QUALIFICATION_UPDATED
    assert result["checkpoint"].state_version == result["sales_state"].version


def test_missing_data_scores_zero_and_same_turn_replay_is_idempotent() -> None:
    state = _state()
    empty = state["sales_state"].model_copy(update={"customer": None, "requirements": (), "latest_request": None})
    state["sales_state"] = empty
    state["checkpoint"] = CheckpointIdentity.for_state(empty)
    first = update_qualification_node(state)
    assert first["sales_state"].qualification is not None
    assert first["sales_state"].qualification.total_score == 0
    assert all(item.missing for item in first["qualification_history"][-1].evidence)
    replay = update_qualification_node({**state, **first})
    assert replay["sales_state"] == first["sales_state"]
    assert replay["emitted_events"] == first["emitted_events"]
