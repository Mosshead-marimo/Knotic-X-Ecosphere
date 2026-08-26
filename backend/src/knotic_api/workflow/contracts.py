"""Versioned graph state, checkpoint, mutation, and failure contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from knotic_api.domain import UUID7, DomainEvent, ObjectionCategory, SalesState


class WorkflowNode(StrEnum):
    RECEIVE_TURN = "receive_turn"
    UNDERSTAND_TURN = "understand_turn"
    UPDATE_MEMORY = "update_memory"
    DETECT_INTENT = "detect_intent"
    DETECT_OBJECTION = "detect_objection"
    ROUTE_TURN = "route_turn"
    HANDLE_DISCOVERY = "handle_discovery"
    HANDLE_PRICING = "handle_pricing"
    HANDLE_PRODUCT_QUESTION = "handle_product_question"
    HANDLE_COMPETITOR_COMPARISON = "handle_competitor_comparison"
    HANDLE_OBJECTION = "handle_objection"
    HANDLE_CHANGE_REQUIREMENT = "handle_change_requirement"
    HANDLE_DEMO_REQUEST = "handle_demo_request"
    HANDLE_BOOKING = "handle_booking"
    HANDLE_FOLLOWUP = "handle_followup"
    HANDLE_HUMAN_HANDOFF = "handle_human_handoff"
    HANDLE_GENERAL_QUESTION = "handle_general_question"
    HANDLE_CLOSING = "handle_closing"
    HANDLE_CLARIFICATION = "handle_clarification"
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


class SalesIntent(StrEnum):
    DISCOVERY = "DISCOVERY"
    PRICING = "PRICING"
    PRODUCT_QUESTION = "PRODUCT_QUESTION"
    COMPETITOR_COMPARISON = "COMPETITOR_COMPARISON"
    OBJECTION = "OBJECTION"
    CHANGE_REQUIREMENT = "CHANGE_REQUIREMENT"
    DEMO_REQUEST = "DEMO_REQUEST"
    BOOKING = "BOOKING"
    FOLLOWUP = "FOLLOWUP"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    GENERAL_QUESTION = "GENERAL_QUESTION"
    CLOSING = "CLOSING"


class AssertionStrength(StrEnum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"


class TopicControl(StrEnum):
    CONTINUE = "CONTINUE"
    SWITCH = "SWITCH"
    RETURN_PREVIOUS = "RETURN_PREVIOUS"


class SalesRoute(StrEnum):
    DISCOVERY = "DISCOVERY"
    PRICING = "PRICING"
    PRODUCT_QUESTION = "PRODUCT_QUESTION"
    COMPETITOR_COMPARISON = "COMPETITOR_COMPARISON"
    OBJECTION = "OBJECTION"
    CHANGE_REQUIREMENT = "CHANGE_REQUIREMENT"
    DEMO_REQUEST = "DEMO_REQUEST"
    BOOKING = "BOOKING"
    FOLLOWUP = "FOLLOWUP"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"
    GENERAL_QUESTION = "GENERAL_QUESTION"
    CLOSING = "CLOSING"
    CLARIFICATION = "CLARIFICATION"


class ObjectionRisk(StrEnum):
    SECURITY = "SECURITY"
    LEGAL = "LEGAL"
    TRUST = "TRUST"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"


class ObjectionPolicyAction(StrEnum):
    GROUND_PRICING = "GROUND_PRICING"
    GROUND_COMPARISON = "GROUND_COMPARISON"
    GROUND_CAPABILITY = "GROUND_CAPABILITY"
    ASK_DISCOVERY = "ASK_DISCOVERY"
    GROUND_OR_ESCALATE = "GROUND_OR_ESCALATE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


class ExtractableField(StrEnum):
    CUSTOMER_NAME = "customer_name"
    COMPANY = "company"
    ROLE = "role"
    USERS = "users"
    USE_CASES = "use_cases"
    INTEGRATIONS = "integrations"
    BUDGET = "budget"
    TIMELINE = "timeline"
    COMPETITORS = "competitors"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = 1


class CheckpointIdentity(ContractModel):
    tenant_id: UUID7
    session_id: UUID7
    thread_id: Annotated[str, StringConstraints(pattern=r"^sales:[0-9a-f-]{36}:[0-9a-f-]{36}$")]
    state_version: int = Field(ge=1)
    event_watermark: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_thread_identity(self) -> CheckpointIdentity:
        expected = f"sales:{self.tenant_id}:{self.session_id}"
        if self.thread_id != expected:
            raise ValueError("checkpoint thread_id must be derived from tenant and session identity")
        return self

    @classmethod
    def for_state(cls, state: SalesState, *, event_watermark: int = 0) -> CheckpointIdentity:
        return cls(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            thread_id=f"sales:{state.tenant_id}:{state.session_id}",
            state_version=state.version,
            event_watermark=event_watermark,
        )


class SemanticTurn(ContractModel):
    tenant_id: UUID7
    session_id: UUID7
    turn_id: UUID7
    correlation_id: UUID7
    actor_id: UUID7
    sequence: int = Field(ge=1)
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
    locale: Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]
    occurred_at: AwareDatetime


class ExtractedEntity(ContractModel):
    field: ExtractableField
    value: str | int | tuple[str, ...]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    confidence: float = Field(ge=0, le=1)
    assertion: AssertionStrength
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_value_and_span(self) -> ExtractedEntity:
        if self.end_offset <= self.start_offset:
            raise ValueError("entity end_offset must be greater than start_offset")
        if self.field == ExtractableField.USERS and (
            not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 1
        ):
            raise ValueError("users entity must be a positive integer")
        collection = self.field in {
            ExtractableField.USE_CASES,
            ExtractableField.INTEGRATIONS,
            ExtractableField.COMPETITORS,
        }
        if collection != isinstance(self.value, tuple):
            raise ValueError("collection entity fields require a tuple value")
        if self.field == ExtractableField.BUDGET and self.currency is None:
            raise ValueError("budget entity requires currency")
        if self.field != ExtractableField.BUDGET and self.currency is not None:
            raise ValueError("currency is valid only for budget entities")
        return self


class ObjectionCandidate(ContractModel):
    category: ObjectionCategory
    confidence: float = Field(ge=0, le=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    risk_flags: tuple[ObjectionRisk, ...] = ()

    @model_validator(mode="after")
    def validate_candidate(self) -> ObjectionCandidate:
        if self.category == ObjectionCategory.OTHER:
            raise ValueError("model objection output must use an FR-07 category")
        if self.end_offset <= self.start_offset:
            raise ValueError("objection end_offset must be greater than start_offset")
        if len(self.risk_flags) != len(set(self.risk_flags)):
            raise ValueError("objection risk flags must be unique")
        return self


class ModelTurnUnderstanding(ContractModel):
    intent: SalesIntent
    intent_confidence: float = Field(ge=0, le=1)
    entities: tuple[ExtractedEntity, ...] = ()
    topic_control: TopicControl = TopicControl.SWITCH
    objection_candidates: tuple[ObjectionCandidate, ...] = ()
    ambiguous: bool
    ambiguity_reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)] | None = (
        None
    )
    clarification_question: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)] | None
    ) = None
    language: Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}$")]

    @model_validator(mode="after")
    def validate_ambiguity(self) -> ModelTurnUnderstanding:
        details_present = self.ambiguity_reason is not None and self.clarification_question is not None
        if self.ambiguous != details_present:
            raise ValueError("ambiguous output requires a reason and clarification question")
        fields = [entity.field for entity in self.entities]
        if len(fields) != len(set(fields)):
            raise ValueError("model output may contain only one proposal per memory field")
        categories = [candidate.category for candidate in self.objection_candidates]
        if len(categories) != len(set(categories)):
            raise ValueError("model output may contain only one objection per category")
        if self.objection_candidates and self.intent != SalesIntent.OBJECTION:
            raise ValueError("turns with objection candidates must use the OBJECTION intent")
        if self.ambiguous and self.objection_candidates:
            raise ValueError("ambiguous turns cannot create objection evidence")
        return self


class TurnUnderstanding(ModelTurnUnderstanding):
    source_turn_id: UUID7
    provider_response_id: Annotated[str, StringConstraints(pattern=r"^resp_[A-Za-z0-9_-]{8,128}$")]

    def validate_against(self, turn: SemanticTurn) -> TurnUnderstanding:
        if self.source_turn_id != turn.turn_id:
            raise ValueError("understanding provenance must match the source turn")
        if any(entity.end_offset > len(turn.text) for entity in self.entities):
            raise ValueError("entity evidence span exceeds the source turn")
        if any(candidate.end_offset > len(turn.text) for candidate in self.objection_candidates):
            raise ValueError("objection evidence span exceeds the source turn")
        return self


class UncertainClaim(ContractModel):
    field: ExtractableField
    value: str | int | Decimal | tuple[str, ...]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    confidence: float = Field(ge=0, le=1)
    source_turn_id: UUID7
    captured_at: AwareDatetime
    reason: Literal["AMBIGUOUS_TURN", "INFERRED", "LOW_CONFIDENCE"]


class TopicFrame(ContractModel):
    route: SalesRoute
    intent: SalesIntent
    source_turn_id: UUID7
    entered_at: AwareDatetime


class ObjectionEvidence(ContractModel):
    objection_id: UUID7
    category: ObjectionCategory
    source_turn_id: UUID7
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    evidence_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    confidence: float = Field(ge=0, le=1)
    risk_flags: tuple[ObjectionRisk, ...]
    policy_action: ObjectionPolicyAction
    escalation_required: bool
    detected_at: AwareDatetime

    @model_validator(mode="after")
    def validate_span(self) -> ObjectionEvidence:
        if self.end_offset <= self.start_offset:
            raise ValueError("objection evidence end_offset must be greater than start_offset")
        return self


class ObjectionDecision(ContractModel):
    objection_id: UUID7
    category: ObjectionCategory
    policy_action: ObjectionPolicyAction
    escalation_required: bool
    requires_grounding: bool
    reason_code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]

    @model_validator(mode="after")
    def validate_policy_flags(self) -> ObjectionDecision:
        if self.escalation_required != (self.policy_action == ObjectionPolicyAction.ESCALATE_HUMAN):
            raise ValueError("objection escalation flag must match the policy action")
        grounding_actions = {
            ObjectionPolicyAction.GROUND_PRICING,
            ObjectionPolicyAction.GROUND_COMPARISON,
            ObjectionPolicyAction.GROUND_CAPABILITY,
            ObjectionPolicyAction.GROUND_OR_ESCALATE,
        }
        if self.requires_grounding != (self.policy_action in grounding_actions):
            raise ValueError("objection grounding flag must match the policy action")
        return self


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
    understanding: NotRequired[TurnUnderstanding | None]
    uncertain_claims: NotRequired[tuple[UncertainClaim, ...]]
    emitted_events: NotRequired[tuple[DomainEvent, ...]]
    route: NotRequired[SalesRoute | None]
    topic_history: NotRequired[tuple[TopicFrame, ...]]
    objection_decision: NotRequired[ObjectionDecision | None]
    objection_history: NotRequired[tuple[ObjectionEvidence, ...]]
    workflow_error: NotRequired[WorkflowError | None]


class StateUpdate(TypedDict, total=False):
    checkpoint: CheckpointIdentity
    sales_state: SalesState
    turn: SemanticTurn
    understanding: TurnUnderstanding | None
    uncertain_claims: tuple[UncertainClaim, ...]
    emitted_events: tuple[DomainEvent, ...]
    route: SalesRoute | None
    topic_history: tuple[TopicFrame, ...]
    objection_decision: ObjectionDecision | None
    objection_history: tuple[ObjectionEvidence, ...]
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
        kind=NodeKind.PURE,
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
        allowed_mutations=frozenset(
            {
                "sales_state",
                "checkpoint",
                "objection_decision",
                "objection_history",
                "emitted_events",
                "workflow_error",
            }
        ),
        description="Apply deterministic objection classification and escalation policy.",
    ),
    WorkflowNode.ROUTE_TURN: NodeContract(
        node=WorkflowNode.ROUTE_TURN,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset({"route", "topic_history", "workflow_error"}),
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

for _route_node in {
    WorkflowNode.HANDLE_DISCOVERY,
    WorkflowNode.HANDLE_PRICING,
    WorkflowNode.HANDLE_PRODUCT_QUESTION,
    WorkflowNode.HANDLE_COMPETITOR_COMPARISON,
    WorkflowNode.HANDLE_OBJECTION,
    WorkflowNode.HANDLE_CHANGE_REQUIREMENT,
    WorkflowNode.HANDLE_DEMO_REQUEST,
    WorkflowNode.HANDLE_BOOKING,
    WorkflowNode.HANDLE_FOLLOWUP,
    WorkflowNode.HANDLE_HUMAN_HANDOFF,
    WorkflowNode.HANDLE_GENERAL_QUESTION,
    WorkflowNode.HANDLE_CLOSING,
    WorkflowNode.HANDLE_CLARIFICATION,
}:
    NODE_CONTRACTS[_route_node] = NodeContract(
        node=_route_node,
        kind=NodeKind.PURE,
        allowed_mutations=frozenset(),
        description="Typed route boundary; later tasks attach only the approved route behavior.",
    )


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
