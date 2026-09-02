from __future__ import annotations

from datetime import UTC, datetime

import pytest

from knotic_api.domain import NextBestAction, ObjectionCategory, SalesState, SessionStatus, new_uuid7
from knotic_api.workflow import (
    ActionInput,
    ApprovalRequirement,
    CheckpointIdentity,
    ObjectionDecision,
    ObjectionPolicyAction,
    SalesGraphState,
    SalesRoute,
    SemanticTurn,
    decide_next_action,
    next_best_action_node,
)

NOW = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def _state(route: SalesRoute, inputs: frozenset[ActionInput] = frozenset()) -> SalesGraphState:
    tenant_id = new_uuid7()
    session_id = new_uuid7()
    turn_id = new_uuid7()
    sales = SalesState(
        tenant_id=tenant_id,
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    return {
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
            text="Choose the next action",
            locale="en-US",
            occurred_at=NOW,
        ),
        "route": route,
        "action_inputs": inputs,
    }


@pytest.mark.parametrize(
    ("route", "action"),
    (
        (SalesRoute.DISCOVERY, NextBestAction.ASK_DISCOVERY),
        (SalesRoute.PRICING, NextBestAction.GET_PRICING),
        (SalesRoute.PRODUCT_QUESTION, NextBestAction.RETRIEVE_PRODUCT_INFO),
        (SalesRoute.COMPETITOR_COMPARISON, NextBestAction.COMPARE_COMPETITOR),
        (SalesRoute.OBJECTION, NextBestAction.HANDLE_OBJECTION),
        (SalesRoute.CHANGE_REQUIREMENT, NextBestAction.UPDATE_REQUIREMENT),
        (SalesRoute.DEMO_REQUEST, NextBestAction.OFFER_DEMO),
        (SalesRoute.BOOKING, NextBestAction.BOOK_DEMO),
        (SalesRoute.FOLLOWUP, NextBestAction.CREATE_FOLLOWUP),
        (SalesRoute.HUMAN_HANDOFF, NextBestAction.ESCALATE_HUMAN),
        (SalesRoute.GENERAL_QUESTION, NextBestAction.ANSWER_QUESTION),
        (SalesRoute.CLOSING, NextBestAction.END_CALL),
        (SalesRoute.CLARIFICATION, NextBestAction.ASK_DISCOVERY),
    ),
)
def test_every_route_has_one_closed_fr10_action(route: SalesRoute, action: NextBestAction) -> None:
    assert decide_next_action(_state(route)).action == action


def test_booking_cannot_execute_without_slot_and_customer_confirmation() -> None:
    blocked = decide_next_action(_state(SalesRoute.BOOKING))
    assert blocked.action == NextBestAction.BOOK_DEMO
    assert blocked.approval == ApprovalRequirement.CUSTOMER_CONFIRMATION
    assert blocked.tool == "calendar.book_meeting"
    assert blocked.provider_confirmation_required
    assert blocked.missing_inputs == {ActionInput.SELECTED_SLOT, ActionInput.CUSTOMER_CONFIRMATION}
    assert not blocked.executable

    allowed = decide_next_action(
        _state(SalesRoute.BOOKING, frozenset({ActionInput.SELECTED_SLOT, ActionInput.CUSTOMER_CONFIRMATION}))
    )
    assert allowed.executable


def test_high_risk_objection_preempts_route_and_requires_policy_handoff() -> None:
    state = _state(SalesRoute.PRICING)
    state["objection_decision"] = ObjectionDecision(
        objection_id=new_uuid7(),
        category=ObjectionCategory.SECURITY,
        policy_action=ObjectionPolicyAction.ESCALATE_HUMAN,
        escalation_required=True,
        requires_grounding=False,
        reason_code="HIGH_RISK_ESCALATION",
    )
    decision = decide_next_action(state)
    assert decision.action == NextBestAction.ESCALATE_HUMAN
    assert decision.approval == ApprovalRequirement.POLICY
    assert decision.tool == "handoff.request_agent"


def test_node_updates_checkpoint_and_same_turn_replay_is_stable() -> None:
    state = _state(SalesRoute.PRICING)
    first = next_best_action_node(state)
    assert first["sales_state"].next_best_action == NextBestAction.GET_PRICING
    assert first["checkpoint"].state_version == first["sales_state"].version
    replay = next_best_action_node({**state, **first})
    assert replay == first
