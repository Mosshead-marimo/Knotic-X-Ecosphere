from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from knotic_api.domain import SalesState, SessionStatus, new_uuid7
from knotic_api.workflow import (
    CheckpointIdentity,
    SemanticTurn,
    WorkflowNode,
    build_sales_graph,
    validate_graph_state,
    validate_node_update,
)
from knotic_api.workflow.contracts import NODE_CONTRACTS, InvalidNodeUpdate

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=UTC)


def _state() -> SalesState:
    return SalesState(
        session_id=new_uuid7(timestamp_ms=1_787_000_000_001),
        tenant_id=new_uuid7(timestamp_ms=1_787_000_000_000),
        status=SessionStatus.ACTIVE,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def test_checkpoint_and_turn_contracts_bind_identity_and_reject_extra_fields() -> None:
    state = _state()
    checkpoint = CheckpointIdentity.for_state(state)
    assert checkpoint.thread_id == f"sales:{state.tenant_id}:{state.session_id}"
    with pytest.raises(ValidationError, match="derived"):
        CheckpointIdentity(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            thread_id=f"sales:{state.session_id}:{state.tenant_id}",
            state_version=1,
        )
    with pytest.raises(ValidationError, match="extra"):
        SemanticTurn(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            turn_id=new_uuid7(),
            sequence=1,
            text="Hello",
            locale="en-US",
            occurred_at=NOW,
            untrusted=True,
        )
    other = _state()
    turn = SemanticTurn(
        tenant_id=other.tenant_id,
        session_id=other.session_id,
        turn_id=new_uuid7(),
        sequence=1,
        text="Hello",
        locale="en-US",
        occurred_at=NOW,
    )
    with pytest.raises(ValueError, match="identities"):
        validate_graph_state(
            {
                "schema_version": 1,
                "checkpoint": checkpoint,
                "sales_state": state,
                "turn": turn,
            }
        )


def test_every_graph_node_has_a_typed_mutation_and_execution_boundary() -> None:
    assert set(NODE_CONTRACTS) == set(WorkflowNode)
    assert all(contract.allowed_mutations is not None for contract in NODE_CONTRACTS.values())
    assert {contract.kind.value for contract in NODE_CONTRACTS.values()} == {"PURE", "IO"}
    with pytest.raises(InvalidNodeUpdate, match="route_turn cannot mutate: sales_state"):
        validate_node_update(WorkflowNode.ROUTE_TURN, {"sales_state": _state()})


def test_graph_compiles_with_stable_node_and_edge_inventory() -> None:
    graph = build_sales_graph()
    rendered = graph.get_graph()
    assert set(rendered.nodes) == {"__start__", "__end__", *(node.value for node in WorkflowNode)}
    assert len(rendered.edges) == len(WorkflowNode) + 1
    assert graph.name == "knotic-sales-workflow-v1"
