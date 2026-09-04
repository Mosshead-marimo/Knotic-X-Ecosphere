# MCP and RAG Service Levels

## Scope and expected load

These objectives cover the private MCP gateway's knowledge, sales, and (once implemented)
integration tool calls, including pgvector retrieval, the pricing catalog, per-tool caching, and
provider-health degradation. The Phase 3 baseline uses 25 concurrent callers issuing a mixed
workload of knowledge and pricing tool calls, matching the Phase 1 state/data baseline so the two
can run together in one staging exercise. Measurements exclude client/network transit outside the
gateway.

| Indicator | Target | Window |
| --- | --- | --- |
| Knowledge-tool call latency (`knowledge.search`, `product.*`, `competitor.compare`, `security.get_information`) | p95 <= 2.0 s | 5 minutes |
| Sales-tool call latency (`pricing.*`, `lead.*`) | p95 <= 1.0 s | 5 minutes |
| Tool failure ratio (`status="FAILED"` of all terminal calls) | <= 2% | 10 minutes |
| Knowledge miss ratio (`NO_GROUNDED_RESULT` of knowledge-domain calls) | <= 20% | 15 minutes |
| Cache hit ratio (cacheable knowledge/pricing tools) | >= 50% | 15 minutes |
| Degraded (stale-if-safe) serving ratio | <= 5% | 15 minutes |
| Policy/scope/approval denial ratio | <= 5% | 10 minutes |
| Pricing correctness | 100% — a pricing failure never returns invented or expired data | continuous |

Pricing correctness is not a ratio threshold: it is enforced structurally (`stale_grace_seconds`
is `0` for every pricing tool, see `mcp/src/knotic_mcp/cache.py`) and verified by tests
(`mcp/tests/test_gateway.py::test_pricing_provider_failure_never_serves_a_stale_price`,
`mcp/tests/test_cache.py::test_pricing_tools_never_allow_stale_serving`) rather than measured as
an SLO ratio, because a single violation is unacceptable regardless of frequency.

The checked-in unit and gateway tests are a regression guard, not a substitute for environment
load testing. Before release, run a mixed knowledge/pricing workload against staging for 30
minutes at the Phase 1 baseline concurrency (25 concurrent callers) and at 2x that as a peak-load
check, and attach the dashboard snapshot from both runs to the release evidence. A load run should
also inject at least one deliberate provider outage (stop pgvector connectivity, or point the
pricing catalog at an unreadable path) to confirm `KnowledgeMissRateHigh` and `McpProviderDegraded`
fire and that pricing failures surface as `PRICE_UNAVAILABLE`, never a fabricated number.

## Retrieval quality and index freshness

Retrieval quality is proxied by the knowledge miss ratio above: a rising `NO_GROUNDED_RESULT` rate
against a stable query mix indicates either an index gap or a regression in the retrieval scoring
function, and should be triaged with the curated question benchmark referenced in `P3-T006`. Index
freshness (`knotic_mcp_knowledge_index_age_seconds`) is exposed as a gauge per domain but is not
yet populated automatically by the ingestion pipeline in-process — today it must be set by a
periodic job that reads `knowledge_index_versions` and pushes the age, or scraped from that table
directly until that wiring exists (tracked as a follow-up in `docs/CHANGES_MADE.md`). Until that
gauge is live, treat a rising knowledge miss ratio as the primary freshness signal.

## Diagnosis and security

Every MCP tool-call trace accepts the request correlation ID as `knotic.correlation_id`, matching
the state/data convention. Operators start with that ID, then compare tool call outcome, cache
status, and provider health metrics for the affected tool. Metric labels are deliberately bounded
(tool name, terminal status, safe error code, cache status, provider name, domain) and contain no
tenant ID, session ID, query text, or retrieved content. The `/internal/metrics` route requires a
dedicated 32-character-or-longer bearer token (`KNOTIC_METRICS_AUTH_TOKEN`), is required in staging
and production, and must be reachable only by the monitoring network. Rotate it independently of
the gateway's own workload authentication token.

For an alert, preserve the correlation ID and relevant metric window, check `knotic_mcp_provider_health`
and the audit log's `cache_status`/`approval_decision` fields for the affected tool, then follow
`docs/INCIDENT_RESPONSE.md`. A `McpDegradedServingHigh` alert means customers are receiving
stale-if-safe knowledge answers, not failures — this is the intended safe degradation, but sustained
degradation should be escalated like any other provider outage. A pricing failure is never an
incident to "work around" by extending its cache: extend the underlying pricing catalog's
`effective_at`/`expires_at` window instead, through the same review process as any other pricing change.

## Alert-to-code traceability

Every alert in `infra/observability/prometheus/mcp-rag-alerts.yaml` and every panel in
`infra/observability/grafana/mcp-rag-dashboard.json` has an executable counterpart in
`mcp/src/knotic_mcp/observability.py` (`McpSloAlertPolicy`) and is exercised by
`mcp/tests/test_observability.py`, which fails if a threshold changes in one place and not the
others. `McpProviderDegraded` is the one alert without a `McpSloAlertPolicy` mirror: it is a direct
gauge check (`knotic_mcp_provider_health == 0`) rather than a ratio, so there is nothing to compute
from a snapshot — it is verified instead by asserting the gauge value in
`mcp/tests/test_observability.py`.
