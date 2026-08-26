"""Evidence-preserving objection detection and deterministic handling policy."""

from __future__ import annotations

import hashlib
from uuid import UUID

from knotic_api.domain import DomainEvent, EventType, Objection, ObjectionCategory, ObjectionStatus
from knotic_api.domain.memory import MemoryConflict, merge_objection

from .contracts import (
    ObjectionCandidate,
    ObjectionDecision,
    ObjectionEvidence,
    ObjectionPolicyAction,
    ObjectionRisk,
    SalesGraphState,
    StateUpdate,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)
from .identifiers import derived_uuid7

_BASE_POLICY: dict[ObjectionCategory, ObjectionPolicyAction] = {
    ObjectionCategory.PRICE: ObjectionPolicyAction.GROUND_PRICING,
    ObjectionCategory.COMPETITOR: ObjectionPolicyAction.GROUND_COMPARISON,
    ObjectionCategory.SECURITY: ObjectionPolicyAction.ESCALATE_HUMAN,
    ObjectionCategory.TRUST: ObjectionPolicyAction.ESCALATE_HUMAN,
    ObjectionCategory.FEATURE_GAP: ObjectionPolicyAction.GROUND_CAPABILITY,
    ObjectionCategory.IMPLEMENTATION: ObjectionPolicyAction.ASK_DISCOVERY,
    ObjectionCategory.TIMELINE: ObjectionPolicyAction.ASK_DISCOVERY,
    ObjectionCategory.BUDGET: ObjectionPolicyAction.GROUND_PRICING,
    ObjectionCategory.AUTHORITY: ObjectionPolicyAction.ASK_DISCOVERY,
    ObjectionCategory.OTHER: ObjectionPolicyAction.ESCALATE_HUMAN,
}
_HIGH_RISK = {ObjectionRisk.SECURITY, ObjectionRisk.LEGAL, ObjectionRisk.TRUST}
_GROUNDING_ACTIONS = {
    ObjectionPolicyAction.GROUND_PRICING,
    ObjectionPolicyAction.GROUND_COMPARISON,
    ObjectionPolicyAction.GROUND_CAPABILITY,
    ObjectionPolicyAction.GROUND_OR_ESCALATE,
}


def objection_policy(candidate: ObjectionCandidate, objection_id: UUID) -> ObjectionDecision:
    high_risk = candidate.category in {ObjectionCategory.SECURITY, ObjectionCategory.TRUST} or bool(
        set(candidate.risk_flags) & _HIGH_RISK
    )
    action = _BASE_POLICY[candidate.category]
    if high_risk:
        action = ObjectionPolicyAction.ESCALATE_HUMAN
    elif ObjectionRisk.UNSUPPORTED_CLAIM in candidate.risk_flags:
        action = ObjectionPolicyAction.GROUND_OR_ESCALATE
    reason = "HIGH_RISK_ESCALATION" if high_risk else f"{candidate.category.value}_POLICY"
    return ObjectionDecision(
        objection_id=objection_id,
        category=candidate.category,
        policy_action=action,
        escalation_required=high_risk,
        requires_grounding=action in _GROUNDING_ACTIONS,
        reason_code=reason,
    )


def _event(state: SalesGraphState, evidence: ObjectionEvidence, sequence: int) -> DomainEvent:
    turn = state["turn"]
    return DomainEvent(
        event_id=derived_uuid7(turn.turn_id, f"event:objection:{evidence.category.value}"),
        event_type=EventType.OBJECTION_UPDATED,
        occurred_at=turn.occurred_at,
        tenant_id=turn.tenant_id,
        session_id=turn.session_id,
        sequence=sequence,
        correlation_id=turn.correlation_id,
        causation_id=turn.turn_id,
        actor_type="CUSTOMER",
        actor_id=turn.actor_id,
        payload={
            "objection_id": str(evidence.objection_id),
            "category": evidence.category.value,
            "status": ObjectionStatus.OPEN.value,
            "policy_action": evidence.policy_action.value,
            "escalation_required": evidence.escalation_required,
            "source_turn_id": str(evidence.source_turn_id),
            "evidence_sha256": evidence.evidence_sha256,
        },
    )


def detect_objection_node(state: SalesGraphState) -> StateUpdate:
    validated = validate_graph_state(state)
    understanding = validated.get("understanding")
    if understanding is None:
        raise WorkflowExecutionError(
            WorkflowError(
                code=WorkflowErrorCode.INVALID_STATE,
                safe_message="Turn understanding is required before objection detection.",
                retryable=False,
                failed_node=WorkflowNode.DETECT_OBJECTION,
            )
        )
    current = validated["sales_state"]
    history = list(validated.get("objection_history", ()))
    events: list[DomainEvent] = []
    decisions: list[ObjectionDecision] = []
    for candidate in sorted(understanding.objection_candidates, key=lambda item: item.category.value):
        excerpt = validated["turn"].text[candidate.start_offset : candidate.end_offset]
        if not excerpt.strip():
            raise WorkflowExecutionError(
                WorkflowError(
                    code=WorkflowErrorCode.INVALID_MODEL_OUTPUT,
                    safe_message="Objection evidence was invalid.",
                    retryable=False,
                    failed_node=WorkflowNode.DETECT_OBJECTION,
                )
            )
        existing = next((item for item in current.objections if item.category == candidate.category), None)
        objection_id = (
            existing.objection_id
            if existing is not None
            else derived_uuid7(current.session_id, f"objection:{candidate.category.value}")
        )
        replay = existing is not None and existing.latest_turn_id == validated["turn"].turn_id
        next_version = 1 if existing is None else (existing.version if replay else existing.version + 1)
        proposal = Objection(
            objection_id=objection_id,
            tenant_id=current.tenant_id,
            session_id=current.session_id,
            category=candidate.category,
            detail=excerpt,
            status=ObjectionStatus.OPEN,
            first_turn_id=existing.first_turn_id if existing is not None else validated["turn"].turn_id,
            latest_turn_id=validated["turn"].turn_id,
            version=next_version,
        )
        decision = objection_policy(candidate, objection_id)
        decisions.append(decision)
        evidence = ObjectionEvidence(
            objection_id=objection_id,
            category=candidate.category,
            source_turn_id=validated["turn"].turn_id,
            start_offset=candidate.start_offset,
            end_offset=candidate.end_offset,
            evidence_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
            confidence=candidate.confidence,
            risk_flags=candidate.risk_flags,
            policy_action=decision.policy_action,
            escalation_required=decision.escalation_required,
            detected_at=validated["turn"].occurred_at,
        )
        try:
            updated = merge_objection(current, proposal)
        except (MemoryConflict, ValueError) as error:
            raise WorkflowExecutionError(
                WorkflowError(
                    code=WorkflowErrorCode.CHECKPOINT_CONFLICT,
                    safe_message="Objection history changed concurrently.",
                    retryable=True,
                    failed_node=WorkflowNode.DETECT_OBJECTION,
                )
            ) from error
        if updated is not current:
            current = updated.model_copy(update={"updated_at": max(updated.updated_at, evidence.detected_at)})
            if not any(
                item.objection_id == evidence.objection_id and item.source_turn_id == evidence.source_turn_id
                for item in history
            ):
                history.append(evidence)
                sequence = validated["checkpoint"].event_watermark + len(events) + 1
                events.append(_event(validated, evidence, sequence))

    primary = max(
        decisions,
        key=lambda item: (item.escalation_required, item.requires_grounding, item.category.value),
        default=None,
    )
    checkpoint = validated["checkpoint"]
    if events:
        checkpoint = checkpoint.model_copy(
            update={"state_version": current.version, "event_watermark": checkpoint.event_watermark + len(events)}
        )
    return validate_node_update(
        WorkflowNode.DETECT_OBJECTION,
        {
            "sales_state": current,
            "checkpoint": checkpoint,
            "objection_decision": primary,
            "objection_history": tuple(history[-100:]),
            "emitted_events": (*validated.get("emitted_events", ()), *events),
        },
    )
