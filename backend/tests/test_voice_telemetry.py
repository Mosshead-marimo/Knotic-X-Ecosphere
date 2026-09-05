import json
from pathlib import Path

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.telemetry import (
    VoiceFailureComponent,
    VoiceObservability,
    VoiceStage,
    VoiceTraceContext,
)

ROOT = Path(__file__).parents[2]


def test_every_voice_stage_is_correlated_without_customer_content() -> None:
    exporter = InMemorySpanExporter()
    telemetry = VoiceObservability(service_name="voice-test", environment="test", span_exporter=exporter)
    context = VoiceTraceContext(
        correlation_id=new_uuid7(), session_id=new_uuid7(), turn_id=new_uuid7(), response_id=new_uuid7()
    )
    for stage in VoiceStage:
        with telemetry.stage(stage, context) as result:
            result["outcome"] = "success"
    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {f"voice.{stage.value}" for stage in VoiceStage}
    for span in spans:
        serialized = str(dict(span.attributes))
        assert str(context.correlation_id) in serialized
        assert "transcript" not in serialized.casefold()
        assert "audio" not in serialized.casefold()


def test_metrics_have_only_bounded_operational_labels() -> None:
    telemetry = VoiceObservability(service_name="voice-test", environment="test")
    telemetry.record_turn("success")
    telemetry.record_failure(VoiceFailureComponent.AGORA, "CONNECTION_LOST")
    telemetry.record_quality("barge_in")
    metrics, _ = telemetry.render()
    rendered = metrics.decode()
    assert 'knotic_voice_turns_total{outcome="success"} 1.0' in rendered
    assert 'knotic_voice_failures_total{code="CONNECTION_LOST",component="agora"} 1.0' in rendered
    assert "session_id" not in rendered and "correlation_id" not in rendered


def test_dashboard_and_alerts_cover_latency_failure_and_quality() -> None:
    alerts = (ROOT / "infra" / "observability" / "prometheus" / "voice-alerts.yaml").read_text()
    dashboard_path = ROOT / "infra" / "observability" / "grafana" / "voice-dashboard.json"
    dashboard = json.loads(dashboard_path.read_text())
    for name in ("VoiceFirstAudioLatencyHigh", "VoiceInterruptionLatencyHigh", "VoiceFailureRateHigh"):
        assert f"alert: {name}" in alerts
    for title in ("First audio p95", "Pipeline stage p95", "Voice failures", "Quality signals"):
        assert title in {panel["title"] for panel in dashboard["panels"]}
