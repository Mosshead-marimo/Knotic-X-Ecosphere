from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knotic_api.domain import SalesState, SessionStatus, new_uuid7
from knotic_api.workflow import (
    INTENT_ROUTES,
    ROUTE_NODES,
    CheckpointIdentity,
    SalesIntent,
    SalesRoute,
    SemanticTurn,
    TopicControl,
    TopicFrame,
    TurnUnderstanding,
    WorkflowNode,
    build_sales_graph,
    route_turn_node,
    update_memory_node,
)
from knotic_api.workflow.contracts import SalesGraphState

NOW = datetime(2026, 8, 26, 11, 0, tzinfo=UTC)


def _state(intent: SalesIntent, *, control: TopicControl = TopicControl.SWITCH) -> SalesGraphState:
    tenant_id = new_uuid7(timestamp_ms=1_787_000_300_000)
    session_id = new_uuid7(timestamp_ms=1_787_000_300_001)
    turn_id = new_uuid7(timestamp_ms=1_787_000_300_002)
    sales_state = SalesState(
        tenant_id=tenant_id,
        session_id=session_id,
        status=SessionStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    return {
        "schema_version": 1,
        "checkpoint": CheckpointIdentity.for_state(sales_state),
        "sales_state": sales_state,
        "turn": SemanticTurn(
            tenant_id=tenant_id,
            session_id=session_id,
            turn_id=turn_id,
            correlation_id=new_uuid7(timestamp_ms=1_787_000_300_003),
            actor_id=new_uuid7(timestamp_ms=1_787_000_300_004),
            sequence=1,
            text="Route this turn",
            locale="en-US",
            occurred_at=NOW,
        ),
        "understanding": TurnUnderstanding(
            intent=intent,
            intent_confidence=0.99,
            topic_control=control,
            ambiguous=False,
            language="en",
            source_turn_id=turn_id,
            provider_response_id="resp_routing0001",
        ),
    }


@pytest.mark.parametrize(("intent", "route"), tuple(INTENT_ROUTES.items()))
def test_every_fr06_intent_reaches_exactly_one_approved_route(intent: SalesIntent, route: SalesRoute) -> None:
    result = route_turn_node(_state(intent))
    assert result["route"] == route
    assert result["topic_history"][-1].route == route
    assert ROUTE_NODES[route] in set(WorkflowNode)


def test_topic_switch_continue_return_and_missing_history_are_deterministic() -> None:
    state = _state(SalesIntent.PRICING, control=TopicControl.RETURN_PREVIOUS)
    turn = state["turn"]
    state["topic_history"] = (
        TopicFrame(
            route=SalesRoute.DISCOVERY, intent=SalesIntent.DISCOVERY, source_turn_id=turn.turn_id, entered_at=NOW
        ),
        TopicFrame(
            route=SalesRoute.PRICING,
            intent=SalesIntent.PRICING,
            source_turn_id=new_uuid7(timestamp_ms=1_787_000_300_010),
            entered_at=NOW + timedelta(seconds=1),
        ),
        TopicFrame(
            route=SalesRoute.PRODUCT_QUESTION,
            intent=SalesIntent.PRODUCT_QUESTION,
            source_turn_id=new_uuid7(timestamp_ms=1_787_000_300_011),
            entered_at=NOW + timedelta(seconds=2),
        ),
    )
    returned = route_turn_node(state)
    assert returned["route"] == SalesRoute.PRICING
    assert returned["topic_history"][-1].route == SalesRoute.PRICING

    continued_state = _state(SalesIntent.GENERAL_QUESTION, control=TopicControl.CONTINUE)
    continued_state["topic_history"] = returned["topic_history"]
    continued_memory = update_memory_node(continued_state)
    continued_state.update(continued_memory)
    assert continued_state["sales_state"].current_topic == "pricing"
    assert route_turn_node(continued_state)["route"] == SalesRoute.PRICING

    missing = _state(SalesIntent.PRICING, control=TopicControl.RETURN_PREVIOUS)
    assert route_turn_node(missing)["route"] == SalesRoute.CLARIFICATION


def test_ambiguous_turn_routes_to_clarification_without_destroying_topic_history() -> None:
    state = _state(SalesIntent.PRICING)
    turn = state["turn"]
    prior = TopicFrame(
        route=SalesRoute.DISCOVERY,
        intent=SalesIntent.DISCOVERY,
        source_turn_id=turn.turn_id,
        entered_at=NOW,
    )
    state["topic_history"] = (prior,)
    state["understanding"] = TurnUnderstanding(
        intent=SalesIntent.PRICING,
        intent_confidence=0.45,
        ambiguous=True,
        ambiguity_reason="Pricing request has no scope.",
        clarification_question="How many users should I price for?",
        language="en",
        source_turn_id=turn.turn_id,
        provider_response_id="resp_routing0002",
    )

    result = route_turn_node(state)
    assert result["route"] == SalesRoute.CLARIFICATION
    assert result["topic_history"] == (prior,)


def test_compiled_graph_contains_one_branch_and_rejoin_for_every_route() -> None:
    graph = build_sales_graph()
    rendered = graph.get_graph()
    pairs = {(edge.source, edge.target) for edge in rendered.edges}
    for route, node in ROUTE_NODES.items():
        assert route in set(SalesRoute)
        assert (WorkflowNode.ROUTE_TURN.value, node.value) in pairs
        assert (node.value, WorkflowNode.UPDATE_QUALIFICATION.value) in pairs
