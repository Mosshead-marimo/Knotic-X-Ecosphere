"""Private authenticated ASGI MCP gateway with fail-closed dispatch."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
import redis
from pydantic import ValidationError

from .audit import ApprovalPolicy, AuditSink, InMemoryAuditSink, audit_record
from .config import McpSettings, load_mcp_settings
from .contracts import ToolEnvelope, ToolError, ToolInvocation, ToolStatus, TrustedContext
from .knowledge import KnowledgeStore, PgvectorRetrievalService
from .registry import ToolRegistry, arguments_are_valid

AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], Any, AsgiSend], Awaitable[None]]


class RateLimiter:
    def __init__(self, *, limit: int = 120, window_seconds: float = 60) -> None:
        self._limit, self._window, self._hits = limit, window_seconds, defaultdict(deque[str])

    def allow(self, key: str) -> bool:
        now, values = time.monotonic(), self._hits[key]
        while values and now - values[0] >= self._window:
            values.popleft()
        if len(values) >= self._limit:
            return False
        values.append(now)
        return True


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    return {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}


async def _body(receive: Any, maximum: int = 32_768) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > maximum:
            raise ValueError("request body exceeds the gateway limit")
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _json(send: AsgiSend, status: int, payload: dict[str, Any]) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": json.dumps(payload, default=str, separators=(",", ":")).encode()})


def _failed(invocation: ToolInvocation, code: str, message: str, *, retryable: bool = False) -> ToolEnvelope:
    now = datetime.now(UTC)
    return ToolEnvelope(tool_call_id=invocation.tool_call_id, tool=invocation.tool, version=invocation.version, status=ToolStatus.FAILED, error=ToolError(code=code, message=message, retryable=retryable), started_at=now, completed_at=now)


def create_app(settings: McpSettings | None = None, *, registry: ToolRegistry | None = None, audit_sink: AuditSink | None = None) -> AsgiApp:
    resolved, sink, limiter = settings or load_mcp_settings(), audit_sink or InMemoryAuditSink(), RateLimiter()
    tool_registry = registry or ToolRegistry()
    retrieval = PgvectorRetrievalService(KnowledgeStore())

    def knowledge_search(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        matches = retrieval.search(tenant_id=context.tenant_id, query=str(invocation.arguments["query"]), domains=set(invocation.arguments["domains"]), limit=int(invocation.arguments["limit"]))
        if not matches:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved evidence matched this query.")
        now = datetime.now(UTC)
        return ToolEnvelope(tool_call_id=invocation.tool_call_id, tool=invocation.tool, version=1, status=ToolStatus.SUCCEEDED, data={"matches": [{"text": match.text, "score": match.score, "citation": match.citation} for match in matches], "index_version": "v1"}, started_at=now, completed_at=now)

    tool_registry.register("knowledge.search", knowledge_search)
    approval = ApprovalPolicy(hmac_key=resolved.auth_token.get_secret_value().encode())

    async def app(scope: dict[str, Any], receive: Any, send: AsgiSend) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path")
        if path == "/health/live":
            await _json(send, 200, {"status": "ok"})
            return
        if path == "/health/ready":
            checks: dict[str, str] = {}
            try:
                with psycopg.connect(resolved.database_url.get_secret_value(), connect_timeout=2) as connection:
                    connection.execute("select 1")
                checks["postgres"] = "ok"
                redis.Redis.from_url(resolved.redis_url.get_secret_value(), socket_connect_timeout=2).ping()
                checks["redis"] = "ok"
            except Exception:
                await _json(send, 503, {"status": "not_ready", "checks": checks})
                return
            await _json(send, 200, {"status": "ok", "checks": checks})
            return
        if not path.startswith("/v1/tools/") or scope.get("method") != "POST":
            await _json(send, 404, {"status": "not_found"})
            return
        headers = _headers(scope)
        supplied = headers.get("authorization", "").removeprefix("Bearer ")
        tokens = [resolved.auth_token.get_secret_value()] + ([resolved.previous_auth_token.get_secret_value()] if resolved.previous_auth_token else [])
        if not supplied or not any(hmac.compare_digest(supplied, token) for token in tokens):
            await _json(send, 401, {"error": {"code": "UNAUTHENTICATED", "message": "Workload authentication failed."}})
            return
        try:
            context = TrustedContext.model_validate_json(headers["x-knotic-trusted-context"])
            invocation = ToolInvocation.model_validate(json.loads((await _body(receive)).decode("utf-8")))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
            await _json(send, 400, {"error": {"code": "INVALID_ARGUMENT", "message": "The invocation envelope is invalid."}})
            return
        requested = path.removeprefix("/v1/tools/")
        if requested != invocation.tool or not limiter.allow(f"{context.tenant_id}:{invocation.tool}"):
            code = "RATE_LIMITED" if requested == invocation.tool else "INVALID_ARGUMENT"
            await _json(send, 429 if code == "RATE_LIMITED" else 400, _failed(invocation, code, "Tool path is invalid or rate limited.").model_dump(mode="json"))
            return
        definition = tool_registry.definition(invocation.tool, invocation.version)
        if definition is None:
            await _json(send, 404, _failed(invocation, "NOT_FOUND", "Unknown tool or version.").model_dump(mode="json"))
            return
        if not arguments_are_valid(definition, invocation.arguments):
            await _json(
                send,
                400,
                _failed(invocation, "INVALID_ARGUMENT", "Tool arguments do not match its schema.").model_dump(mode="json"),
            )
            return
        if definition.scope not in context.scopes:
            envelope, decision = _failed(invocation, "PERMISSION_DENIED", "The workload lacks this tool scope."), "REJECTED"
        elif context.deadline_at <= datetime.now(UTC):
            envelope, decision = _failed(invocation, "TIMEOUT", "The invocation deadline has expired."), "REJECTED"
        elif definition.side_effect and invocation.idempotency_key is None:
            envelope, decision = _failed(invocation, "INVALID_ARGUMENT", "This tool requires an idempotency key."), "REJECTED"
        else:
            decision = approval.decide(level=definition.approval, context=context, invocation=invocation)
            if decision not in {"NOT_REQUIRED", "APPROVED"}:
                envelope = _failed(invocation, "APPROVAL_REQUIRED" if decision == "REQUIRED" else "POLICY_DENIED", "Approval is missing or invalid.")
            else:
                started = time.monotonic()
                try:
                    envelope = await asyncio.wait_for(asyncio.to_thread(tool_registry.invoke, context, invocation), timeout=definition.timeout_ms / 1000)
                except (TimeoutError, asyncio.TimeoutError):
                    envelope = _failed(invocation, "TIMEOUT", "The provider did not respond in time.", retryable=True)
                except (KeyError, ValueError):
                    envelope = _failed(invocation, "DEPENDENCY_UNAVAILABLE", "The tool is unavailable.", retryable=True)
                latency = round((time.monotonic() - started) * 1000)
                sink.append(audit_record(context=context, invocation=invocation, envelope=envelope, approval_decision=decision, latency_ms=latency, idempotency_key_hmac=approval.idempotency_hmac(invocation.idempotency_key)))
                await _json(send, 200, envelope.model_dump(mode="json"))
                return
        sink.append(audit_record(context=context, invocation=invocation, envelope=envelope, approval_decision=decision, latency_ms=None, idempotency_key_hmac=approval.idempotency_hmac(invocation.idempotency_key)))
        await _json(send, 200, envelope.model_dump(mode="json"))

    return app
