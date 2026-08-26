"""Provider-neutral turn-understanding port and OpenAI Responses adapter."""

from __future__ import annotations

import json
from typing import Protocol

import openai
from openai import OpenAI
from pydantic import ValidationError

from knotic_api.config import BackendSettings

from .contracts import (
    ModelTurnUnderstanding,
    SalesGraphState,
    SemanticTurn,
    StateUpdate,
    TurnUnderstanding,
    WorkflowError,
    WorkflowErrorCode,
    WorkflowExecutionError,
    WorkflowNode,
    validate_graph_state,
    validate_node_update,
)
from .prompts.turn_understanding import TURN_UNDERSTANDING_INSTRUCTIONS, TURN_UNDERSTANDING_PROMPT_VERSION


class TurnUnderstandingPort(Protocol):
    def understand(self, turn: SemanticTurn) -> TurnUnderstanding: ...


class OpenAITurnUnderstanding:
    """Responses API adapter with schema parsing and no retained response context."""

    def __init__(self, client: OpenAI, *, model: str, timeout_seconds: float) -> None:
        if model != "gpt-5.6-terra":
            raise ValueError("turn understanding requires the approved gpt-5.6-terra model")
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("turn understanding timeout must be between 1 and 30 seconds")
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: BackendSettings) -> OpenAITurnUnderstanding:
        if settings.openai_api_key is None:
            raise ValueError("KNOTIC_OPENAI_API_KEY is required to enable model-backed turn understanding")
        return cls(
            OpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=0),
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
        )

    def understand(self, turn: SemanticTurn) -> TurnUnderstanding:
        payload = json.dumps(
            {
                "prompt_version": TURN_UNDERSTANDING_PROMPT_VERSION,
                "locale_hint": turn.locale,
                "untrusted_customer_utterance": turn.text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=TURN_UNDERSTANDING_INSTRUCTIONS,
                input=payload,
                text_format=ModelTurnUnderstanding,
                reasoning={"effort": "low"},
                max_output_tokens=2_000,
                store=False,
                timeout=self._timeout_seconds,
            )
        except openai.APITimeoutError as error:
            raise self._failure(WorkflowErrorCode.DEPENDENCY_TIMEOUT, "Turn understanding timed out.", True) from error
        except (openai.APIConnectionError, openai.RateLimitError) as error:
            raise self._failure(
                WorkflowErrorCode.DEPENDENCY_UNAVAILABLE, "Turn understanding is temporarily unavailable.", True
            ) from error
        except openai.APIStatusError as error:
            retryable = error.status_code >= 500
            raise self._failure(
                WorkflowErrorCode.DEPENDENCY_UNAVAILABLE,
                "Turn understanding is temporarily unavailable." if retryable else "Turn understanding was rejected.",
                retryable,
            ) from error
        parsed = response.output_parsed
        if parsed is None:
            raise self._failure(
                WorkflowErrorCode.INVALID_MODEL_OUTPUT, "Turn understanding returned no valid output.", False
            )
        try:
            return TurnUnderstanding(
                **parsed.model_dump(),
                source_turn_id=turn.turn_id,
                provider_response_id=response.id,
            ).validate_against(turn)
        except (ValidationError, ValueError) as error:
            raise self._failure(
                WorkflowErrorCode.INVALID_MODEL_OUTPUT, "Turn understanding output was invalid.", False
            ) from error

    @staticmethod
    def _failure(code: WorkflowErrorCode, message: str, retryable: bool) -> WorkflowExecutionError:
        return WorkflowExecutionError(
            WorkflowError(
                code=code, safe_message=message, retryable=retryable, failed_node=WorkflowNode.UNDERSTAND_TURN
            )
        )


def understand_turn_node(state: SalesGraphState, port: TurnUnderstandingPort) -> StateUpdate:
    validated = validate_graph_state(state)
    try:
        understanding = TurnUnderstanding.model_validate(port.understand(validated["turn"])).validate_against(
            validated["turn"]
        )
    except WorkflowExecutionError:
        raise
    except (ValidationError, ValueError, TypeError) as error:
        raise OpenAITurnUnderstanding._failure(
            WorkflowErrorCode.INVALID_MODEL_OUTPUT, "Turn understanding output was invalid.", False
        ) from error
    return validate_node_update(WorkflowNode.UNDERSTAND_TURN, {"understanding": understanding})
