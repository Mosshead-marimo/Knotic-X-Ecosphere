"""Low-cardinality, content-free realtime voice telemetry (P4-T009)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Literal
from uuid import UUID

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest


class VoiceStage(StrEnum):
    CAPTURE = "capture"
    TRANSCRIPTION = "transcription"
    WORKFLOW = "workflow"
    TOOL = "tool"
    SYNTHESIS = "synthesis"
    FIRST_AUDIO = "first_audio"
    INTERRUPTION = "interruption"


class VoiceFailureComponent(StrEnum):
    AGORA = "agora"
    SPEECH_INPUT = "speech_input"
    SPEECH_OUTPUT = "speech_output"
    BACKEND = "backend"
    REDIS = "redis"
    TOOL = "tool"


VoiceOutcome = Literal["success", "failure", "cancelled"]


@dataclass(frozen=True, slots=True)
class VoiceTraceContext:
    correlation_id: UUID
    session_id: UUID
    turn_id: UUID | None = None
    response_id: UUID | None = None


class VoiceObservability:
    """Per-turn spans and bounded metrics with no API for customer content."""

    def __init__(
        self,
        *,
        service_name: str,
        environment: str,
        otlp_endpoint: str | None = None,
        span_exporter: SpanExporter | None = None,
    ) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.stage_duration = Histogram(
            "knotic_voice_stage_duration_seconds",
            "Voice pipeline stage latency",
            ("stage", "outcome"),
            buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
            registry=self.registry,
        )
        self.turns = Counter(
            "knotic_voice_turns_total", "Completed realtime voice turns", ("outcome",), registry=self.registry
        )
        self.failures = Counter(
            "knotic_voice_failures_total",
            "Voice dependency failures",
            ("component", "code"),
            registry=self.registry,
        )
        self.quality = Counter(
            "knotic_voice_quality_events_total",
            "Bounded realtime quality signals",
            ("signal",),
            registry=self.registry,
        )
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name, "deployment.environment": environment})
        )
        if span_exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        elif otlp_endpoint:
            endpoint = otlp_endpoint if otlp_endpoint.endswith("/v1/traces") else f"{otlp_endpoint}/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        self.tracer = provider.get_tracer("knotic.realtime-voice")

    @contextmanager
    def stage(self, stage: VoiceStage, context: VoiceTraceContext) -> Iterator[dict[str, VoiceOutcome]]:
        result: dict[str, VoiceOutcome] = {"outcome": "failure"}
        started = perf_counter()
        with self.tracer.start_as_current_span(f"voice.{stage.value}") as span:
            span.set_attribute("knotic.correlation_id", str(context.correlation_id))
            span.set_attribute("knotic.session_id", str(context.session_id))
            if context.turn_id is not None:
                span.set_attribute("knotic.turn_id", str(context.turn_id))
            if context.response_id is not None:
                span.set_attribute("knotic.response_id", str(context.response_id))
            try:
                yield result
            finally:
                outcome = result["outcome"]
                span.set_attribute("knotic.outcome", outcome)
                self.stage_duration.labels(stage=stage.value, outcome=outcome).observe(perf_counter() - started)

    def record_turn(self, outcome: VoiceOutcome) -> None:
        self.turns.labels(outcome=outcome).inc()

    def record_failure(self, component: VoiceFailureComponent, code: str) -> None:
        if not code or len(code) > 64 or not code.replace("_", "").isalnum() or code != code.upper():
            raise ValueError("failure code must be a bounded uppercase identifier")
        self.failures.labels(component=component.value, code=code).inc()

    def record_quality(self, signal: Literal["low_confidence", "reconnect", "barge_in", "dropped_event"]) -> None:
        self.quality.labels(signal=signal).inc()

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
