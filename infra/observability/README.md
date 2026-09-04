# Observability

Shared logs, metrics, traces, dashboards, alerts, and service-level objective definitions belong here.

`prometheus/state-data-alerts.yaml` defines the state/data alerts and
`grafana/state-data-dashboard.json` is the corresponding dashboard. The backend exposes
`/internal/metrics` only when a metrics bearer token is configured; ingress must keep this
route private. OTLP/HTTP traces contain correlation IDs, operation names, and bounded status
attributes only—never tenant IDs, session IDs, prompts, transcripts, or customer fields.

See `docs/STATE_DATA_SLO.md` for targets, expected load, and response procedures.

`prometheus/mcp-rag-alerts.yaml` defines the MCP/RAG alerts and
`grafana/mcp-rag-dashboard.json` is the corresponding dashboard. The MCP gateway exposes its own
`/internal/metrics` route under the same private-route and bearer-token rules as the backend
(`KNOTIC_METRICS_AUTH_TOKEN`, required in staging/production). Metric labels are tool name,
terminal status, safe error code, cache status, provider name, and domain only—never tenant IDs,
session IDs, query text, or retrieved content.

See `docs/MCP_RAG_SLO.md` for targets, expected load, and response procedures.
