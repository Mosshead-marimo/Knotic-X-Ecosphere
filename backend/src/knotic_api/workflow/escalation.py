"""Deterministic FR-13 escalation policy (P5-T007)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ApprovalRequirement


class EscalationTrigger(StrEnum):
    EXPLICIT_REQUEST = "EXPLICIT_REQUEST"
    ENTERPRISE_OPPORTUNITY = "ENTERPRISE_OPPORTUNITY"
    COMPLEX_NEGOTIATION = "COMPLEX_NEGOTIATION"
    SECURITY_OR_LEGAL = "SECURITY_OR_LEGAL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CUSTOMER_FRUSTRATION = "CUSTOMER_FRUSTRATION"
    UNSUPPORTED_QUESTION = "UNSUPPORTED_QUESTION"
    UNAUTHORIZED_DISCOUNT = "UNAUTHORIZED_DISCOUNT"


class EscalationPriority(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class EscalationSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    explicit_request: bool = False
    enterprise_opportunity: bool = False
    complex_negotiation: bool = False
    security_or_legal: bool = False
    model_confidence: float = Field(default=1.0, ge=0, le=1)
    frustration_events: int = Field(default=0, ge=0, le=100)
    unsupported_question: bool = False
    unauthorized_discount: bool = False


class EscalationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_escalate: bool
    primary_trigger: EscalationTrigger | None
    trigger_reasons: tuple[EscalationTrigger, ...]
    priority: EscalationPriority | None
    approval: ApprovalRequirement
    blocked_action: Literal["DISCOUNT"] | None = None
    policy_version: Literal["fr13-v1"] = "fr13-v1"

    def persistence_payload(self) -> dict[str, object]:
        return {
            "should_escalate": self.should_escalate,
            "primary_trigger": self.primary_trigger.value if self.primary_trigger else None,
            "trigger_reasons": [reason.value for reason in self.trigger_reasons],
            "priority": self.priority.value if self.priority else None,
            "approval": self.approval.value,
            "blocked_action": self.blocked_action,
            "policy_version": self.policy_version,
        }


_PRECEDENCE = (
    EscalationTrigger.UNAUTHORIZED_DISCOUNT,
    EscalationTrigger.SECURITY_OR_LEGAL,
    EscalationTrigger.EXPLICIT_REQUEST,
    EscalationTrigger.ENTERPRISE_OPPORTUNITY,
    EscalationTrigger.COMPLEX_NEGOTIATION,
    EscalationTrigger.CUSTOMER_FRUSTRATION,
    EscalationTrigger.UNSUPPORTED_QUESTION,
    EscalationTrigger.LOW_CONFIDENCE,
)


def evaluate_escalation(signals: EscalationSignals) -> EscalationDecision:
    present = {
        EscalationTrigger.EXPLICIT_REQUEST: signals.explicit_request,
        EscalationTrigger.ENTERPRISE_OPPORTUNITY: signals.enterprise_opportunity,
        EscalationTrigger.COMPLEX_NEGOTIATION: signals.complex_negotiation,
        EscalationTrigger.SECURITY_OR_LEGAL: signals.security_or_legal,
        EscalationTrigger.LOW_CONFIDENCE: signals.model_confidence < 0.55,
        EscalationTrigger.CUSTOMER_FRUSTRATION: signals.frustration_events >= 2,
        EscalationTrigger.UNSUPPORTED_QUESTION: signals.unsupported_question,
        EscalationTrigger.UNAUTHORIZED_DISCOUNT: signals.unauthorized_discount,
    }
    reasons = tuple(trigger for trigger in _PRECEDENCE if present[trigger])
    if not reasons:
        return EscalationDecision(
            should_escalate=False,
            primary_trigger=None,
            trigger_reasons=(),
            priority=None,
            approval=ApprovalRequirement.NONE,
        )
    urgent = {EscalationTrigger.UNAUTHORIZED_DISCOUNT, EscalationTrigger.SECURITY_OR_LEGAL}
    high = {
        EscalationTrigger.EXPLICIT_REQUEST,
        EscalationTrigger.ENTERPRISE_OPPORTUNITY,
        EscalationTrigger.COMPLEX_NEGOTIATION,
        EscalationTrigger.CUSTOMER_FRUSTRATION,
    }
    priority = (
        EscalationPriority.URGENT
        if set(reasons) & urgent
        else EscalationPriority.HIGH
        if set(reasons) & high
        else EscalationPriority.NORMAL
    )
    return EscalationDecision(
        should_escalate=True,
        primary_trigger=reasons[0],
        trigger_reasons=reasons,
        priority=priority,
        approval=ApprovalRequirement.POLICY,
        blocked_action="DISCOUNT" if signals.unauthorized_discount else None,
    )


__all__ = [
    "EscalationDecision",
    "EscalationPriority",
    "EscalationSignals",
    "EscalationTrigger",
    "evaluate_escalation",
]
