from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from knotic_api.app import create_app
from knotic_api.config import BackendSettings
from knotic_api.observability import StateDataAlertPolicy, StateDataObservability, StateDataSnapshot

ROOT = Path(__file__).parents[2]
TOKEN = "m" * 40


def _settings(*, metrics: bool) -> BackendSettings:
    return BackendSettings(
        database_url=SecretStr("postgresql://user:password@db.internal/app"),
        redis_url=SecretStr("redis://:password@redis.internal/0"),
        mcp_auth_token=SecretStr("a" * 40),
        agora_app_certificate=SecretStr("b" * 32),
        session_security_key=SecretStr("c" * 32),
        metrics_auth_token=SecretStr(TOKEN) if metrics else None,
        KNOTIC_MCP_BASE_URL="http://mcp.internal:8090",
        KNOTIC_AGORA_APP_ID="0123456789abcdef0123456789abcdef",
    )


def test_metrics_endpoint_is_private_authenticated_and_low_cardinality() -> None:
    telemetry = StateDataObservability(service_name="test", environment="test")
    client = create_app(_settings(metrics=True), observability=telemetry).test_client()

    assert client.get("/internal/metrics").status_code == 401
    assert client.get("/internal/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/internal/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.content_type.startswith("text/plain")
    body = response.get_data(as_text=True)
    assert "knotic_db_pool_saturation_ratio" in body
    assert TOKEN not in body

    disabled = create_app(_settings(metrics=False)).test_client()
    assert disabled.get("/internal/metrics").status_code == 404


def test_metrics_and_trace_capture_outcome_and_correlation_without_business_data() -> None:
    exporter = InMemorySpanExporter()
    telemetry = StateDataObservability(service_name="test", environment="test", span_exporter=exporter)
    correlation_id = UUID("0198d716-d4bf-7000-8000-000000000001")

    with telemetry.timed_load(correlation_id) as labels:
        labels.update(source="cache", outcome="success")
    telemetry.cache_reads.labels(status="hit").inc()
    metrics, _ = telemetry.render()

    rendered = metrics.decode()
    assert 'knotic_state_loads_total{outcome="success",source="cache"} 1.0' in rendered
    assert 'knotic_state_cache_reads_total{status="hit"} 1.0' in rendered
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "state.load"
    assert spans[0].attributes == {"knotic.correlation_id": str(correlation_id)}


def test_alert_policy_and_monitoring_artifacts_cover_every_slo() -> None:
    alerts = StateDataAlertPolicy().evaluate(
        StateDataSnapshot(
            cache_hit_ratio=0.90,
            conflict_ratio=0.03,
            recovery_failure_ratio=0.02,
            pool_saturation=0.90,
        )
    )
    assert set(alerts) == {
        "StateCacheHitRateLow",
        "StateConflictRateHigh",
        "StateRecoveryFailuresHigh",
        "DatabasePoolSaturationHigh",
    }

    rules = (ROOT / "infra" / "observability" / "prometheus" / "state-data-alerts.yaml").read_text()
    dashboard = json.loads((ROOT / "infra" / "observability" / "grafana" / "state-data-dashboard.json").read_text())
    for alert in alerts:
        assert f"alert: {alert}" in rules
    assert {panel["title"] for panel in dashboard["panels"]} >= {
        "State load p95",
        "Cache hit ratio",
        "Conflicts and retries",
        "Recovery outcomes",
        "Database pool saturation",
    }
