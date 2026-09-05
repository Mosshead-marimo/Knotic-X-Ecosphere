"""Deterministic FR-10 next-best-action policy."""

from __future__ import annotations

from dataclasses import dataclass

from knotic_api.domain import NextBestAction

from .contracts import (
    ActionInput,
    ApprovalRequirement,
    NextActionDecision,
    SalesGraphState,
    SalesRoute,
    StateUpdate,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)
from .escalation import EscalationDecision


@dataclass(frozen=True, slots=True)
class _Policy:
    action: NextBestAction
    reason: str
    tool: str | None = None
    approval: ApprovalRequirement = ApprovalRequirement.NONE
    required: frozenset[ActionInput] = frozenset()
    grounding: bool = False
    provider_confirmation: bool = False


_POLICIES = {
    SalesRoute.DISCOVERY: _Policy(NextBestAction.ASK_DISCOVERY, "DISCOVERY_REQUIRED"),
    SalesRoute.PRICING: _Policy(
        NextBestAction.GET_PRICING, "AUTHORITATIVE_PRICE_REQUIRED", "pricing.get_quote", grounding=True
    ),
    SalesRoute.PRODUCT_QUESTION: _Policy(
        NextBestAction.RETRIEVE_PRODUCT_INFO, "PRODUCT_GROUNDING_REQUIRED", "product.search", grounding=True
    ),
    SalesRoute.COMPETITOR_COMPARISON: _Policy(
        NextBestAction.COMPARE_COMPETITOR, "COMPARISON_GROUNDING_REQUIRED", "competitor.compare", grounding=True
    ),
    SalesRoute.OBJECTION: _Policy(
        NextBestAction.HANDLE_OBJECTION, "OBJECTION_POLICY_SELECTED", "knowledge.search", grounding=True
    ),
    SalesRoute.CHANGE_REQUIREMENT: _Policy(NextBestAction.UPDATE_REQUIREMENT, "CONFIRMED_REQUIREMENT_CHANGE"),
    SalesRoute.DEMO_REQUEST: _Policy(NextBestAction.OFFER_DEMO, "EXPLICIT_DEMO_INTEREST"),
    SalesRoute.BOOKING: _Policy(
        NextBestAction.BOOK_DEMO,
        "EXPLICIT_BOOKING_REQUEST",
        "calendar.book_meeting",
        ApprovalRequirement.CUSTOMER_CONFIRMATION,
        frozenset({ActionInput.SELECTED_SLOT, ActionInput.CUSTOMER_CONFIRMATION}),
        provider_confirmation=True,
    ),
    SalesRoute.FOLLOWUP: _Policy(
        NextBestAction.CREATE_FOLLOWUP,
        "EXPLICIT_FOLLOWUP_REQUEST",
        "followup.create",
        ApprovalRequirement.POLICY,
        frozenset({ActionInput.FOLLOWUP_CHANNEL}),
        provider_confirmation=True,
    ),
    SalesRoute.HUMAN_HANDOFF: _Policy(
        NextBestAction.ESCALATE_HUMAN,
        "EXPLICIT_HUMAN_REQUEST",
        "handoff.request_agent",
        ApprovalRequirement.POLICY,
        provider_confirmation=True,
    ),
    SalesRoute.GENERAL_QUESTION: _Policy(NextBestAction.ANSWER_QUESTION, "GENERAL_RESPONSE_ALLOWED"),
    SalesRoute.CLOSING: _Policy(NextBestAction.END_CALL, "EXPLICIT_CLOSING_REQUEST"),
    SalesRoute.CLARIFICATION: _Policy(NextBestAction.ASK_DISCOVERY, "CLARIFICATION_REQUIRED"),
}


def decide_next_action(state: SalesGraphState, *, escalation: EscalationDecision | None = None) -> NextActionDecision:
    validated = validate_graph_state(state)
    turn = validated["turn"]
    objection = validated.get("objection_decision")
    if escalation is not None and escalation.should_escalate:
        policy = _Policy(
            NextBestAction.ESCALATE_HUMAN,
            escalation.primary_trigger.value if escalation.primary_trigger is not None else "FR13_POLICY",
            "handoff.request_agent",
            ApprovalRequirement.POLICY,
            provider_confirmation=True,
        )
    elif objection is not None and objection.escalation_required:
        policy = _Policy(
            NextBestAction.ESCALATE_HUMAN,
            "HIGH_RISK_OBJECTION",
            "handoff.request_agent",
            ApprovalRequirement.POLICY,
            provider_confirmation=True,
        )
    elif validated.get("workflow_error") is not None:
        policy = _Policy(NextBestAction.ASK_DISCOVERY, "SAFE_FAILURE_FALLBACK")
    else:
        route = validated.get("route")
        policy = (
            _POLICIES.get(route, _POLICIES[SalesRoute.CLARIFICATION])
            if route is not None
            else _POLICIES[SalesRoute.CLARIFICATION]
        )
    available = validated.get("action_inputs", frozenset())
    return NextActionDecision(
        action=policy.action,
        source_turn_id=turn.turn_id,
        reason_code=policy.reason,
        required_inputs=policy.required,
        missing_inputs=policy.required - available,
        approval=policy.approval,
        tool=policy.tool,
        requires_grounding=policy.grounding,
        provider_confirmation_required=policy.provider_confirmation,
    )


def next_best_action_node(state: SalesGraphState) -> StateUpdate:
    validated = validate_graph_state(state)
    previous = validated.get("next_action_decision")
    if previous is not None and previous.source_turn_id == validated["turn"].turn_id:
        return validate_node_update(
            WorkflowNode.NEXT_BEST_ACTION,
            {
                "sales_state": validated["sales_state"],
                "checkpoint": validated["checkpoint"],
                "next_action_decision": previous,
            },
        )
    decision = decide_next_action(validated)
    current = validated["sales_state"]
    updated = current.model_copy(
        update={
            "next_best_action": decision.action,
            "version": current.version + 1,
            "updated_at": max(current.updated_at, validated["turn"].occurred_at),
        }
    )
    checkpoint = validated["checkpoint"].model_copy(update={"state_version": updated.version})
    return validate_node_update(
        WorkflowNode.NEXT_BEST_ACTION,
        {"sales_state": updated, "checkpoint": checkpoint, "next_action_decision": decision},
    )
