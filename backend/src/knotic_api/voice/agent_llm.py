"""Private, Agora-only bridge into this backend's governed LangGraph sales workflow.

Agora's managed conversational agent calls this endpoint as an OpenAI-compatible chat-completions
API for every conversational turn (see the ``llm`` block Agora's join API accepts, and
https://docs.agora.io/en/conversational-ai/rest-api/join). It must never be reachable except by
Agora: callers authenticate with a static bearer token (``KNOTIC_AGORA_LLM_API_KEY``) *and* a
per-session signed binding token (see ``managed_agent.sign_session_binding``) that this backend
itself embedded in the ``llm.url`` it gave Agora at agent-start time -- neither the bearer token
nor the binding can be forged or guessed, so a customer's spoken words (which Agora relays into
this request's message content) can never redirect a turn to a different tenant or session.

Every real turn is executed through the exact same governed pipeline the rest of this codebase
already built and tested (:mod:`knotic_api.workflow.execution`, the compiled LangGraph in
:mod:`knotic_api.workflow.graph`) -- this file adds no new business logic of its own, only the
transport, authentication, and OpenAI response-shape adapter around that existing pipeline.

Known limitation: this does not yet persist the customer/assistant turn into the durable
``messages`` table (see ``docs/CHANGES_MADE.md`` for why that was deliberately deferred rather
than guessed at). The governed workflow checkpoint itself IS durably persisted by
``execute_turn``/``PostgresWorkflowCheckpointStore``, so policy decisions and qualification state
are real and durable; only the raw transcript log is not yet written from this path.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.persistence.hydration import SalesStateHydrator, StateRecoveryError
from knotic_api.workflow.contracts import CheckpointIdentity, SalesGraphState, SemanticTurn
from knotic_api.workflow.execution import (
    GraphInvocationPort,
    TurnExecutionStatus,
    WorkflowCheckpointStore,
    execute_turn,
)

from .managed_agent import InvalidSessionBinding, verify_session_binding

_FALLBACK_MESSAGE = "I'm sorry, I'm having trouble with that right now. Could you say that again?"
_DEFAULT_LOCALE = "en-US"


@dataclass(frozen=True, slots=True)
class AgentLlmDependencies:
    hydrator: SalesStateHydrator
    graph_invoker: GraphInvocationPort
    checkpoint_store: WorkflowCheckpointStore
    llm_api_key: str
    session_security_key: bytes


def _latest_user_message(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


def _completion_body(completion_id: str, created: int, model: str, text: str) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def _stream_single_chunk(completion_id: str, created: int, model: str, text: str) -> Iterator[str]:
    # Agora's custom-LLM contract expects an OpenAI-compatible SSE stream when the caller sets
    # stream=true. This yields the full reply as one chunk rather than incremental tokens --
    # correct and consumable, but coarser-grained than real token-by-token streaming, which is a
    # documented follow-up rather than a silent gap.
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
    }
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(chunk)}\n\n"
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


class AgentLlmApi:
    def __init__(self, app: Flask, dependencies: AgentLlmDependencies) -> None:
        self.app = app
        self.dependencies = dependencies

    def register(self) -> None:
        self.app.add_url_rule(
            "/api/v1/internal/voice/chat/completions", view_func=self.chat_completions, methods=["POST"]
        )

    def chat_completions(self) -> ResponseReturnValue:
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not supplied or not hmac.compare_digest(supplied, self.dependencies.llm_api_key):
            response = jsonify(error={"code": "AUTHENTICATION_REQUIRED", "message": "Authentication is required."})
            response.status_code = 401
            return response

        try:
            binding = verify_session_binding(self.dependencies.session_security_key, request.args.get("binding", ""))
        except InvalidSessionBinding:
            response = jsonify(
                error={"code": "INVALID_SESSION_BINDING", "message": "The session binding is invalid or expired."}
            )
            response.status_code = 403
            return response

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            response = jsonify(
                error={"code": "VALIDATION_FAILED", "message": "A JSON chat-completions request is required."}
            )
            response.status_code = 400
            return response

        customer_text = _latest_user_message(payload)
        stream = bool(payload.get("stream", False))
        reply_text = self._run_governed_turn(binding.tenant_id, binding.session_id, customer_text)

        completion_id = f"chatcmpl-{new_uuid7()}"
        created = int(datetime.now(UTC).timestamp())
        model = str(payload.get("model") or "knotic-sales-agent")
        if stream:
            return Response(
                _stream_single_chunk(completion_id, created, model, reply_text),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return jsonify(_completion_body(completion_id, created, model, reply_text))

    def _run_governed_turn(self, tenant_id: UUID, session_id: UUID, customer_text: str) -> str:
        if not customer_text:
            return _FALLBACK_MESSAGE
        try:
            hydrated = self.dependencies.hydrator.load(tenant_id, session_id)
            sales_state = hydrated.envelope.state
            checkpoint = CheckpointIdentity.for_state(sales_state, event_watermark=hydrated.envelope.event_watermark)
            turn = SemanticTurn(
                tenant_id=tenant_id,
                session_id=session_id,
                turn_id=new_uuid7(),
                correlation_id=new_uuid7(),
                actor_id=new_uuid7(),
                sequence=hydrated.envelope.event_watermark + 1,
                text=customer_text[:10_000],
                locale=_DEFAULT_LOCALE,
                occurred_at=datetime.now(UTC),
            )
            state: SalesGraphState = {
                "schema_version": 1,
                "checkpoint": checkpoint,
                "sales_state": sales_state,
                "turn": turn,
            }
            result = execute_turn(
                state,
                invoker=self.dependencies.graph_invoker,
                checkpoints=self.dependencies.checkpoint_store,
            )
            if result.status != TurnExecutionStatus.COMMITTED or result.state is None:
                return _FALLBACK_MESSAGE
            generated = result.state.get("generated_response")
            if generated is None or generated.text is None:
                return _FALLBACK_MESSAGE
            return generated.text
        except StateRecoveryError:
            self.app.logger.warning("agora_managed_agent_turn_state_unavailable")
            return _FALLBACK_MESSAGE
        except Exception:
            self.app.logger.exception("agora_managed_agent_turn_failed")
            return _FALLBACK_MESSAGE


def register_agent_llm_api(app: Flask, dependencies: AgentLlmDependencies) -> None:
    AgentLlmApi(app, dependencies).register()
