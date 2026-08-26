"""Validated, immutable version-1 sales domain models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .identifiers import UUID7
from .types import (
    BuyingStage,
    EventType,
    MemoryField,
    MessageSource,
    NextBestAction,
    ObjectionCategory,
    ObjectionStatus,
    OutcomeType,
    RequirementField,
    SessionStatus,
    Speaker,
    ToolCallStatus,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class DomainModel(BaseModel):
    """Strict immutable base for records crossing persistence boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[1] = 1


class Customer(DomainModel):
    customer_id: UUID7
    tenant_id: UUID7
    lead_id: UUID7 | None = None
    name: ShortText | None = None
    company: ShortText | None = None
    role: ShortText | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=32)] | None = None


class MemoryFact(DomainModel):
    """One current structured-memory value with auditable provenance."""

    fact_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    field: MemoryField
    value: int | Decimal | ShortText | tuple[ShortText, ...]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    confirmed: bool
    confidence: float = Field(ge=0, le=1)
    source_turn_id: UUID7
    actor_type: Literal["CUSTOMER", "ASSISTANT", "HUMAN_AGENT", "SYSTEM", "WORKLOAD"]
    captured_at: AwareDatetime
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_typed_value(self) -> MemoryFact:
        list_fields = {
            MemoryField.USE_CASES,
            MemoryField.INTEGRATIONS,
            MemoryField.COMPETITORS,
        }
        if self.field == MemoryField.USERS and (type(self.value) is not int or self.value <= 0):
            raise ValueError("users memory must be a positive integer")
        if self.field == MemoryField.BUDGET:
            if not isinstance(self.value, Decimal) or isinstance(self.value, int) or self.value < 0:
                raise ValueError("budget memory must be a non-negative decimal")
            if self.currency is None:
                raise ValueError("budget memory requires an ISO 4217 currency")
        elif self.currency is not None:
            raise ValueError("currency is valid only for budget memory")
        if self.field in list_fields:
            if not isinstance(self.value, tuple) or not self.value:
                raise ValueError(f"{self.field.value} memory must be a non-empty list")
            normalized = [item.casefold() for item in self.value]
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"{self.field.value} memory must not contain duplicates")
        elif self.field != MemoryField.USERS and self.field != MemoryField.BUDGET and not isinstance(self.value, str):
            raise ValueError(f"{self.field.value} memory must be text")
        if self.field == MemoryField.NEXT_ACTION:
            NextBestAction(str(self.value))
        return self


class Requirement(DomainModel):
    requirement_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    field: RequirementField
    value: int | Decimal | ShortText | tuple[ShortText, ...]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    confirmed: bool
    confidence: float = Field(ge=0, le=1)
    source_turn_id: UUID7
    updated_at: AwareDatetime
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_typed_value(self) -> Requirement:
        if self.field == RequirementField.USERS and (type(self.value) is not int or self.value <= 0):
            raise ValueError("users requirement must be a positive integer")
        if self.field == RequirementField.BUDGET:
            if not isinstance(self.value, Decimal) or isinstance(self.value, int) or self.value < 0:
                raise ValueError("budget requirement must be a non-negative decimal")
            if self.currency is None:
                raise ValueError("budget requirement requires an ISO 4217 currency")
        elif self.currency is not None:
            raise ValueError("currency is valid only for budget requirements")
        if self.field in {RequirementField.USE_CASES, RequirementField.INTEGRATIONS}:
            if not isinstance(self.value, tuple) or not self.value or len(set(self.value)) != len(self.value):
                raise ValueError(f"{self.field.value} requirement must be a non-empty unique list")
        return self


class Objection(DomainModel):
    objection_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    category: ObjectionCategory
    detail: NonEmptyText
    status: ObjectionStatus
    first_turn_id: UUID7
    latest_turn_id: UUID7
    version: int = Field(ge=1)


class Qualification(DomainModel):
    qualification_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    need: int = Field(ge=0, le=25)
    product_fit: int = Field(ge=0, le=20)
    deployment_fit: int = Field(ge=0, le=15)
    timeline: int = Field(ge=0, le=15)
    authority: int = Field(ge=0, le=10)
    budget: int = Field(ge=0, le=5)
    purchase_intent: int = Field(ge=0, le=10)
    total_score: int = Field(ge=0, le=100)
    buying_stage: BuyingStage
    source_turn_id: UUID7
    calculated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_total(self) -> Qualification:
        calculated = (
            self.need
            + self.product_fit
            + self.deployment_fit
            + self.timeline
            + self.authority
            + self.budget
            + self.purchase_intent
        )
        if self.total_score != calculated:
            raise ValueError("qualification total_score must equal the component sum")
        return self


class Message(DomainModel):
    message_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    turn_id: UUID7
    response_id: UUID7 | None = None
    sequence: int = Field(ge=1)
    speaker: Speaker
    source: MessageSource
    content: NonEmptyText
    locale: Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]
    interrupted: bool = False
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_response_identity(self) -> Message:
        if self.speaker == Speaker.ASSISTANT and self.response_id is None:
            raise ValueError("assistant messages require response_id")
        if self.speaker != Speaker.ASSISTANT and self.response_id is not None:
            raise ValueError("response_id is valid only for assistant messages")
        return self


class ToolCall(DomainModel):
    tool_call_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    turn_id: UUID7
    logical_tool: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{2,127}$")]
    tool_version: int = Field(ge=1)
    status: ToolCallStatus
    attempt_count: int = Field(ge=0, le=10)
    timeout_ms: int = Field(ge=1, le=120_000)
    idempotency_key_hash: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None
    safe_error_code: ShortText | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_failure_fields(self) -> ToolCall:
        failed = self.status in {ToolCallStatus.FAILED_RETRYABLE, ToolCallStatus.FAILED_PERMANENT}
        if failed != (self.safe_error_code is not None):
            raise ValueError("safe_error_code must be present exactly for failed tool calls")
        return self


class Outcome(DomainModel):
    outcome_id: UUID7
    tenant_id: UUID7
    session_id: UUID7
    outcome: OutcomeType
    source: Literal["SYSTEM", "CUSTOMER", "HUMAN", "PROVIDER"]
    source_reference: ShortText | None = None
    assigned_at: AwareDatetime

    @model_validator(mode="after")
    def require_provider_confirmation(self) -> Outcome:
        if self.outcome == OutcomeType.ENTERPRISE_DEMO_BOOKED and (
            self.source != "PROVIDER" or self.source_reference is None
        ):
            raise ValueError("ENTERPRISE_DEMO_BOOKED requires a provider confirmation reference")
        return self


class DomainEvent(DomainModel):
    event_id: UUID7
    event_type: EventType
    event_version: Literal[1] = 1
    occurred_at: AwareDatetime
    tenant_id: UUID7
    session_id: UUID7
    sequence: int = Field(ge=1)
    correlation_id: UUID7
    causation_id: UUID7 | None = None
    actor_type: Literal["CUSTOMER", "ASSISTANT", "HUMAN_AGENT", "SYSTEM", "WORKLOAD"]
    actor_id: UUID7
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_payload(self) -> DomainEvent:
        if not self.payload:
            raise ValueError("event payload must not be empty")
        if self.event_type == EventType.REQUIREMENT_UPDATED:
            required = {"field", "old_value", "new_value", "confirmed"}
            if not required.issubset(self.payload) or self.payload.get("confirmed") is not True:
                raise ValueError("requirement.updated requires confirmed old/new value payload")
        if self.event_type == EventType.MEMORY_UPDATED:
            required = {"field", "old_value", "new_value", "confirmed", "source_turn_id"}
            if not required.issubset(self.payload) or self.payload.get("confirmed") is not True:
                raise ValueError("memory.updated requires confirmed old/new value and provenance")
        if self.event_type == EventType.OBJECTION_UPDATED:
            required = {
                "objection_id",
                "category",
                "status",
                "policy_action",
                "escalation_required",
                "source_turn_id",
                "evidence_sha256",
            }
            if not required.issubset(self.payload):
                raise ValueError("objection.updated requires category, policy, escalation, and provenance")
        return self


_SESSION_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.CREATED: frozenset({SessionStatus.ACTIVE, SessionStatus.ENDING, SessionStatus.FAILED}),
    SessionStatus.ACTIVE: frozenset({SessionStatus.ENDING, SessionStatus.FAILED}),
    SessionStatus.ENDING: frozenset({SessionStatus.ENDED, SessionStatus.FAILED}),
    SessionStatus.ENDED: frozenset(),
    SessionStatus.FAILED: frozenset(),
}


class SalesState(DomainModel):
    session_id: UUID7
    tenant_id: UUID7
    status: SessionStatus
    version: int = Field(ge=1)
    current_intent: ShortText | None = None
    current_topic: ShortText | None = None
    buying_stage: BuyingStage = BuyingStage.NURTURE
    qualification: Qualification | None = None
    next_best_action: NextBestAction = NextBestAction.ASK_DISCOVERY
    conversation_summary: Annotated[str, StringConstraints(max_length=20_000)] = ""
    latest_request: NonEmptyText | None = None
    customer: Customer | None = None
    requirements: tuple[Requirement, ...] = ()
    objections: tuple[Objection, ...] = ()
    competitors: tuple[ShortText, ...] = ()
    memory_facts: tuple[MemoryFact, ...] = ()
    outcome: Outcome | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    ended_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_consistency(self) -> SalesState:
        terminal = self.status in {SessionStatus.ENDED, SessionStatus.FAILED}
        if terminal != (self.ended_at is not None):
            raise ValueError("ended_at must be present exactly for terminal sessions")
        if self.updated_at < self.created_at or (self.ended_at is not None and self.ended_at < self.created_at):
            raise ValueError("session timestamps are out of order")
        owned = [self.customer, self.qualification, self.outcome, *self.requirements, *self.objections]
        if any(item is not None and item.tenant_id != self.tenant_id for item in owned):
            raise ValueError("nested records must belong to the session tenant")
        if any(item is not None and getattr(item, "session_id", self.session_id) != self.session_id for item in owned):
            raise ValueError("nested records must belong to the session")
        fields = [requirement.field for requirement in self.requirements]
        if len(fields) != len(set(fields)):
            raise ValueError("SalesState may contain only one current requirement per field")
        if len(self.competitors) != len(set(self.competitors)):
            raise ValueError("competitors must be unique")
        memory_fields = [fact.field for fact in self.memory_facts]
        if len(memory_fields) != len(set(memory_fields)):
            raise ValueError("SalesState may contain only one current memory fact per field")
        if any(fact.tenant_id != self.tenant_id or fact.session_id != self.session_id for fact in self.memory_facts):
            raise ValueError("memory facts must belong to the session")
        return self

    def transition_to(self, target: SessionStatus, *, at: datetime) -> SalesState:
        if target not in _SESSION_TRANSITIONS[self.status]:
            raise ValueError(f"invalid session transition: {self.status.value} -> {target.value}")
        if at.tzinfo is None or at.utcoffset() is None or at < self.updated_at:
            raise ValueError("transition timestamp must be timezone-aware and monotonic")
        terminal = target in {SessionStatus.ENDED, SessionStatus.FAILED}
        return self.model_copy(
            update={
                "status": target,
                "version": self.version + 1,
                "updated_at": at,
                "ended_at": at if terminal else None,
            }
        )
