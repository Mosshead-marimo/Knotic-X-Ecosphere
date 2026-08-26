from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from knotic_api.domain import EventType, ObjectionCategory, SalesState, SessionStatus, new_uuid7
from knotic_api.workflow import (
    CheckpointIdentity,
    ObjectionCandidate,
    ObjectionPolicyAction,
    ObjectionRisk,
    SalesIntent,
    SemanticTurn,
    TurnUnderstanding,
    detect_objection_node,
    objection_policy,
)
from knotic_api.workflow.contracts import SalesGraphState

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
TEXT = "The price is too high for us"


def _candidate(category: ObjectionCategory, *, risks: tuple[ObjectionRisk, ...] = ()) -> ObjectionCandidate:
    return ObjectionCandidate(
        category=category,
        confidence=0.96,
        start_offset=0,
        end_offset=len(TEXT),
        risk_flags=risks,
    )


def _state(category: ObjectionCategory = ObjectionCategory.PRICE) -> SalesGraphState:
    tenant_id = new_uuid7(timestamp_ms=1_787_000_400_000)
    session_id = new_uuid7(timestamp_ms=1_787_000_400_001)
    turn_id = new_uuid7(timestamp_ms=1_787_000_400_002)
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
            correlation_id=new_uuid7(timestamp_ms=1_787_000_400_003),
            actor_id=new_uuid7(timestamp_ms=1_787_000_400_004),
            sequence=1,
            text=TEXT,
            locale="en-US",
            occurred_at=NOW,
        ),
        "understanding": TurnUnderstanding(
            intent=SalesIntent.OBJECTION,
            intent_confidence=0.97,
            objection_candidates=(_candidate(category),),
            ambiguous=False,
            language="en",
            source_turn_id=turn_id,
            provider_response_id="resp_objection0001",
        ),
    }


@pytest.mark.parametrize(
    ("category", "action", "escalates"),
    (
        (ObjectionCategory.PRICE, ObjectionPolicyAction.GROUND_PRICING, False),
        (ObjectionCategory.COMPETITOR, ObjectionPolicyAction.GROUND_COMPARISON, False),
        (ObjectionCategory.SECURITY, ObjectionPolicyAction.ESCALATE_HUMAN, True),
        (ObjectionCategory.TRUST, ObjectionPolicyAction.ESCALATE_HUMAN, True),
        (ObjectionCategory.FEATURE_GAP, ObjectionPolicyAction.GROUND_CAPABILITY, False),
        (ObjectionCategory.IMPLEMENTATION, ObjectionPolicyAction.ASK_DISCOVERY, False),
        (ObjectionCategory.TIMELINE, ObjectionPolicyAction.ASK_DISCOVERY, False),
        (ObjectionCategory.BUDGET, ObjectionPolicyAction.GROUND_PRICING, False),
        (ObjectionCategory.AUTHORITY, ObjectionPolicyAction.ASK_DISCOVERY, False),
    ),
)
def test_fr07_category_matrix_is_complete_and_policy_driven(
    category: ObjectionCategory, action: ObjectionPolicyAction, escalates: bool
) -> None:
    decision = objection_policy(_candidate(category), new_uuid7())
    assert decision.policy_action == action
    assert decision.escalation_required is escalates


def test_high_risk_and_unsupported_claims_fail_closed() -> None:
    legal = objection_policy(_candidate(ObjectionCategory.IMPLEMENTATION, risks=(ObjectionRisk.LEGAL,)), new_uuid7())
    unsupported = objection_policy(
        _candidate(ObjectionCategory.FEATURE_GAP, risks=(ObjectionRisk.UNSUPPORTED_CLAIM,)), new_uuid7()
    )
    assert legal.policy_action == ObjectionPolicyAction.ESCALATE_HUMAN and legal.escalation_required
    assert unsupported.policy_action == ObjectionPolicyAction.GROUND_OR_ESCALATE
    assert unsupported.requires_grounding and not unsupported.escalation_required


def test_repeated_objection_preserves_first_turn_and_appends_replay_safe_evidence() -> None:
    first_state = _state()
    first = detect_objection_node(first_state)
    objection = first["sales_state"].objections[0]
    assert objection.version == 1
    assert first["emitted_events"][0].event_type == EventType.OBJECTION_UPDATED
    assert first["objection_history"][0].evidence_sha256

    second_turn = first_state["turn"].model_copy(
        update={
            "turn_id": new_uuid7(timestamp_ms=1_787_000_400_012),
            "correlation_id": new_uuid7(timestamp_ms=1_787_000_400_013),
            "sequence": 2,
            "occurred_at": NOW + timedelta(seconds=1),
        }
    )
    second_understanding = first_state["understanding"].model_copy(
        update={"source_turn_id": second_turn.turn_id, "provider_response_id": "resp_objection0002"}
    )
    second_state: SalesGraphState = {
        **first_state,
        **first,
        "turn": second_turn,
        "understanding": second_understanding,
    }
    repeated = detect_objection_node(second_state)
    current = repeated["sales_state"].objections[0]
    assert current.objection_id == objection.objection_id
    assert current.first_turn_id == objection.first_turn_id
    assert current.latest_turn_id == second_turn.turn_id and current.version == 2
    assert len(repeated["objection_history"]) == 2
    assert len(repeated["emitted_events"]) == 2

    replayed = detect_objection_node({**second_state, **repeated})
    assert replayed["sales_state"] == repeated["sales_state"]
    assert replayed["objection_history"] == repeated["objection_history"]
    assert replayed["emitted_events"] == repeated["emitted_events"]


def test_non_objection_turn_keeps_prior_history_and_invalid_evidence_is_rejected() -> None:
    state = _state()
    detected = detect_objection_node(state)
    later = {**state, **detected}
    later["understanding"] = state["understanding"].model_copy(
        update={"objection_candidates": (), "intent": SalesIntent.GENERAL_QUESTION}
    )
    unchanged = detect_objection_node(later)
    assert unchanged["sales_state"].objections == detected["sales_state"].objections
    assert unchanged["objection_history"] == detected["objection_history"]

    with pytest.raises(ValidationError, match="FR-07"):
        _candidate(ObjectionCategory.OTHER)
