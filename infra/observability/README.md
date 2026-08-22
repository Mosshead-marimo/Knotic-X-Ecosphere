# Observability

Shared logs, metrics, traces, dashboards, alerts, and service-level objective definitions belong here.

`prometheus/state-data-alerts.yaml` defines the state/data alerts and
`grafana/state-data-dashboard.json` is the corresponding dashboard. The backend exposes
`/internal/metrics` only when a metrics bearer token is configured; ingress must keep this
route private. OTLP/HTTP traces contain correlation IDs, operation names, and bounded status
attributes only—never tenant IDs, session IDs, prompts, transcripts, or customer fields.

See `docs/STATE_DATA_SLO.md` for targets, expected load, and response procedures.
