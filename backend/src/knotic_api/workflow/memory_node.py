"""Deterministic confirmed-memory application and uncertain-claim preservation."""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from knotic_api.domain import Customer, DomainEvent, EventType, MemoryFact, MemoryField
from knotic_api.domain.memory import MemoryConflict, merge_memory_fact

from .contracts import (
    AssertionStrength,
    CheckpointIdentity,
    ExtractableField,
    ExtractedEntity,
    SalesGraphState,
    StateUpdate,
    UncertainClaim,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)

CONFIRMED_ENTITY_CONFIDENCE = 0.85


def _derived_uuid7(source: UUID, purpose: str) -> UUID:
    timestamp = source.int >> 80
    random_bits = int.from_bytes(hashlib.sha256(source.bytes + purpose.encode()).digest(), "big") & ((1 << 74) - 1)
    value = timestamp << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)


def _memory_value(entity: ExtractedEntity) -> str | int | Decimal | tuple[str, ...]:
    if entity.field == ExtractableField.BUDGET:
        try:
            return Decimal(str(entity.value))
        except InvalidOperation as error:
            raise ValueError("budget extraction must be a decimal value") from error
    return entity.value


def _uncertain(entity: ExtractedEntity, state: SalesGraphState) -> UncertainClaim:
    understanding = state["understanding"]
    if understanding is None:
        raise ValueError("understanding is required before memory update")
    reason: Literal["AMBIGUOUS_TURN", "INFERRED", "LOW_CONFIDENCE"]
    if understanding.ambiguous:
        reason = "AMBIGUOUS_TURN"
    elif entity.assertion == AssertionStrength.INFERRED:
        reason = "INFERRED"
    else:
        reason = "LOW_CONFIDENCE"
    return UncertainClaim(
        field=entity.field,
        value=_memory_value(entity),
        currency=entity.currency,
        confidence=entity.confidence,
        source_turn_id=state["turn"].turn_id,
        captured_at=state["turn"].occurred_at,
        reason=reason,
    )


def _proposal(state: SalesGraphState, entity: ExtractedEntity) -> MemoryFact:
    sales_state = state["sales_state"]
    turn = state["turn"]
    field = MemoryField(entity.field.value)
    current = next((fact for fact in sales_state.memory_facts if fact.field == field), None)
    replay = current is not None and current.source_turn_id == turn.turn_id
    return MemoryFact(
        fact_id=current.fact_id
        if current is not None and replay
        else _derived_uuid7(turn.turn_id, f"memory:{field.value}"),
        tenant_id=sales_state.tenant_id,
        session_id=sales_state.session_id,
        field=field,
        value=_memory_value(entity),
        currency=entity.currency,
        confirmed=True,
        confidence=entity.confidence,
        source_turn_id=turn.turn_id,
        actor_type="CUSTOMER",
        captured_at=turn.occurred_at,
        version=current.version if current is not None and replay else (1 if current is None else current.version + 1),
    )


def _topic_proposal(state: SalesGraphState) -> MemoryFact:
    sales_state = state["sales_state"]
    turn = state["turn"]
    understanding = state["understanding"]
    if understanding is None:
        raise ValueError("understanding is required before memory update")
    current = next((fact for fact in sales_state.memory_facts if fact.field == MemoryField.CURRENT_TOPIC), None)
    replay = current is not None and current.source_turn_id == turn.turn_id
    return MemoryFact(
        fact_id=current.fact_id
        if current is not None and replay
        else _derived_uuid7(turn.turn_id, "memory:current_topic"),
        tenant_id=sales_state.tenant_id,
        session_id=sales_state.session_id,
        field=MemoryField.CURRENT_TOPIC,
        value=understanding.intent.value.casefold(),
        confirmed=True,
        confidence=understanding.intent_confidence,
        source_turn_id=turn.turn_id,
        actor_type="SYSTEM",
        captured_at=turn.occurred_at,
        version=current.version if current is not None and replay else (1 if current is None else current.version + 1),
    )


def _event(state: SalesGraphState, fact: MemoryFact, replaced: MemoryFact | None, sequence: int) -> DomainEvent:
    event_type = (
        EventType.REQUIREMENT_UPDATED
        if fact.field
        in {
            MemoryField.USERS,
            MemoryField.USE_CASES,
            MemoryField.INTEGRATIONS,
            MemoryField.BUDGET,
            MemoryField.TIMELINE,
        }
        else EventType.MEMORY_UPDATED
    )
    payload = {
        "field": fact.field.value,
        "old_value": None if replaced is None else replaced.model_dump(mode="json")["value"],
        "new_value": fact.model_dump(mode="json")["value"],
        "confirmed": True,
        "source_turn_id": str(fact.source_turn_id),
    }
    return DomainEvent(
        event_id=_derived_uuid7(fact.source_turn_id, f"event:{fact.field.value}"),
        event_type=event_type,
        occurred_at=fact.captured_at,
        tenant_id=fact.tenant_id,
        session_id=fact.session_id,
        sequence=sequence,
        correlation_id=state["turn"].correlation_id,
        causation_id=fact.source_turn_id,
        actor_type="CUSTOMER",
        actor_id=state["turn"].actor_id,
        payload=payload,
    )


def update_memory_node(state: SalesGraphState) -> StateUpdate:
    validated = validate_graph_state(state)
    understanding = validated.get("understanding")
    if understanding is None or understanding.source_turn_id != validated["turn"].turn_id:
        raise WorkflowExecutionError(
            WorkflowError(
                code=WorkflowErrorCode.INVALID_STATE,
                safe_message="Valid turn understanding is required before memory update.",
                retryable=False,
                failed_node=WorkflowNode.UPDATE_MEMORY,
            )
        )

    current = validated["sales_state"]
    uncertain = list(validated.get("uncertain_claims", ()))
    emitted: list[DomainEvent] = []
    proposals: list[MemoryFact] = []
    for entity in understanding.entities:
        confirmed = (
            not understanding.ambiguous
            and entity.assertion == AssertionStrength.EXPLICIT
            and entity.confidence >= CONFIRMED_ENTITY_CONFIDENCE
        )
        if confirmed:
            proposals.append(_proposal(validated, entity))
        else:
            claim = _uncertain(entity, validated)
            if not any(item.field == claim.field and item.source_turn_id == claim.source_turn_id for item in uncertain):
                uncertain.append(claim)
    if not understanding.ambiguous:
        proposals.append(_topic_proposal(validated))

    for proposal in proposals:
        if (
            proposal.field in {MemoryField.CUSTOMER_NAME, MemoryField.COMPANY, MemoryField.ROLE}
            and current.customer is None
        ):
            current = current.model_copy(
                update={"customer": Customer(customer_id=proposal.fact_id, tenant_id=current.tenant_id)}
            )
        try:
            result = merge_memory_fact(current, proposal)
        except (MemoryConflict, ValueError) as error:
            raise WorkflowExecutionError(
                WorkflowError(
                    code=WorkflowErrorCode.CHECKPOINT_CONFLICT,
                    safe_message="Structured memory changed concurrently.",
                    retryable=True,
                    failed_node=WorkflowNode.UPDATE_MEMORY,
                )
            ) from error
        if result.changed:
            sequence = validated["checkpoint"].event_watermark + len(emitted) + 1
            emitted.append(_event(validated, proposal, result.replaced, sequence))
            current = result.state

    checkpoint = CheckpointIdentity(
        tenant_id=current.tenant_id,
        session_id=current.session_id,
        thread_id=validated["checkpoint"].thread_id,
        state_version=current.version,
        event_watermark=validated["checkpoint"].event_watermark + len(emitted),
    )
    existing_events = validated.get("emitted_events", ())
    return validate_node_update(
        WorkflowNode.UPDATE_MEMORY,
        {
            "sales_state": current,
            "checkpoint": checkpoint,
            "uncertain_claims": tuple(uncertain),
            "emitted_events": (*existing_events, *emitted),
        },
    )
