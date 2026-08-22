"""Low-cardinality state/data metrics, traces, and alert evaluation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast
from uuid import UUID

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy.engine import Engine


@dataclass(frozen=True, slots=True)
class StateDataSnapshot:
    cache_hit_ratio: float
    conflict_ratio: float
    recovery_failure_ratio: float
    pool_saturation: float


class StateDataAlertPolicy:
    """Executable mirror of the checked-in Prometheus alert thresholds."""

    def evaluate(self, snapshot: StateDataSnapshot) -> tuple[str, ...]:
        alerts: list[str] = []
        if snapshot.cache_hit_ratio < 0.95:
            alerts.append("StateCacheHitRateLow")
        if snapshot.conflict_ratio > 0.02:
            alerts.append("StateConflictRateHigh")
        if snapshot.recovery_failure_ratio > 0.01:
            alerts.append("StateRecoveryFailuresHigh")
        if snapshot.pool_saturation > 0.80:
            alerts.append("DatabasePoolSaturationHigh")
        return tuple(alerts)


class StateDataObservability:
    """Own a registry per application so tests and workers cannot collide."""

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        otlp_endpoint: str | None = None,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.loads = Counter("knotic_state_loads_total", "State loads", ("source", "outcome"), registry=self.registry)
        self.load_duration = Histogram(
            "knotic_state_load_duration_seconds",
            "State load duration",
            ("source", "outcome"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.2, 0.3, 0.5, 1.0),
            registry=self.registry,
        )
        self.cache_reads = Counter("knotic_state_cache_reads_total", "Cache reads", ("status",), registry=self.registry)
        self.conflicts = Counter(
            "knotic_state_conflicts_total", "Optimistic concurrency conflicts", ("operation",), registry=self.registry
        )
        self.retries = Counter(
            "knotic_state_retries_total", "State operation retries", ("operation",), registry=self.registry
        )
        self.recoveries = Counter(
            "knotic_state_recoveries_total", "Durable recoveries", ("outcome",), registry=self.registry
        )
        self.failures = Counter(
            "knotic_state_failures_total", "State failures", ("component", "code"), registry=self.registry
        )
        self.pool_size = Gauge("knotic_db_pool_size", "Configured database pool size", registry=self.registry)
        self.pool_checked_out = Gauge(
            "knotic_db_pool_checked_out", "Checked-out database connections", registry=self.registry
        )
        self.pool_overflow = Gauge("knotic_db_pool_overflow", "Database pool overflow", registry=self.registry)
        self.pool_saturation = Gauge(
            "knotic_db_pool_saturation_ratio", "Checked-out connections divided by capacity", registry=self.registry
        )
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name, "deployment.environment": environment})
        )
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif otlp_endpoint:
            trace_endpoint = otlp_endpoint if otlp_endpoint.endswith("/v1/traces") else f"{otlp_endpoint}/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=trace_endpoint)))
        self.tracer = provider.get_tracer("knotic.state-data")

    @contextmanager
    def span(self, operation: str, correlation_id: UUID | None = None) -> Iterator[None]:
        with self.tracer.start_as_current_span(operation) as active_span:
            if correlation_id is not None:
                active_span.set_attribute("knotic.correlation_id", str(correlation_id))
            yield

    @contextmanager
    def timed_load(self, correlation_id: UUID | None = None) -> Iterator[dict[str, str]]:
        labels = {"source": "unknown", "outcome": "error"}
        started = perf_counter()
        with self.span("state.load", correlation_id):
            try:
                yield labels
            finally:
                self.loads.labels(**labels).inc()
                self.load_duration.labels(**labels).observe(perf_counter() - started)

    def sample_pool(self, engine: Engine) -> None:
        pool = cast(Any, engine.pool)
        size_method = getattr(pool, "size", None)
        checked_out_method = getattr(pool, "checkedout", None)
        overflow_method = getattr(pool, "overflow", None)
        if not callable(size_method) or not callable(checked_out_method) or not callable(overflow_method):
            self.pool_size.set(0)
            self.pool_checked_out.set(0)
            self.pool_overflow.set(0)
            self.pool_saturation.set(0)
            return
        size = float(size_method())
        checked_out = float(checked_out_method())
        overflow = float(max(0, overflow_method()))
        max_overflow = float(max(0, getattr(pool, "_max_overflow", 0)))
        capacity = size + max_overflow
        self.pool_size.set(size)
        self.pool_checked_out.set(checked_out)
        self.pool_overflow.set(overflow)
        self.pool_saturation.set(checked_out / capacity if capacity else 0.0)

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
