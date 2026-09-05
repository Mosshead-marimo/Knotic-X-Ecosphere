# Integration Security and Operations

## Release status

The controls and executable drills required by `P5-T010` are implemented. Production certification remains `INCOMPLETE` until the release owner attaches dated evidence for the target environment's provider access review, actual quotas, egress policy, credential rotation, load test, and incident exercise. A sandbox test result is not production certification.

## Service-level objectives

| Signal | Objective | Alert |
|---|---:|---|
| Confirmed provider action availability | >= 99.9% over 30 days | error budget burn / provider circuit |
| Provider action p95 latency | <= 2 seconds excluding asynchronous confirmation | integration latency panel |
| Reconciliation freshness | 99% terminal within 15 minutes | `IntegrationPendingWorkStale` |
| Dead-letter visibility | 100% detected within 2 minutes | `IntegrationDeadLetterPresent` |
| Webhook authentication | 100% invalid/replayed payloads rejected | `IntegrationWebhookAttackDetected` |
| Provider truth discrepancies | 100% alerted within 2 minutes | `IntegrationTruthDiscrepancy` |

`PENDING` is not success. Availability counts only schema-valid provider-confirmed outcomes. Maintenance and provider outage periods are included unless the release policy explicitly excludes them before the window starts.

## Provider quotas and egress

- Every provider has a production-owner-approved request/window quota configured below the provider limit. Unknown providers fail closed.
- Quotas are isolated by tenant scope and provider; the local guard returns a retry delay and increments a low-cardinality provider metric.
- Production network policy permits TCP 443 only to the exact approved provider origins. Wildcards, literal IPs, HTTP, embedded credentials, and redirects to a different origin are denied. Every redirect is re-authorized.
- The environment-specific origin inventory is stored in the deployment secret/configuration service, reviewed with provider ownership, and never supplied by model output.

## Webhook response runbook

1. Page integrations on-call for repeated signature, timestamp, unknown-provider, or replay rejection.
2. Preserve delivery ID, provider, safe rejection category, trace/correlation ID, and receive time. Never preserve the secret or unrestricted payload in logs.
3. Confirm ingress came through the provider allowlist, compare the configured secret version, and inspect replay counts.
4. For suspected compromise, disable that provider's webhook route, rotate the webhook secret with bounded current/previous overlap, reconcile all transactions since the last trusted delivery, and notify the incident commander.
5. Re-enable only after an authentic signed probe passes and all missing callbacks are reconciled from provider truth.

## Quota and provider-outage runbook

1. Stop optional work and preserve voice/customer-facing paths when quota denial or circuit-open alerts fire.
2. Do not retry writes of unknown outcome. Move them to `PENDING_CONFIRMATION` and query provider truth using the original idempotency key/reference.
3. Verify actual account quota and reset time with the provider owner. A quota increase requires security/cost approval.
4. Drain due reconciliation work gradually after recovery. Watch latency, failure, duplicate callback, and dead-letter panels.
5. Close the incident only after backlog is zero, discrepancies are resolved, and provider-confirmed results are persisted.

## Credential-compromise runbook

1. Revoke the affected credential, block provider egress where practical, and declare severity using `INCIDENT_RESPONSE.md`.
2. Rotate through the managed secret service; do not place replacement values in Git, chat, logs, commands, or frontend configuration.
3. Review provider audit logs and internal tool-call audit for the credential's entire exposure window.
4. Reconcile every side effect, notify affected owners, and restore with least-privilege scopes only after an access review.

## Access review

The integration owner and security owner review each production account at least quarterly and after owner/scope/provider changes. Evidence must list provider, environment, service principal, scopes, owner, last use, credential age, rotation method, approved origins, webhook secret version, and decision. Remove unused access immediately. Two-person approval is required for CRM writes, calendar booking, messaging, and handoff production scopes.

## Disaster exercises

Before release and at least twice yearly, execute these drills against the candidate environment:

1. Tampered, stale, future-dated, replayed, and unknown-provider webhook attacks; all must fail closed and alert.
2. Provider quota exhaustion under representative load; optional traffic sheds, accepted work remains visible, and recovery drains without duplicates.
3. Ambiguous timeout after each side effect; no success is claimed before reconciliation.
4. Credential compromise; revocation, rotation, audit search, reconciliation, and controlled restoration meet the incident objectives.
5. Loss/restart of the reconciliation worker; PostgreSQL outbox/inbox work resumes without lost or duplicated outcomes.

Store a dated record with candidate commit, environment, provider/account references, participants, start/end times, injected fault, alert timestamps, recovery timestamps, reconciled counts, unresolved discrepancies, and approvals. Any missing evidence or failed objective keeps certification `INCOMPLETE` or `FAIL`.

## Dashboard and ownership

Grafana dashboard `knotic-integrations` and Prometheus rules `knotic-integrations` are owned by integrations on-call. Audit access is restricted to support/security roles, logged, and reviewed; metrics contain no tenant, lead, session, customer, content, credential, or provider-reference labels.
