from __future__ import annotations

import json
from pathlib import Path

import yaml

from knotic_mcp.observability import McpObservability

_ROOT = Path(__file__).resolve().parents[2]


def test_integration_dashboard_and_alerts_cover_operational_risks() -> None:
    dashboard = json.loads(
        (_ROOT / "infra/observability/grafana/integration-dashboard.json").read_text(encoding="utf-8")
    )
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Provider action outcomes",
        "Pending reconciliation work",
        "Webhook rejections",
        "Provider quota denials",
        "Provider truth discrepancies",
        "Integration p95 latency",
    } <= titles
    alerts = yaml.safe_load(
        (_ROOT / "infra/observability/prometheus/integration-alerts.yaml").read_text(encoding="utf-8")
    )
    names = {rule["alert"] for group in alerts["groups"] for rule in group["rules"]}
    assert {
        "IntegrationWebhookAttackDetected",
        "IntegrationProviderQuotaNearExhaustion",
        "IntegrationPendingWorkStale",
        "IntegrationDeadLetterPresent",
        "IntegrationTruthDiscrepancy",
    } <= names


def test_integration_metrics_are_content_free_and_low_cardinality() -> None:
    telemetry = McpObservability(service_name="test", environment="test")
    telemetry.record_webhook_rejection(provider="messaging", reason="signature")
    telemetry.record_quota_denial(provider="crm")
    telemetry.set_pending_work(kind="CALENDAR", status="PENDING", count=2)
    telemetry.record_discrepancy(kind="CALENDAR")
    payload = telemetry.render()[0].decode()
    assert "knotic_integration_webhook_rejections_total" in payload
    assert "knotic_integration_provider_quota_denials_total" in payload
    assert "knotic_integration_pending_work" in payload
    assert "knotic_integration_discrepancies_total" in payload
    for forbidden in ("tenant", "session", "customer", "content", "provider_reference"):
        assert forbidden not in payload.casefold()
