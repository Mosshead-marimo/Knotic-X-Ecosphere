from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from knotic_mcp.app import create_app
from knotic_mcp.cache import ProviderHealthTracker
from knotic_mcp.config import McpSettings
from knotic_mcp.observability import McpObservability, McpSloAlertPolicy, McpSloSnapshot

ROOT = Path(__file__).parents[2]
TOKEN = "m" * 40


async def _get(app: object, *, path: str, authorization: str | None = None) -> tuple[int, bytes, dict[str, str]]:
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    headers = [(b"authorization", authorization.encode())] if authorization else []
    await app(  # type: ignore[misc]
        {"type": "http", "method": "GET", "path": path, "headers": headers},
        receive,
        send,
    )
    response_headers = {key.decode().lower(): value.decode() for key, value in messages[0]["headers"]}
    return int(messages[0]["status"]), messages[1]["body"], response_headers


def _settings(*, metrics: bool) -> McpSettings:
    return McpSettings(
        database_url=SecretStr("postgresql://user:password@db.internal/app"),
        redis_url=SecretStr("redis://:password@redis.internal/0"),
        auth_token=SecretStr("a" * 40),
        metrics_auth_token=SecretStr(TOKEN) if metrics else None,
    )


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_metrics_endpoint_is_private_authenticated_and_low_cardinality(self) -> None:
        app = create_app(_settings(metrics=True))

        status, _, _ = await _get(app, path="/internal/metrics")
        self.assertEqual(status, 401)

        status, _, _ = await _get(app, path="/internal/metrics", authorization="Bearer wrong")
        self.assertEqual(status, 401)

        status, body, headers = await _get(app, path="/internal/metrics", authorization=f"Bearer {TOKEN}")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/plain"))
        rendered = body.decode()
        self.assertIn("knotic_mcp_tool_calls_total", rendered)
        self.assertNotIn(TOKEN, rendered)

        disabled = create_app(_settings(metrics=False))
        status, _, _ = await _get(disabled, path="/internal/metrics")
        self.assertEqual(status, 404)

    async def test_metrics_survive_a_real_tool_call_and_show_no_business_data(self) -> None:
        app = create_app(_settings(metrics=True))
        # Trigger a rejection (no scopes granted) so a call is recorded without needing a live
        # Postgres/Redis-backed knowledge store.
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            body = json.dumps(
                {
                    "tool_call_id": "0193a2d7-1000-7000-8000-000000000004",
                    "tool": "knowledge.search",
                    "version": 1,
                    "arguments": {"query": "SSO", "domains": ["PRODUCT"], "limit": 5},
                }
            ).encode()
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        context = {
            "tenant_id": "0193a2d7-1000-7000-8000-000000000001",
            "actor_id": "0193a2d7-1000-7000-8000-000000000002",
            "correlation_id": "0193a2d7-1000-7000-8000-000000000003",
            "scopes": [],
            "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        await app(  # type: ignore[misc]
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/tools/knowledge.search",
                "headers": [
                    (b"authorization", b"Bearer " + b"a" * 40),
                    (b"x-knotic-trusted-context", json.dumps(context).encode()),
                ],
            },
            receive,
            send,
        )
        self.assertEqual(messages[0]["status"], 200)
        payload = json.loads(messages[1]["body"])
        self.assertEqual(payload["error"]["code"], "PERMISSION_DENIED")

        status, body, _ = await _get(app, path="/internal/metrics", authorization=f"Bearer {TOKEN}")
        self.assertEqual(status, 200)
        rendered = body.decode()
        self.assertIn('knotic_mcp_tool_calls_total{status="FAILED",tool="knowledge.search"} 1.0', rendered)
        self.assertIn('knotic_mcp_policy_denials_total{code="PERMISSION_DENIED",tool="knowledge.search"} 1.0', rendered)
        # No tenant/session/query content ever appears in exported metric text.
        self.assertNotIn("0193a2d7-1000-7000-8000-000000000001", rendered)
        self.assertNotIn("SSO", rendered)

    async def test_record_call_and_trace_capture_outcome_and_correlation_without_business_data(self) -> None:
        exporter = InMemorySpanExporter()
        telemetry = McpObservability(service_name="test", environment="test", span_exporter=exporter)
        correlation_id = UUID("0198d716-d4bf-7000-8000-000000000001")

        with telemetry.span("mcp.tool_call", correlation_id):
            telemetry.record_call(
                tool="knowledge.search", status="SUCCEEDED", code=None, latency_seconds=0.05, cache_status="HIT"
            )
        rendered = telemetry.render()[0].decode()
        self.assertIn('knotic_mcp_tool_calls_total{status="SUCCEEDED",tool="knowledge.search"} 1.0', rendered)
        self.assertIn('knotic_mcp_cache_results_total{status="HIT",tool="knowledge.search"} 1.0', rendered)
        self.assertIn('knotic_mcp_knowledge_queries_total{tool="knowledge.search"} 1.0', rendered)

        spans = exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "mcp.tool_call")
        self.assertEqual(spans[0].attributes, {"knotic.correlation_id": str(correlation_id)})

    def test_knowledge_miss_and_policy_denial_labels_are_tracked_separately_from_ordinary_errors(self) -> None:
        telemetry = McpObservability(service_name="test", environment="test")
        telemetry.record_call(
            tool="knowledge.search", status="FAILED", code="NO_GROUNDED_RESULT", latency_seconds=0.1, cache_status="MISS"
        )
        telemetry.record_call(
            tool="pricing.get_quote", status="FAILED", code="PRICE_UNAVAILABLE", latency_seconds=0.1, cache_status="MISS"
        )
        telemetry.record_call(
            tool="knowledge.search", status="FAILED", code="PERMISSION_DENIED", latency_seconds=0.0, cache_status="NOT_ATTEMPTED"
        )
        rendered = telemetry.render()[0].decode()
        self.assertIn('knotic_mcp_knowledge_misses_total{tool="knowledge.search"} 1.0', rendered)
        self.assertIn('knotic_mcp_policy_denials_total{code="PERMISSION_DENIED",tool="knowledge.search"} 1.0', rendered)
        # A pricing failure is an ordinary tool error, never a "knowledge miss" or policy denial.
        self.assertNotIn("knotic_mcp_knowledge_misses_total{tool=\"pricing.get_quote\"}", rendered)
        self.assertNotIn("knotic_mcp_policy_denials_total{code=\"PRICE_UNAVAILABLE\"", rendered)

    def test_provider_health_gauge_reflects_the_tracker(self) -> None:
        telemetry = McpObservability(service_name="test", environment="test")
        tracker = ProviderHealthTracker(failure_threshold=1, open_seconds=60)
        tracker.record_failure("pricing_catalog")
        telemetry.sample_provider_health(tracker, ("knowledge_retrieval", "pricing_catalog"))
        rendered = telemetry.render()[0].decode()
        self.assertIn('knotic_mcp_provider_health{provider="knowledge_retrieval"} 1.0', rendered)
        self.assertIn('knotic_mcp_provider_health{provider="pricing_catalog"} 0.0', rendered)

    def test_alert_policy_and_monitoring_artifacts_cover_every_slo(self) -> None:
        alerts = McpSloAlertPolicy().evaluate(
            McpSloSnapshot(
                knowledge_tool_p95_latency_seconds=2.5,
                sales_tool_p95_latency_seconds=1.5,
                tool_error_ratio=0.05,
                knowledge_miss_ratio=0.30,
                cache_hit_ratio=0.10,
                stale_serve_ratio=0.10,
                policy_denial_ratio=0.10,
            )
        )
        self.assertEqual(
            set(alerts),
            {
                "KnowledgeToolLatencyHigh",
                "SalesToolLatencyHigh",
                "McpToolErrorRateHigh",
                "KnowledgeMissRateHigh",
                "McpCacheHitRateLow",
                "McpDegradedServingHigh",
                "McpPolicyDenialRateHigh",
            },
        )

        rules = (ROOT / "infra" / "observability" / "prometheus" / "mcp-rag-alerts.yaml").read_text()
        dashboard = json.loads((ROOT / "infra" / "observability" / "grafana" / "mcp-rag-dashboard.json").read_text())
        for alert in alerts:
            self.assertIn(f"alert: {alert}", rules)
        self.assertGreaterEqual(
            {panel["title"] for panel in dashboard["panels"]},
            {
                "Tool call p95 latency",
                "Tool call outcomes",
                "Cache outcomes",
                "Knowledge miss ratio",
                "Policy denials",
                "Provider health",
            },
        )

    def test_healthy_snapshot_raises_no_alerts(self) -> None:
        alerts = McpSloAlertPolicy().evaluate(
            McpSloSnapshot(
                knowledge_tool_p95_latency_seconds=0.5,
                sales_tool_p95_latency_seconds=0.2,
                tool_error_ratio=0.0,
                knowledge_miss_ratio=0.05,
                cache_hit_ratio=0.80,
                stale_serve_ratio=0.0,
                policy_denial_ratio=0.0,
            )
        )
        self.assertEqual(alerts, ())


if __name__ == "__main__":
    unittest.main()
