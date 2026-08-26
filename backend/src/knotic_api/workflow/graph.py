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
    ordered = tuple(WorkflowNode)
    for node in ordered:
        action = (
            partial(understand_turn_node, port=understanding_port)
            if node == WorkflowNode.UNDERSTAND_TURN and understanding_port is not None
            else update_memory_node
            if node == WorkflowNode.UPDATE_MEMORY
            else _contract_node(node)
        )
        builder.add_node(node.value, cast(Any, action))
    builder.add_edge(START, ordered[0].value)
    for current, following in pairwise(ordered):
        builder.add_edge(current.value, following.value)
    builder.add_edge(ordered[-1].value, END)
    if set(NODE_CONTRACTS) != set(ordered):
        raise RuntimeError("every graph node must have exactly one mutation contract")
    return builder.compile(name="knotic-sales-workflow-v1")
