"""Private authenticated ASGI MCP gateway with fail-closed dispatch."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import redis
from pydantic import ValidationError

from .audit import ApprovalPolicy, AuditSink, InMemoryAuditSink, audit_record
from .cache import (
    TOOL_CACHE_POLICIES,
    CacheEntry,
    CachePolicy,
    InMemoryToolResultCache,
    ProviderHealthTracker,
    ToolResultCache,
    cache_key,
)
from .config import McpSettings, load_mcp_settings
from .contracts import ToolEnvelope, ToolError, ToolInvocation, ToolStatus, TrustedContext
from .knowledge import KnowledgeQueryService, KnowledgeStore, PgvectorRetrievalService
from .observability import McpObservability
from .registry import ToolRegistry, arguments_are_valid
from .sales import PricingCatalog, compare_plans, decide_next_action, get_quote, qualify_lead

# Groups tools by the external dependency their handler actually calls, so provider health is
# tracked per dependency rather than per tool (five knowledge tools share one retrieval path).
_PROVIDER_FOR_TOOL: dict[str, str] = {
    "knowledge.search": "knowledge_retrieval",
    "product.search": "knowledge_retrieval",
    "product.get_feature": "knowledge_retrieval",
    "product.get_integration": "knowledge_retrieval",
    "competitor.compare": "knowledge_retrieval",
    "security.get_information": "knowledge_retrieval",
    "pricing.get_quote": "pricing_catalog",
    "pricing.compare_plans": "pricing_catalog",
}
_RETRYABLE_FAILURE_CODES = frozenset({"TIMEOUT", "DEPENDENCY_UNAVAILABLE"})

AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], Any, AsgiSend], Awaitable[None]]


class RateLimiter:
    def __init__(self, *, limit: int = 120, window_seconds: float = 60) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

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


async def _json(
    send: AsgiSend, status: int, payload: dict[str, Any], *, extra_headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"), *(extra_headers or [])],
        }
    )
    await send({"type": "http.response.body", "body": json.dumps(payload, default=str, separators=(",", ":")).encode()})


def _failed(invocation: ToolInvocation, code: str, message: str, *, retryable: bool = False) -> ToolEnvelope:
    now = datetime.now(UTC)
    return ToolEnvelope(
        tool_call_id=invocation.tool_call_id,
        tool=invocation.tool,
        version=invocation.version,
        status=ToolStatus.FAILED,
        error=ToolError(code=code, message=message, retryable=retryable),
        started_at=now,
        completed_at=now,
    )


def _succeeded(invocation: ToolInvocation, data: dict[str, Any]) -> ToolEnvelope:
    now = datetime.now(UTC)
    return ToolEnvelope(
        tool_call_id=invocation.tool_call_id,
        tool=invocation.tool,
        version=invocation.version,
        status=ToolStatus.SUCCEEDED,
        data=data,
        started_at=now,
        completed_at=now,
    )


def create_app(
    settings: McpSettings | None = None,
    *,
    registry: ToolRegistry | None = None,
    audit_sink: AuditSink | None = None,
    knowledge_store: KnowledgeStore | None = None,
    pricing_catalog: PricingCatalog | None = None,
    tool_cache: ToolResultCache | None = None,
    provider_health: ProviderHealthTracker | None = None,
    observability: McpObservability | None = None,
) -> AsgiApp:
    resolved, sink, limiter = settings or load_mcp_settings(), audit_sink or InMemoryAuditSink(), RateLimiter()
    tool_registry = registry or ToolRegistry()
    retrieval = PgvectorRetrievalService(knowledge_store or KnowledgeStore())
    knowledge_query = KnowledgeQueryService(retrieval)
    catalog = pricing_catalog or PricingCatalog.load_default()
    cache = tool_cache or InMemoryToolResultCache()
    health = provider_health or ProviderHealthTracker()
    telemetry = observability or McpObservability(
        service_name=resolved.service_name,
        environment=resolved.environment.value,
        otlp_endpoint=resolved.otel_exporter_otlp_endpoint,
    )

    def knowledge_search(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        matches = retrieval.search(
            tenant_id=context.tenant_id,
            query=str(invocation.arguments["query"]),
            domains=set(invocation.arguments["domains"]),
            limit=int(invocation.arguments["limit"]),
        )
        if not matches:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved evidence matched this query.")
        return _succeeded(
            invocation,
            {
                "matches": [
                    {"text": match.text, "score": match.score, "citation": match.citation} for match in matches
                ],
                "index_version": "v1",
            },
        )

    def product_search(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result = knowledge_query.search_products(
            tenant_id=context.tenant_id,
            query=str(invocation.arguments["query"]),
            limit=int(invocation.arguments["limit"]),
        )
        if result is None:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved product evidence matched this query.")
        return _succeeded(invocation, result)

    def product_get_feature(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result = knowledge_query.get_feature(tenant_id=context.tenant_id, feature=str(invocation.arguments["feature"]))
        if result is None:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved evidence describes this feature.")
        return _succeeded(invocation, result)

    def product_get_integration(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result = knowledge_query.get_integration(
            tenant_id=context.tenant_id, integration=str(invocation.arguments["integration"])
        )
        if result is None:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved evidence describes this integration.")
        return _succeeded(invocation, result)

    def competitor_compare(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result = knowledge_query.compare_competitor(
            tenant_id=context.tenant_id,
            competitor=str(invocation.arguments["competitor"]),
            dimensions=[str(item) for item in invocation.arguments["dimensions"]],
        )
        if result is None:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved evidence mentions this competitor.")
        return _succeeded(invocation, result)

    def security_get_information(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result, denied = knowledge_query.get_security_information(
            tenant_id=context.tenant_id,
            topic=str(invocation.arguments["topic"]),
            customer_clearance=invocation.arguments.get("customer_clearance"),
        )
        if denied:
            return _failed(invocation, "PERMISSION_DENIED", "This security topic requires customer clearance.")
        if result is None:
            return _failed(invocation, "NO_GROUNDED_RESULT", "No approved evidence answers this security topic.")
        return _succeeded(invocation, result)

    def pricing_get_quote(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result = get_quote(
            catalog,
            users=int(invocation.arguments["users"]),
            billing_period=invocation.arguments["billing_period"],
            plan_id=invocation.arguments.get("plan"),
        )
        if result is None:
            return _failed(invocation, "PRICE_UNAVAILABLE", "No current authoritative price for this request.")
        return _succeeded(invocation, result)

    def pricing_compare_plans(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        result = compare_plans(
            catalog,
            plan_ids=[str(item) for item in invocation.arguments["plan_ids"]],
            users=int(invocation.arguments["users"]),
        )
        if result is None:
            return _failed(invocation, "PRICE_UNAVAILABLE", "One or more requested plans have no current price.")
        return _succeeded(invocation, result)

    def lead_qualify(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        try:
            result = qualify_lead(
                need=int(invocation.arguments["need"]),
                product_fit=int(invocation.arguments["product_fit"]),
                deployment_fit=int(invocation.arguments["deployment_fit"]),
                timeline=int(invocation.arguments["timeline"]),
                authority=int(invocation.arguments["authority"]),
                budget=int(invocation.arguments["budget"]),
                purchase_intent=int(invocation.arguments["purchase_intent"]),
            )
        except ValueError:
            return _failed(invocation, "INVALID_ARGUMENT", "Qualification components are out of bounds.")
        return _succeeded(invocation, result)

    def lead_next_action(context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        try:
            result = decide_next_action(
                intent=str(invocation.arguments["intent"]),
                stage=str(invocation.arguments["stage"]),
                explicit_request=invocation.arguments.get("explicit_request"),
            )
        except ValueError:
            return _failed(invocation, "INVALID_ARGUMENT", "The buying stage is not recognized.")
        return _succeeded(invocation, result)

    def _register_default(name: str, handler: Any) -> None:
        # A caller-supplied registry may pre-populate a handler (tests use this to simulate a
        # failing provider for a specific tool); the gateway's own default never overwrites one.
        if not tool_registry.has_handler(name):
            tool_registry.register(name, handler)

    _register_default("knowledge.search", knowledge_search)
    _register_default("product.search", product_search)
    _register_default("product.get_feature", product_get_feature)
    _register_default("product.get_integration", product_get_integration)
    _register_default("competitor.compare", competitor_compare)
    _register_default("security.get_information", security_get_information)
    _register_default("pricing.get_quote", pricing_get_quote)
    _register_default("pricing.compare_plans", pricing_compare_plans)
    _register_default("lead.qualify", lead_qualify)
    _register_default("lead.next_action", lead_next_action)
    approval = ApprovalPolicy(hmac_key=resolved.auth_token.get_secret_value().encode())

    async def app(scope: dict[str, Any], receive: Any, send: AsgiSend) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path")
        if not isinstance(path, str):
            await _json(send, 404, {"status": "not_found"})
            return
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
            checks.update(health.status())
            await _json(send, 200, {"status": "ok", "checks": checks})
            return
        if path == "/internal/metrics":
            configured = resolved.metrics_auth_token
            if configured is None:
                await _json(send, 404, {"status": "not_found"})
                return
            supplied = _headers(scope).get("authorization", "").removeprefix("Bearer ")
            if not supplied or not hmac.compare_digest(supplied, configured.get_secret_value()):
                await _json(send, 401, {"error": {"code": "UNAUTHENTICATED", "message": "Metrics token invalid."}})
                return
            telemetry.sample_provider_health(health, tuple(set(_PROVIDER_FOR_TOOL.values())))
            body, content_type = telemetry.render()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", content_type.encode()), (b"cache-control", b"no-store")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        if not path.startswith("/v1/tools/") or scope.get("method") != "POST":
            await _json(send, 404, {"status": "not_found"})
            return
        headers = _headers(scope)
        supplied = headers.get("authorization", "").removeprefix("Bearer ")
        tokens = [resolved.auth_token.get_secret_value()] + (
            [resolved.previous_auth_token.get_secret_value()] if resolved.previous_auth_token else []
        )
        if not supplied or not any(hmac.compare_digest(supplied, token) for token in tokens):
            await _json(send, 401, {"error": {"code": "UNAUTHENTICATED", "message": "Workload authentication failed."}})
            return
        try:
            context = TrustedContext.model_validate_json(headers["x-knotic-trusted-context"])
            invocation = ToolInvocation.model_validate(json.loads((await _body(receive)).decode("utf-8")))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError):
            await _json(
                send, 400, {"error": {"code": "INVALID_ARGUMENT", "message": "The invocation envelope is invalid."}}
            )
            return
        requested = path.removeprefix("/v1/tools/")
        if requested != invocation.tool or not limiter.allow(f"{context.tenant_id}:{invocation.tool}"):
            code = "RATE_LIMITED" if requested == invocation.tool else "INVALID_ARGUMENT"
            await _json(
                send,
                429 if code == "RATE_LIMITED" else 400,
                _failed(invocation, code, "Tool path is invalid or rate limited.").model_dump(mode="json"),
            )
            return
        definition = tool_registry.definition(invocation.tool, invocation.version)
        if definition is None:
            await _json(send, 404, _failed(invocation, "NOT_FOUND", "Unknown tool or version.").model_dump(mode="json"))
            return
        if not arguments_are_valid(definition, invocation.arguments):
            await _json(
                send,
                400,
                _failed(invocation, "INVALID_ARGUMENT", "Tool arguments do not match its schema.").model_dump(
                    mode="json"
                ),
            )
            return
        if definition.scope not in context.scopes:
            envelope, decision = (
                _failed(invocation, "PERMISSION_DENIED", "The workload lacks this tool scope."),
                "REJECTED",
            )
        elif context.deadline_at <= datetime.now(UTC):
            envelope, decision = _failed(invocation, "TIMEOUT", "The invocation deadline has expired."), "REJECTED"
        elif definition.side_effect and invocation.idempotency_key is None:
            envelope, decision = (
                _failed(invocation, "INVALID_ARGUMENT", "This tool requires an idempotency key."),
                "REJECTED",
            )
        else:
            decision = approval.decide(level=definition.approval, context=context, invocation=invocation)
            if decision not in {"NOT_REQUIRED", "APPROVED"}:
                envelope = _failed(
                    invocation,
                    "APPROVAL_REQUIRED" if decision == "REQUIRED" else "POLICY_DENIED",
                    "Approval is missing or invalid.",
                )
            else:
                policy = TOOL_CACHE_POLICIES.get(invocation.tool, CachePolicy(cacheable=False))
                provider = _PROVIDER_FOR_TOOL.get(invocation.tool)
                key = (
                    cache_key(
                        tenant_id=context.tenant_id,
                        tool=invocation.tool,
                        version=invocation.version,
                        arguments=invocation.arguments,
                    )
                    if policy.cacheable
                    else None
                )
                now = datetime.now(UTC)
                cached = cache.get(key) if key else None
                cache_status = "MISS"
                latency = 0
                if cached is not None and cached.is_fresh(at=now):
                    envelope, cache_status = cached.envelope, "HIT"
                else:
                    started = time.monotonic()
                    try:
                        envelope = await asyncio.wait_for(
                            asyncio.to_thread(tool_registry.invoke, context, invocation),
                            timeout=definition.timeout_ms / 1000,
                        )
                        if provider:
                            health.record_success(provider)
                    except TimeoutError:
                        envelope = _failed(
                            invocation, "TIMEOUT", "The provider did not respond in time.", retryable=True
                        )
                        if provider:
                            health.record_failure(provider)
                    except (KeyError, ValueError):
                        envelope = _failed(
                            invocation, "DEPENDENCY_UNAVAILABLE", "The tool is unavailable.", retryable=True
                        )
                        if provider:
                            health.record_failure(provider)
                    latency = round((time.monotonic() - started) * 1000)
                    provider_failed = (
                        envelope.status == ToolStatus.FAILED
                        and envelope.error is not None
                        and envelope.error.code in _RETRYABLE_FAILURE_CODES
                    )
                    if (
                        provider_failed
                        and policy.allows_stale
                        and cached is not None
                        and cached.is_within_stale_grace(at=now)
                    ):
                        # Stale-if-safe: the provider is currently failing but a recent answer for
                        # this exact request exists. Never applies to pricing (its policy sets
                        # stale_grace_seconds=0), so a pricing failure always surfaces as
                        # PRICE_UNAVAILABLE rather than an out-of-date number.
                        envelope, cache_status = cached.envelope, "STALE"
                    elif envelope.status == ToolStatus.SUCCEEDED and policy.cacheable and key:
                        cache.set(
                            key,
                            CacheEntry(
                                envelope=envelope,
                                cached_at=now,
                                fresh_until=now + timedelta(seconds=policy.ttl_seconds),
                                stale_until=now
                                + timedelta(seconds=policy.ttl_seconds + policy.stale_grace_seconds),
                            ),
                        )
                sink.append(
                    audit_record(
                        context=context,
                        invocation=invocation,
                        envelope=envelope,
                        approval_decision=decision,
                        latency_ms=latency,
                        idempotency_key_hmac=approval.idempotency_hmac(invocation.idempotency_key),
                        cache_status=cache_status,
                    )
                )
                telemetry.record_call(
                    tool=invocation.tool,
                    status=envelope.status.value,
                    code=envelope.error.code if envelope.error else None,
                    latency_seconds=latency / 1000,
                    cache_status=cache_status,
                )
                if provider:
                    telemetry.sample_provider_health(health, (provider,))
                degraded_headers = [(b"x-knotic-degraded", b"true")] if cache_status == "STALE" else []
                await _json(
                    send,
                    200,
                    envelope.model_dump(mode="json"),
                    extra_headers=[(b"x-knotic-cache", cache_status.encode()), *degraded_headers],
                )
                return
        sink.append(
            audit_record(
                context=context,
                invocation=invocation,
                envelope=envelope,
                approval_decision=decision,
                latency_ms=None,
                idempotency_key_hmac=approval.idempotency_hmac(invocation.idempotency_key),
            )
        )
        telemetry.record_call(
            tool=invocation.tool,
            status=envelope.status.value,
            code=envelope.error.code if envelope.error else None,
            latency_seconds=0.0,
            cache_status="NOT_ATTEMPTED",
        )
        await _json(send, 200, envelope.model_dump(mode="json"))

    return app
