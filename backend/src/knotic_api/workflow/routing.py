"""Deterministic intent routing and nonlinear topic-history control."""

from __future__ import annotations

from .contracts import (
    SalesGraphState,
    SalesIntent,
    SalesRoute,
    StateUpdate,
    TopicControl,
    TopicFrame,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)

INTENT_ROUTES: dict[SalesIntent, SalesRoute] = {intent: SalesRoute(intent.value) for intent in SalesIntent}

ROUTE_NODES: dict[SalesRoute, WorkflowNode] = {
    SalesRoute.DISCOVERY: WorkflowNode.HANDLE_DISCOVERY,
    SalesRoute.PRICING: WorkflowNode.HANDLE_PRICING,
    SalesRoute.PRODUCT_QUESTION: WorkflowNode.HANDLE_PRODUCT_QUESTION,
    SalesRoute.COMPETITOR_COMPARISON: WorkflowNode.HANDLE_COMPETITOR_COMPARISON,
    SalesRoute.OBJECTION: WorkflowNode.HANDLE_OBJECTION,
    SalesRoute.CHANGE_REQUIREMENT: WorkflowNode.HANDLE_CHANGE_REQUIREMENT,
    SalesRoute.DEMO_REQUEST: WorkflowNode.HANDLE_DEMO_REQUEST,
    SalesRoute.BOOKING: WorkflowNode.HANDLE_BOOKING,
    SalesRoute.FOLLOWUP: WorkflowNode.HANDLE_FOLLOWUP,
    SalesRoute.HUMAN_HANDOFF: WorkflowNode.HANDLE_HUMAN_HANDOFF,
    SalesRoute.GENERAL_QUESTION: WorkflowNode.HANDLE_GENERAL_QUESTION,
    SalesRoute.CLOSING: WorkflowNode.HANDLE_CLOSING,
    SalesRoute.CLARIFICATION: WorkflowNode.HANDLE_CLARIFICATION,
}


def select_route(state: SalesGraphState) -> SalesRoute:
    understanding = state.get("understanding")
    if understanding is None:
        raise WorkflowExecutionError(
            WorkflowError(
                code=WorkflowErrorCode.INVALID_STATE,
                safe_message="Turn understanding is required before routing.",
                retryable=False,
                failed_node=WorkflowNode.ROUTE_TURN,
            )
        )
    if understanding.ambiguous:
        return SalesRoute.CLARIFICATION
    desired = INTENT_ROUTES[understanding.intent]
    history = state.get("topic_history", ())
    if understanding.topic_control == TopicControl.CONTINUE and history:
        return history[-1].route
    if understanding.topic_control == TopicControl.RETURN_PREVIOUS:
        for frame in reversed(history[:-1]):
            if frame.route == desired:
                return frame.route
        return SalesRoute.CLARIFICATION
    return desired


def route_turn_node(state: SalesGraphState) -> StateUpdate:
    validated = validate_graph_state(state)
    route = select_route(validated)
    history = list(validated.get("topic_history", ()))
    understanding = validated["understanding"]
    if understanding is None:
        raise AssertionError("route selection requires understanding")
    if route != SalesRoute.CLARIFICATION and (not history or history[-1].route != route):
        history.append(
            TopicFrame(
                route=route,
                intent=understanding.intent,
                source_turn_id=validated["turn"].turn_id,
                entered_at=validated["turn"].occurred_at,
            )
        )
    return validate_node_update(
        WorkflowNode.ROUTE_TURN,
        {"route": route, "topic_history": tuple(history[-32:])},
    )


def route_destination(state: SalesGraphState) -> str:
    route = state.get("route")
    if route is None:
        raise WorkflowExecutionError(
            WorkflowError(
                code=WorkflowErrorCode.INVALID_STATE,
                safe_message="No approved route was selected.",
                retryable=False,
                failed_node=WorkflowNode.ROUTE_TURN,
            )
        )
    return route.value
