from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from knotic_api.domain import EventType, MemoryField, SalesState, SessionStatus, new_uuid7
from knotic_api.workflow import (
    CheckpointIdentity,
    ExtractableField,
    ExtractedEntity,
    SalesIntent,
    SemanticTurn,
    TurnUnderstanding,
    WorkflowErrorCode,
    update_memory_node,
)
from knotic_api.workflow.contracts import AssertionStrength, SalesGraphState, WorkflowExecutionError

NOW = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)


def _base() -> SalesGraphState:
    tenant_id = new_uuid7(timestamp_ms=1_787_000_200_000)
    session_id = new_uuid7(timestamp_ms=1_787_000_200_001)
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
        "turn": _turn(tenant_id, session_id, 1),
    }


def _turn(tenant_id: UUID, session_id: UUID, offset: int) -> SemanticTurn:
    return SemanticTurn(
        tenant_id=tenant_id,
        session_id=session_id,
        turn_id=new_uuid7(timestamp_ms=1_787_000_200_010 + offset),
        correlation_id=new_uuid7(timestamp_ms=1_787_000_200_020 + offset),
        actor_id=new_uuid7(timestamp_ms=1_787_000_200_030 + offset),
        sequence=offset,
        text="We need 50 users" if offset == 1 else "Actually make that 250 users",
        locale="en-US",
        occurred_at=NOW + timedelta(seconds=offset),
    )


def _understanding(
    turn: SemanticTurn, value: int, *, assertion: AssertionStrength, ambiguous: bool = False
) -> TurnUnderstanding:
    return TurnUnderstanding(
        intent=SalesIntent.CHANGE_REQUIREMENT if value == 250 else SalesIntent.DISCOVERY,
        intent_confidence=0.98,
        entities=(
            ExtractedEntity(
                field=ExtractableField.USERS,
                value=value,
                confidence=0.96,
                assertion=assertion,
                start_offset=8,
                end_offset=10,
            ),
        ),
        ambiguous=ambiguous,
        ambiguity_reason="The user gave conflicting counts." if ambiguous else None,
        clarification_question="Which user count is correct?" if ambiguous else None,
        language="en",
        source_turn_id=turn.turn_id,
        provider_response_id=f"resp_memory{value:04d}",
    )


def test_confirmed_revision_updates_current_value_history_event_topic_and_checkpoint() -> None:
    first = _base()
    first["understanding"] = _understanding(first["turn"], 50, assertion=AssertionStrength.EXPLICIT)
    created = update_memory_node(first)
    assert created["sales_state"].requirements[0].value == 50
    assert created["sales_state"].current_topic == "discovery"
    assert [event.event_type for event in created["emitted_events"]] == [
        EventType.REQUIREMENT_UPDATED,
        EventType.MEMORY_UPDATED,
    ]

    second_turn = _turn(first["sales_state"].tenant_id, first["sales_state"].session_id, 2)
    second: SalesGraphState = {
        "schema_version": 1,
        "checkpoint": created["checkpoint"],
        "sales_state": created["sales_state"],
        "turn": second_turn,
        "understanding": _understanding(second_turn, 250, assertion=AssertionStrength.EXPLICIT),
        "emitted_events": created["emitted_events"],
    }
    revised = update_memory_node(second)

    assert revised["sales_state"].requirements[0].value == 250
    assert revised["sales_state"].requirements[0].version == 2
    requirement_event = revised["emitted_events"][-2]
    assert requirement_event.payload["old_value"] == 50
    assert requirement_event.payload["new_value"] == 250
    assert revised["checkpoint"].state_version == revised["sales_state"].version
    assert revised["checkpoint"].event_watermark == len(revised["emitted_events"])

    replay_state = {**second, **revised}
    replayed = update_memory_node(cast(SalesGraphState, replay_state))
    assert replayed["sales_state"] == revised["sales_state"]
    assert replayed["emitted_events"] == revised["emitted_events"]


def test_uncertain_or_ambiguous_claims_never_replace_confirmed_memory_or_topic() -> None:
    initial = _base()
    initial["understanding"] = _understanding(initial["turn"], 50, assertion=AssertionStrength.EXPLICIT)
    confirmed = update_memory_node(initial)
    later_turn = _turn(initial["sales_state"].tenant_id, initial["sales_state"].session_id, 2)
    ambiguous: SalesGraphState = {
        "schema_version": 1,
        "checkpoint": confirmed["checkpoint"],
        "sales_state": confirmed["sales_state"],
        "turn": later_turn,
        "understanding": _understanding(
            later_turn,
            250,
            assertion=AssertionStrength.INFERRED,
            ambiguous=True,
        ),
        "uncertain_claims": (),
        "emitted_events": confirmed["emitted_events"],
    }
    result = update_memory_node(ambiguous)

    assert result["sales_state"].requirements[0].value == 50
    assert result["sales_state"].current_topic == "discovery"
    assert result["uncertain_claims"][0].value == 250
    assert result["uncertain_claims"][0].reason == "AMBIGUOUS_TURN"
    assert result["emitted_events"] == confirmed["emitted_events"]


def test_missing_or_wrong_turn_understanding_fails_without_mutation() -> None:
    state = _base()
    with pytest.raises(WorkflowExecutionError) as captured:
        update_memory_node(state)
    assert captured.value.error.code == WorkflowErrorCode.INVALID_STATE
    assert state["sales_state"].memory_facts == ()
    assert all(fact.field != MemoryField.USERS for fact in state["sales_state"].memory_facts)
