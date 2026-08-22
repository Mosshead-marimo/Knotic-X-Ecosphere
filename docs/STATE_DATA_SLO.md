# State and Data Service Levels

## Scope and expected load

These objectives cover active-state reads, durable reconstruction, optimistic writes, and the
PostgreSQL connection pool. The Phase 1 baseline uses 25 concurrent sessions and at least 500
Redis-backed reads per test run. Measurements exclude client/network transit outside the service.

| Indicator | Target | Window |
| --- | --- | --- |
| Cache-backed state-load latency | p95 <= 75 ms | 5 minutes |
| Durable recovery latency | p95 <= 300 ms | 5 minutes |
| Cache hit ratio after warm-up | >= 95% | 15 minutes |
| Optimistic conflict ratio | <= 2% | 10 minutes |
| Durable recovery failure ratio | <= 1% | 10 minutes |
| Database pool saturation | <= 80% | 10 minutes |

The checked-in performance test is a regression guard, not a substitute for environment load
testing. Before release, run the same workload against staging for 30 minutes and attach the
dashboard snapshot to the release evidence.

## Diagnosis and security

Every state-load trace accepts the request correlation ID as `knotic.correlation_id`. Operators
start with that ID, inspect `state.load`, then compare cache, recovery, conflict, retry, failure,
and pool metrics. Metric labels are deliberately bounded and contain no tenant or customer data.
The metrics route requires a dedicated 32-character-or-longer bearer token and must be reachable
only by the monitoring network. Rotate it independently of application authentication tokens.

For an alert, preserve the correlation ID and relevant metric window, check pool saturation and
dependency health, then follow `docs/INCIDENT_RESPONSE.md`. A failed durable recovery is treated
as an integrity incident; do not synthesize or silently reset state.
