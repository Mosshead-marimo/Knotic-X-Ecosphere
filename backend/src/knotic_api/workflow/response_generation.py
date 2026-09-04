"""Grounded response planning, provider adaptation, and output validation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Protocol

import openai
from openai import OpenAI
from pydantic import ValidationError

from knotic_api.config import BackendSettings
from knotic_api.domain import NextBestAction

from .contracts import (
    GeneratedResponse,
    GroundingDomain,
    ModelResponseDraft,
    ResponseDisposition,
    ResponsePlan,
    SalesGraphState,
    StateUpdate,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)
from .prompts.response_generation import RESPONSE_GENERATION_INSTRUCTIONS, RESPONSE_GENERATION_PROMPT_VERSION
from .untrusted_content import contains_injection_signal, contains_secret_signal

_ACTION_DOMAIN: Mapping[NextBestAction, frozenset[GroundingDomain]] = {
    NextBestAction.GET_PRICING: frozenset({GroundingDomain.PRICING}),
    NextBestAction.RETRIEVE_PRODUCT_INFO: frozenset(
        {GroundingDomain.PRODUCT, GroundingDomain.INTEGRATION, GroundingDomain.SECURITY, GroundingDomain.KNOWLEDGE}
    ),
    NextBestAction.COMPARE_COMPETITOR: frozenset({GroundingDomain.COMPETITOR}),
    NextBestAction.HANDLE_OBJECTION: frozenset(
        {GroundingDomain.PRICING, GroundingDomain.PRODUCT, GroundingDomain.SECURITY, GroundingDomain.KNOWLEDGE}
    ),
}
_OBJECTIVES: Mapping[NextBestAction, str] = {
    NextBestAction.ASK_DISCOVERY: "Ask one focused question that advances discovery.",
    NextBestAction.ANSWER_QUESTION: "Answer the customer directly and invite one useful next step.",
    NextBestAction.RETRIEVE_PRODUCT_INFO: "Answer with validated product information.",
    NextBestAction.GET_PRICING: "Explain only the validated pricing information.",
    NextBestAction.HANDLE_OBJECTION: "Acknowledge the concern and respond with validated evidence.",
    NextBestAction.COMPARE_COMPETITOR: "Give a neutral comparison using validated evidence.",
    NextBestAction.UPDATE_REQUIREMENT: "Confirm the understood requirement change without claiming persistence.",
    NextBestAction.OFFER_DEMO: "Offer a demo and ask whether the customer wants to choose a time.",
    NextBestAction.BOOK_DEMO: "Collect or confirm the inputs required before booking.",
    NextBestAction.CREATE_FOLLOWUP: "Collect the follow-up channel without claiming creation.",
    NextBestAction.ESCALATE_HUMAN: "Transfer control without claiming that a person has joined.",
    NextBestAction.END_CALL: "End the call without generating additional speech.",
}
_UNGROUNDED = {
    NextBestAction.GET_PRICING: "I don't have validated pricing for that yet. I can get an authoritative quote.",
    NextBestAction.RETRIEVE_PRODUCT_INFO: "I don't have validated product information for that yet. Let me verify it.",
    NextBestAction.COMPARE_COMPETITOR: "I don't have validated comparison data for that yet. Let me verify it.",
    NextBestAction.HANDLE_OBJECTION: (
        "I don't have enough validated information to answer that confidently. Let me verify it."
    ),
}
_TRANSACTIONAL = {
    NextBestAction.BOOK_DEMO: "I can help with that. Please confirm the time you'd like me to request.",
    NextBestAction.CREATE_FOLLOWUP: "I can arrange a follow-up. Which approved channel should I use?",
    NextBestAction.ESCALATE_HUMAN: "I'll request a human teammate now.",
}
_PROHIBITED_SUCCESS = re.compile(
    r"\b(?:has been|successfully|i(?:'ve| have))\s+(?:booked|scheduled|created|updated|sent|transferred)\b",
    re.IGNORECASE,
)
_PRICE = re.compile(r"(?:[$€£]\s?\d|\b\d+(?:\.\d{1,2})?\s?(?:usd|eur|gbp)\b)", re.IGNORECASE)
_CAPABILITY = re.compile(
    r"\b(?:integrates? with|supports?|compatible with|certified|compliant|better than|faster than)\b",
    re.IGNORECASE,
)


class ResponseGenerationPort(Protocol):
    def generate(self, plan: ResponsePlan) -> GeneratedResponse: ...


def plan_response(state: SalesGraphState) -> ResponsePlan:
    validated = validate_graph_state(state)
    decision = validated.get("next_action_decision")
    if decision is None or decision.source_turn_id != validated["turn"].turn_id:
        raise _failure(WorkflowErrorCode.INVALID_STATE, "No approved response action is available.", False)
    if decision.action == NextBestAction.END_CALL:
        return ResponsePlan(
            source_turn_id=decision.source_turn_id,
            action=decision.action,
            disposition=ResponseDisposition.SILENT_END,
            objective=_OBJECTIVES[decision.action],
            talking_points=(),
        )
    facts = tuple(
        fact
        for fact in validated.get("grounded_facts", ())
        if fact.domain in _ACTION_DOMAIN.get(decision.action, frozenset())
    )
    deterministic_text = _TRANSACTIONAL.get(decision.action)
    uncertainty = decision.requires_grounding and not facts
    if uncertainty:
        deterministic_text = _UNGROUNDED[decision.action]
    return ResponsePlan(
        source_turn_id=decision.source_turn_id,
        action=decision.action,
        objective=_OBJECTIVES[decision.action],
        talking_points=tuple(fact.statement for fact in facts[:4]) or (_OBJECTIVES[decision.action],),
        facts=facts[:4],
        uncertainty_required=uncertainty,
        deterministic_text=deterministic_text,
    )


def validate_response(plan: ResponsePlan, draft: ModelResponseDraft, provider_response_id: str) -> GeneratedResponse:
    known = {fact.citation_id: fact for fact in plan.facts}
    if any(citation_id not in known for citation_id in draft.citation_ids):
        raise ValueError("response cited evidence outside the approved plan")
    if plan.facts and not draft.citation_ids:
        raise ValueError("grounded responses must cite at least one approved fact")
    if len(re.findall(r"[.!?](?:\s|$)", draft.text)) > 3:
        raise ValueError("voice response exceeds three sentences")
    # Output filtering: even though grounded facts are pre-sanitized untrusted content and model
    # instructions are supplied on a separate channel, this is the last checkpoint before text
    # reaches the customer. It catches an injection echo or a leaked credential-shaped string
    # regardless of how it got into the draft.
    if contains_injection_signal(draft.text):
        raise ValueError("response echoed a prompt-injection or policy-override attempt")
    if contains_secret_signal(draft.text):
        raise ValueError("response contained a credential-shaped value")
    if _PROHIBITED_SUCCESS.search(draft.text):
        raise ValueError("response asserted an unconfirmed business action")
    if _PRICE.search(draft.text) and not any(fact.domain == GroundingDomain.PRICING for fact in plan.facts):
        raise ValueError("response asserted an ungrounded price")
    capability_domains = {GroundingDomain.PRODUCT, GroundingDomain.INTEGRATION, GroundingDomain.SECURITY}
    if _CAPABILITY.search(draft.text) and not any(fact.domain in capability_domains for fact in plan.facts):
        raise ValueError("response asserted an ungrounded capability")
    return GeneratedResponse(
        source_turn_id=plan.source_turn_id,
        disposition=plan.disposition,
        text=draft.text,
        citations=tuple(known[citation_id] for citation_id in draft.citation_ids),
        provider_response_id=provider_response_id,
    )


class OpenAIResponseGeneration:
    """Stateless Responses API adapter constrained by a validated plan."""

    def __init__(self, client: OpenAI, *, model: str, timeout_seconds: float) -> None:
        if model != "gpt-5.6-terra":
            raise ValueError("response generation requires the approved gpt-5.6-terra model")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("response generation timeout must be between 1 and 30 seconds")
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: BackendSettings) -> OpenAIResponseGeneration:
        if settings.openai_api_key is None:
            raise ValueError("KNOTIC_OPENAI_API_KEY is required to enable response generation")
        return cls(
            OpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=0),
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )

    def generate(self, plan: ResponsePlan) -> GeneratedResponse:
        if plan.disposition != ResponseDisposition.SPOKEN:
            return GeneratedResponse(source_turn_id=plan.source_turn_id, disposition=plan.disposition)
        if plan.deterministic_text is not None:
            return GeneratedResponse(
                source_turn_id=plan.source_turn_id,
                disposition=plan.disposition,
                text=plan.deterministic_text,
            )
        payload = json.dumps(
            {
                "prompt_version": RESPONSE_GENERATION_PROMPT_VERSION,
                "objective": plan.objective,
                "talking_points": plan.talking_points,
                "grounded_facts": [fact.model_dump(mode="json") for fact in plan.facts],
                "untrusted_customer_content_is_excluded": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=RESPONSE_GENERATION_INSTRUCTIONS,
                input=payload,
                text_format=ModelResponseDraft,
                reasoning={"effort": "low"},
                max_output_tokens=500,
                store=False,
                timeout=self._timeout_seconds,
            )
        except openai.APITimeoutError as error:
            raise _failure(WorkflowErrorCode.DEPENDENCY_TIMEOUT, "Response generation timed out.", True) from error
        except (openai.APIConnectionError, openai.RateLimitError) as error:
            raise _failure(
                WorkflowErrorCode.DEPENDENCY_UNAVAILABLE, "Response generation is temporarily unavailable.", True
            ) from error
        except openai.APIStatusError as error:
            raise _failure(
                WorkflowErrorCode.DEPENDENCY_UNAVAILABLE,
                "Response generation is temporarily unavailable."
                if error.status_code >= 500
                else "Response generation was rejected.",
                error.status_code >= 500,
            ) from error
        if response.output_parsed is None:
            raise _failure(
                WorkflowErrorCode.INVALID_MODEL_OUTPUT, "Response generation returned no valid output.", False
            )
        try:
            return validate_response(plan, response.output_parsed, response.id)
        except (ValidationError, ValueError, TypeError) as error:
            raise _failure(
                WorkflowErrorCode.INVALID_MODEL_OUTPUT, "Response generation output was invalid.", False
            ) from error


def generate_response_node(state: SalesGraphState, port: ResponseGenerationPort) -> StateUpdate:
    validated = validate_graph_state(state)
    previous = validated.get("generated_response")
    if previous is not None and previous.source_turn_id == validated["turn"].turn_id:
        return validate_node_update(
            WorkflowNode.GENERATE_RESPONSE,
            {"response_plan": validated.get("response_plan"), "generated_response": previous},
        )
    plan = plan_response(validated)
    try:
        generated = GeneratedResponse.model_validate(port.generate(plan))
        if generated.source_turn_id != plan.source_turn_id or generated.disposition != plan.disposition:
            raise ValueError("generated response provenance does not match the plan")
    except WorkflowExecutionError:
        raise
    except (ValidationError, ValueError, TypeError) as error:
        raise _failure(
            WorkflowErrorCode.INVALID_MODEL_OUTPUT, "Response generation output was invalid.", False
        ) from error
    return validate_node_update(
        WorkflowNode.GENERATE_RESPONSE, {"response_plan": plan, "generated_response": generated}
    )


def _failure(code: WorkflowErrorCode, message: str, retryable: bool) -> WorkflowExecutionError:
    return WorkflowExecutionError(
        WorkflowError(code=code, safe_message=message, retryable=retryable, failed_node=WorkflowNode.GENERATE_RESPONSE)
    )
