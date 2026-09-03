"""Compile-only LangGraph topology; later Phase 2 tasks replace contract nodes."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from itertools import pairwise
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .contracts import NODE_CONTRACTS, SalesGraphState, StateUpdate, WorkflowNode, validate_node_update
from .memory_node import update_memory_node
from .objections import detect_objection_node
from .qualification import update_qualification_node
from .routing import ROUTE_NODES, route_destination, route_turn_node
from .understanding import TurnUnderstandingPort, understand_turn_node


def _contract_node(node: WorkflowNode) -> Callable[[SalesGraphState], StateUpdate]:
    def execute(state: SalesGraphState) -> StateUpdate:
        del state
        return validate_node_update(node, {})

    execute.__name__ = node.value
    return execute


def build_sales_graph(
    *, understanding_port: TurnUnderstandingPort | None = None
) -> CompiledStateGraph[SalesGraphState, None, SalesGraphState, SalesGraphState]:
    """Build the authoritative, side-effect-free Phase 2 contract topology."""

    builder = StateGraph(SalesGraphState)
    for node in WorkflowNode:
        action = (
            partial(understand_turn_node, port=understanding_port)
            if node == WorkflowNode.UNDERSTAND_TURN and understanding_port is not None
            else update_memory_node
            if node == WorkflowNode.UPDATE_MEMORY
            else route_turn_node
            if node == WorkflowNode.ROUTE_TURN
            else detect_objection_node
            if node == WorkflowNode.DETECT_OBJECTION
            else update_qualification_node
            if node == WorkflowNode.UPDATE_QUALIFICATION
            else _contract_node(node)
        )
        builder.add_node(node.value, cast(Any, action))
    before_route = (
        WorkflowNode.RECEIVE_TURN,
        WorkflowNode.UNDERSTAND_TURN,
        WorkflowNode.UPDATE_MEMORY,
        WorkflowNode.DETECT_INTENT,
        WorkflowNode.DETECT_OBJECTION,
        WorkflowNode.ROUTE_TURN,
    )
    builder.add_edge(START, before_route[0].value)
    for current, following in pairwise(before_route):
        builder.add_edge(current.value, following.value)
    builder.add_conditional_edges(
        WorkflowNode.ROUTE_TURN.value,
        cast(Any, route_destination),
        {route.value: node.value for route, node in ROUTE_NODES.items()},
    )
    for route_node in ROUTE_NODES.values():
        builder.add_edge(route_node.value, WorkflowNode.UPDATE_QUALIFICATION.value)
    builder.add_edge(WorkflowNode.UPDATE_QUALIFICATION.value, WorkflowNode.NEXT_BEST_ACTION.value)
    builder.add_edge(WorkflowNode.NEXT_BEST_ACTION.value, WorkflowNode.GENERATE_RESPONSE.value)
    builder.add_edge(WorkflowNode.GENERATE_RESPONSE.value, END)
    if set(NODE_CONTRACTS) != set(WorkflowNode):
        raise RuntimeError("every graph node must have exactly one mutation contract")
    return builder.compile(name="knotic-sales-workflow-v1")
