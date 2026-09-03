"""Pure, evidence-bearing FR-09 qualification policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from knotic_api.domain import BuyingStage, DomainEvent, EventType, Qualification, RequirementField

from .contracts import (
    QualificationAssessment,
    QualificationDimension,
    QualificationEvidence,
    QualificationOverride,
    SalesGraphState,
    SalesRoute,
    StateUpdate,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)
from .identifiers import derived_uuid7

_MAXIMA = {
    QualificationDimension.BUSINESS_NEED: 25,
    QualificationDimension.PRODUCT_FIT: 20,
    QualificationDimension.DEPLOYMENT_FIT: 15,
    QualificationDimension.TIMELINE: 15,
    QualificationDimension.AUTHORITY: 10,
    QualificationDimension.BUDGET: 5,
    QualificationDimension.PURCHASE_INTENT: 10,
}
_PURCHASE_INTENT = {
    SalesRoute.DEMO_REQUEST: 8,
    SalesRoute.BOOKING: 10,
    SalesRoute.FOLLOWUP: 5,
    SalesRoute.CLOSING: 10,
}
type OverrideReason = Literal["EXPLICIT_BOOKING", "EXPLICIT_DEMO", "EXPLICIT_FOLLOWUP", "EXPLICIT_CLOSING"]

_OVERRIDES: dict[SalesRoute, tuple[BuyingStage, OverrideReason]] = {
    SalesRoute.DEMO_REQUEST: (BuyingStage.SALES_QUALIFIED, "EXPLICIT_DEMO"),
    SalesRoute.BOOKING: (BuyingStage.HIGH_INTENT, "EXPLICIT_BOOKING"),
    SalesRoute.FOLLOWUP: (BuyingStage.FOLLOWUP, "EXPLICIT_FOLLOWUP"),
    SalesRoute.CLOSING: (BuyingStage.HIGH_INTENT, "EXPLICIT_CLOSING"),
}


def buying_stage_for_score(score: int) -> BuyingStage:
    if not 0 <= score <= 100:
        raise ValueError("qualification score must be between zero and one hundred")
    if score < 40:
        return BuyingStage.NURTURE
    if score < 60:
        return BuyingStage.FOLLOWUP
    if score < 75:
        return BuyingStage.SALES_QUALIFIED
    return BuyingStage.HIGH_INTENT


def _evidence(dimension: QualificationDimension, points: int, sources: Iterable[str]) -> QualificationEvidence:
    source_tuple = tuple(sources)
    return QualificationEvidence(
        dimension=dimension,
        points=points,
        maximum=_MAXIMA[dimension],
        sources=source_tuple,
        missing=not source_tuple,
    )


def assess_qualification(state: SalesGraphState) -> QualificationAssessment:
    validated = validate_graph_state(state)
    sales = validated["sales_state"]
    turn = validated["turn"]
    requirements = {item.field: item for item in sales.requirements if item.confirmed}
    use_cases = RequirementField.USE_CASES in requirements
    integrations = RequirementField.INTEGRATIONS in requirements
    users = RequirementField.USERS in requirements
    route = validated.get("route")
    purchase_points = _PURCHASE_INTENT.get(route, 0) if route is not None else 0

    evidence = (
        _evidence(
            QualificationDimension.BUSINESS_NEED,
            (15 if use_cases else 0) + (10 if sales.latest_request else 0),
            (*(("CONFIRMED_USE_CASES",) if use_cases else ()), *(("LATEST_REQUEST",) if sales.latest_request else ())),
        ),
        _evidence(
            QualificationDimension.PRODUCT_FIT,
            (10 if use_cases else 0) + (10 if integrations else 0),
            (*(("CONFIRMED_USE_CASES",) if use_cases else ()), *(("CONFIRMED_INTEGRATIONS",) if integrations else ())),
        ),
        _evidence(
            QualificationDimension.DEPLOYMENT_FIT,
            (8 if users else 0) + (7 if integrations else 0),
            (*(("CONFIRMED_USERS",) if users else ()), *(("CONFIRMED_INTEGRATIONS",) if integrations else ())),
        ),
        _evidence(
            QualificationDimension.TIMELINE,
            15 if RequirementField.TIMELINE in requirements else 0,
            ("CONFIRMED_TIMELINE",) if RequirementField.TIMELINE in requirements else (),
        ),
        _evidence(
            QualificationDimension.AUTHORITY,
            10 if sales.customer is not None and sales.customer.role else 0,
            ("CONFIRMED_ROLE",) if sales.customer is not None and sales.customer.role else (),
        ),
        _evidence(
            QualificationDimension.BUDGET,
            5 if RequirementField.BUDGET in requirements else 0,
            ("CONFIRMED_BUDGET",) if RequirementField.BUDGET in requirements else (),
        ),
        _evidence(
            QualificationDimension.PURCHASE_INTENT,
            purchase_points,
            (f"EXPLICIT_{route.value}",) if route is not None and route in _PURCHASE_INTENT else (),
        ),
    )
    score = sum(item.points for item in evidence)
    override: QualificationOverride | None = None
    stage = buying_stage_for_score(score)
    if route in _OVERRIDES:
        override_stage, reason = _OVERRIDES[route]
        stage = override_stage
        override = QualificationOverride(
            stage=override_stage,
            route=route,
            source_turn_id=turn.turn_id,
            reason_code=reason,
        )
    qualification = Qualification(
        qualification_id=derived_uuid7(turn.turn_id, "qualification"),
        tenant_id=sales.tenant_id,
        session_id=sales.session_id,
        need=evidence[0].points,
        product_fit=evidence[1].points,
        deployment_fit=evidence[2].points,
        timeline=evidence[3].points,
        authority=evidence[4].points,
        budget=evidence[5].points,
        purchase_intent=evidence[6].points,
        total_score=score,
        buying_stage=stage,
        source_turn_id=turn.turn_id,
        calculated_at=turn.occurred_at,
    )
    return QualificationAssessment(
        qualification=qualification,
        evidence=evidence,
        override=override,
        previous_score=sales.qualification.total_score if sales.qualification else 0,
    )


def update_qualification_node(state: SalesGraphState) -> StateUpdate:
    validated = validate_graph_state(state)
    current = validated["sales_state"]
    if current.qualification is not None and current.qualification.source_turn_id == validated["turn"].turn_id:
        return validate_node_update(
            WorkflowNode.UPDATE_QUALIFICATION,
            {
                "sales_state": current,
                "checkpoint": validated["checkpoint"],
                "qualification_history": validated.get("qualification_history", ()),
                "emitted_events": validated.get("emitted_events", ()),
            },
        )
    assessment = assess_qualification(validated)
    qualification = assessment.qualification
    updated = current.model_copy(
        update={
            "qualification": qualification,
            "buying_stage": qualification.buying_stage,
            "version": current.version + 1,
            "updated_at": max(current.updated_at, qualification.calculated_at),
        }
    )
    checkpoint = validated["checkpoint"].model_copy(
        update={"state_version": updated.version, "event_watermark": validated["checkpoint"].event_watermark + 1}
    )
    event = DomainEvent(
        event_id=derived_uuid7(validated["turn"].turn_id, "event:qualification"),
        event_type=EventType.QUALIFICATION_UPDATED,
        occurred_at=qualification.calculated_at,
        tenant_id=updated.tenant_id,
        session_id=updated.session_id,
        sequence=checkpoint.event_watermark,
        correlation_id=validated["turn"].correlation_id,
        causation_id=validated["turn"].turn_id,
        actor_type="SYSTEM",
        actor_id=validated["turn"].actor_id,
        payload={
            "old_score": assessment.previous_score,
            "new_score": qualification.total_score,
            "buying_stage": qualification.buying_stage.value,
        },
    )
    return validate_node_update(
        WorkflowNode.UPDATE_QUALIFICATION,
        {
            "sales_state": updated,
            "checkpoint": checkpoint,
            "qualification_history": (*validated.get("qualification_history", ()), assessment)[-100:],
            "emitted_events": (*validated.get("emitted_events", ()), event),
        },
    )
