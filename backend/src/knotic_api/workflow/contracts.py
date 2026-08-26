"""Versioned graph state, checkpoint, mutation, and failure contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from knotic_api.domain import UUID7, DomainEvent, SalesState


class WorkflowNode(StrEnum):
    RECEIVE_TURN = "receive_turn"
    UNDERSTAND_TURN = "understand_turn"
    UPDATE_MEMORY = "update_memory"
    DETECT_INTENT = "detect_intent"
    DETECT_OBJECTION = "detect_objection"
    ROUTE_TURN = "route_turn"
    UPDATE_QUALIFICATION = "update_qualification"
    NEXT_BEST_ACTION = "next_best_action"
    GENERATE_RESPONSE = "generate_response"


class NodeKind(StrEnum):
    PURE = "PURE"
    IO = "IO"


class WorkflowErrorCode(StrEnum):
    INVALID_STATE = "INVALID_STATE"
    INVALID_TURN = "INVALID_TURN"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    CHECKPOINT_CONFLICT = "CHECKPOINT_CONFLICT"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = 1


class CheckpointIdentity(ContractModel):
    tenant_id: UUID7
    session_id: UUID7
    thread_id: Annotated[str, StringConstraints(pattern=r"^sales:[0-9a-f-]{36}:[0-9a-f-]{36}$")]
    state_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_thread_identity(self) -> CheckpointIdentity:
        expected = f"sales:{self.tenant_id}:{self.session_id}"
        if self.thread_id != expected:
            raise ValueError("checkpoint thread_id must be derived from tenant and session identity")
        return self

    @classmethod
    def for_state(cls, state: SalesState) -> CheckpointIdentity:
        return cls(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            thread_id=f"sales:{state.tenant_id}:{state.session_id}",
            state_version=state.version,
        )


class SemanticTurn(ContractModel):
    tenant_id: UUID7
    session_id: UUID7
    turn_id: UUID7
    sequence: int = Field(ge=1)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
    locale: Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]
    occurred_at: AwareDatetime


class WorkflowError(ContractModel):
    code: WorkflowErrorCode
    safe_message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    retryable: bool
    failed_node: WorkflowNode


class SalesGraphState(TypedDict):
    schema_version: Literal[1]
    checkpoint: CheckpointIdentity
    sales_state: SalesState
    turn: SemanticTurn
    understanding: NotRequired[object | None]
    uncertain_claims: NotRequired[tuple[object, ...]]
    emitted_events: NotRequired[tuple[DomainEvent, ...]]
    route: NotRequired[str | None]
    objection_decision: NotRequired[object | None]
    workflow_error: NotRequired[WorkflowError | None]


class StateUpdate(TypedDict, total=False):
    checkpoint: CheckpointIdentity
    sales_state: SalesState
    turn: SemanticTurn
    understanding: object | None
    uncertain_claims: tuple[object, ...]
    emitted_events: tuple[DomainEvent, ...]
    route: str | None
    objection_decision: object | None
    workflow_error: WorkflowError | None


_STATE_ADAPTER = TypeAdapter(SalesGraphState)


def validate_graph_state(payload: object) -> SalesGraphState:
    state = _STATE_ADAPTER.validate_python(payload)
    sales_state = state["sales_state"]
    turn = state["turn"]
    checkpoint = state["checkpoint"]
    identities = {(sales_state.tenant_id, sales_state.session_id), (turn.tenant_id, turn.session_id)}
    if identities != {(checkpoint.tenant_id, checkpoint.session_id)}:
        raise ValueError("graph state identities must match the checkpoint")
    if checkpoint.state_version != sales_state.version:
        raise ValueError("checkpoint state_version must match SalesState")
    return state


class NodeContract(ContractModel):
    node: WorkflowNode
    kind: NodeKind
    allowed_mutations: frozenset[str]
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


NODE_CONTRACTS: dict[WorkflowNode, NodeContract] = {
    WorkflowNode.RECEIVE_TURN: NodeContract(
        node=WorkflowNode.RECEIVE_TURN,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset(),
        description="Validate the already-authenticated semantic turn envelope.",
    ),
    WorkflowNode.UNDERSTAND_TURN: NodeContract(
        node=WorkflowNode.UNDERSTAND_TURN,
        kind=NodeKind.IO,
        allowed_mutations=frozenset({"understanding", "workflow_error"}),
        description="Call the model port and validate intent and entity proposals.",
    ),
    WorkflowNode.UPDATE_MEMORY: NodeContract(
        node=WorkflowNode.UPDATE_MEMORY,
        kind=NodeKind.IO,
        allowed_mutations=frozenset(
            {"sales_state", "checkpoint", "uncertain_claims", "emitted_events", "workflow_error"}
        ),
        description="Apply deterministic memory policy and persist accepted revisions.",
    ),
    WorkflowNode.DETECT_INTENT: NodeContract(
        node=WorkflowNode.DETECT_INTENT,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset({"sales_state", "workflow_error"}),
        description="Select the validated intent without provider access.",
    ),
    WorkflowNode.DETECT_OBJECTION: NodeContract(
        node=WorkflowNode.DETECT_OBJECTION,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset({"sales_state", "objection_decision", "emitted_events", "workflow_error"}),
        description="Apply deterministic objection classification and escalation policy.",
    ),
    WorkflowNode.ROUTE_TURN: NodeContract(
        node=WorkflowNode.ROUTE_TURN,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset({"route", "workflow_error"}),
        description="Choose one approved route from validated turn evidence and state.",
    ),
    WorkflowNode.UPDATE_QUALIFICATION: NodeContract(
        node=WorkflowNode.UPDATE_QUALIFICATION,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset({"sales_state", "emitted_events", "workflow_error"}),
        description="Recalculate explainable qualification from structured evidence.",
    ),
    WorkflowNode.NEXT_BEST_ACTION: NodeContract(
        node=WorkflowNode.NEXT_BEST_ACTION,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset({"sales_state", "workflow_error"}),
        description="Select an approved action without performing the action.",
    ),
    WorkflowNode.GENERATE_RESPONSE: NodeContract(
        node=WorkflowNode.GENERATE_RESPONSE,
        kind=NodeKind.IO,
        allowed_mutations=frozenset({"sales_state", "workflow_error"}),
        description="Generate and validate grounded response content through the model port.",
    ),
}


class InvalidNodeUpdate(ValueError):
    """A node attempted to mutate state outside its declared ownership."""


def validate_node_update(node: WorkflowNode, update: StateUpdate) -> StateUpdate:
    unexpected = set(update).difference(NODE_CONTRACTS[node].allowed_mutations)
    if unexpected:
        fields = ", ".join(sorted(unexpected))
        raise InvalidNodeUpdate(f"{node.value} cannot mutate: {fields}")
    return update


class WorkflowExecutionError(RuntimeError):
    """Safe workflow failure with an explicit retry contract."""

    def __init__(self, error: WorkflowError) -> None:
        super().__init__(error.safe_message)
        self.error = error
