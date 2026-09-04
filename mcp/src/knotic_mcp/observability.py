"""MCP/RAG production metrics, traces, and the executable mirror of alert thresholds (P3-T010).

Every tool call updates the same low-cardinality label set regardless of tenant, argument
content, or retrieved text — labels are tool name, terminal status, and safe error code only,
matching the pattern in ``knotic_api.observability`` for the state/data service. Metric values
never carry a tenant ID, session ID, query text, or retrieved content.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

if TYPE_CHECKING:
    from .cache import ProviderHealthTracker

# Denied-by-policy failure codes (see docs/MCP_TOOLS.md's failure envelope) are tracked
# separately from ordinary tool errors so a spike in intentional denials — a caller requesting a
# scope it doesn't hold, a missing approval — is distinguishable from a provider malfunction.
_POLICY_DENIAL_CODES = frozenset({"PERMISSION_DENIED", "POLICY_DENIED", "APPROVAL_REQUIRED", "RATE_LIMITED"})
_KNOWLEDGE_TOOLS = frozenset(
    {
        "knowledge.search",
        "product.search",
        "product.get_feature",
        "product.get_integration",
        "competitor.compare",
        "security.get_information",
    }
)

@dataclass(frozen=True, slots=True)
class McpSloSnapshot:
    knowledge_tool_p95_latency_seconds: float
    sales_tool_p95_latency_seconds: float
    tool_error_ratio: float
    knowledge_miss_ratio: float
    cache_hit_ratio: float
    stale_serve_ratio: float
    policy_denial_ratio: float


class McpSloAlertPolicy:
    """Executable mirror of the checked-in Prometheus alert thresholds (see the alert rules

    file and ``docs/MCP_RAG_SLO.md``); keep both in sync when a threshold changes.
    """

    def evaluate(self, snapshot: McpSloSnapshot) -> tuple[str, ...]:
        alerts: list[str] = []
        if snapshot.knowledge_tool_p95_latency_seconds > 2.0:
            alerts.append("KnowledgeToolLatencyHigh")
        if snapshot.sales_tool_p95_latency_seconds > 1.0:
            alerts.append("SalesToolLatencyHigh")
        if snapshot.tool_error_ratio > 0.02:
            alerts.append("McpToolErrorRateHigh")
        if snapshot.knowledge_miss_ratio > 0.20:
            alerts.append("KnowledgeMissRateHigh")
        if snapshot.cache_hit_ratio < 0.50:
            alerts.append("McpCacheHitRateLow")
        if snapshot.stale_serve_ratio > 0.05:
            alerts.append("McpDegradedServingHigh")
        if snapshot.policy_denial_ratio > 0.05:
            alerts.append("McpPolicyDenialRateHigh")
        return tuple(alerts)


class McpObservability:
    """Owns one Prometheus registry and one tracer per gateway process."""

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        otlp_endpoint: str | None = None,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.tool_calls = Counter(
            "knotic_mcp_tool_calls_total", "MCP tool call terminal outcomes", ("tool", "status"), registry=self.registry
        )
        self.tool_call_duration = Histogram(
            "knotic_mcp_tool_call_duration_seconds",
            "MCP tool call latency",
            ("tool", "status"),
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0),
            registry=self.registry,
        )
        self.tool_errors = Counter(
            "knotic_mcp_tool_errors_total", "MCP tool call failures by safe error code", ("tool", "code"),
            registry=self.registry,
        )
        self.policy_denials = Counter(
            "knotic_mcp_policy_denials_total", "MCP calls denied by policy, scope, or approval", ("tool", "code"),
            registry=self.registry,
        )
        self.cache_results = Counter(
            "knotic_mcp_cache_results_total", "MCP tool cache outcomes", ("tool", "status"), registry=self.registry
        )
        self.knowledge_queries = Counter(
            "knotic_mcp_knowledge_queries_total", "Knowledge-domain tool calls", ("tool",), registry=self.registry
        )
        self.knowledge_misses = Counter(
            "knotic_mcp_knowledge_misses_total",
            "Knowledge-domain calls with no grounded result",
            ("tool",),
            registry=self.registry,
        )
        self.provider_health = Gauge(
            "knotic_mcp_provider_health",
            "1 if the named provider's circuit is closed (healthy), 0 if it is open (degraded)",
            ("provider",),
            registry=self.registry,
        )
        self.knowledge_index_age_seconds = Gauge(
            "knotic_mcp_knowledge_index_age_seconds",
            "Seconds since the domain's knowledge index was last refreshed by the ingestion pipeline",
            ("domain",),
            registry=self.registry,
        )
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name, "deployment.environment": environment})
        )
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif otlp_endpoint:
            trace_endpoint = otlp_endpoint if otlp_endpoint.endswith("/v1/traces") else f"{otlp_endpoint}/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=trace_endpoint)))
        self.tracer = provider.get_tracer("knotic.mcp-gateway")

    @contextmanager
    def span(self, operation: str, correlation_id: UUID | None = None) -> Iterator[None]:
        with self.tracer.start_as_current_span(operation) as active_span:
            if correlation_id is not None:
                active_span.set_attribute("knotic.correlation_id", str(correlation_id))
            yield

    def record_call(
        self, *, tool: str, status: str, code: str | None, latency_seconds: float, cache_status: str
    ) -> None:
        """Record one terminal tool-call outcome. Called exactly once per gateway request."""
        self.tool_calls.labels(tool=tool, status=status).inc()
        self.tool_call_duration.labels(tool=tool, status=status).observe(latency_seconds)
        self.cache_results.labels(tool=tool, status=cache_status).inc()
        if code:
            self.tool_errors.labels(tool=tool, code=code).inc()
            if code in _POLICY_DENIAL_CODES:
                self.policy_denials.labels(tool=tool, code=code).inc()
        # Only count calls that actually reached the retrieval provider (cache hit, stale-serve,
        # or a real provider attempt) toward the knowledge miss-ratio denominator; a call denied
        # before dispatch (cache_status "NOT_ATTEMPTED") never touched retrieval quality at all.
        if tool in _KNOWLEDGE_TOOLS and cache_status != "NOT_ATTEMPTED":
            self.knowledge_queries.labels(tool=tool).inc()
            if code == "NO_GROUNDED_RESULT":
                self.knowledge_misses.labels(tool=tool).inc()

    def sample_provider_health(self, tracker: ProviderHealthTracker, providers: tuple[str, ...]) -> None:
        for provider in providers:
            self.provider_health.labels(provider=provider).set(0.0 if tracker.is_open(provider) else 1.0)

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


__all__ = ["McpObservability", "McpSloAlertPolicy", "McpSloSnapshot"]
