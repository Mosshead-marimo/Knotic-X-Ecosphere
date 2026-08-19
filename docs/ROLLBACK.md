# Rollback and Recovery

## Trigger

The release owner stops promotion and starts rollback when health/readiness fails, error or latency thresholds breach, data invariants fail, authorization weakens, credentials may be exposed, or an external action can be falsely reported as successful. Security and data-integrity triggers do not wait for the normal bake window.

## Application rollback

1. Declare the incident and preserve logs, traces, request IDs, release digests, and migration versions.
2. Stop traffic promotion and disable affected high-impact actions through deterministic policy controls.
3. Route traffic to the last known-good image digests/configuration. Do not rebuild an old tag.
4. Keep expanded schemas in place; prefer a forward fix over destructive down migrations.
5. Verify readiness, authentication, state/event consistency, pending external operations, and a non-destructive synthetic journey.
6. Reconcile idempotency records, outbox/inbox work, and provider-confirmed CRM/calendar outcomes before resuming writes.

## Data recovery

If corruption or loss is suspected, isolate writers, capture forensic copies, and follow the approved managed-service point-in-time recovery runbook. Restore into an isolated environment first and compare audit/event continuity before cutover. PostgreSQL is authoritative for durable truth; Redis may be rebuilt only from durable records according to `DATA_MODEL.md`. Never infer provider success from an interrupted request.

Record the trigger, decision maker, prior/new digests, schema state, customer impact, reconciliation results, and follow-up owner. Rotation or revocation follows `CONFIGURATION.md` when compromise is possible.
