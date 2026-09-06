# Phase 5 — CRM, Calendar, Follow-up, and Human Handoff

## Objective

Execute business actions through authenticated, idempotent, provider-confirmed integrations with complete audit and recovery behavior.

## Entry criteria

- Phase 4 gate passed.
- Production integration accounts, scopes, owners, quotas, and data-processing approvals are available.

## Tasks

### [ ] P5-T001 — Build shared integration adapter framework

- Dependencies: P3-T001–P3-T003.
- Implement: Credential isolation, OAuth/token refresh where required, typed provider adapters, idempotency, deadlines, retries with jitter, circuit breakers, normalized errors, and sandbox/production separation.
- Acceptance: Provider credentials are least-privileged and rotated; failures cannot be mistaken for success.
- Verify: Contract, auth-expiry, retry, idempotency, and outage tests.

### [ ] P5-T002 — Implement CRM lead lookup and deduplication

- Dependencies: P5-T001.
- Implement: Tenant-scoped matching rules, confidence, conflict handling, provider IDs, and no-match/multiple-match flows.
- Acceptance: The system cannot silently attach a conversation to the wrong lead.
- Verify: Exact, fuzzy, duplicate, missing, and cross-tenant tests.

### [ ] P5-T003 — Implement CRM create/update/activity tools

- Dependencies: P5-T002.
- Implement: Lead creation/update, requirements, objections, qualification, notes, summaries, outcomes, field mapping, optimistic conflict handling, and provider confirmation.
- Acceptance: Writes are idempotent, validated, auditable, and never reported successful without confirmation.
- Verify: Contract, duplicate, partial failure, conflict, and provider-outage tests.

### [ ] P5-T004 — Implement calendar availability retrieval

- Dependencies: P5-T001.
- Implement: Organizer policy, time zones, duration, working hours, buffers, conflicts, freshness, pagination, and customer-readable options.
- Acceptance: Offered slots are current, timezone-explicit, policy-compliant, and not treated as reserved.
- Verify: DST, timezone, stale-slot, conflict, and pagination tests.

### [ ] P5-T005 — Implement confirmed meeting booking

- Dependencies: P5-T004.
- Implement: Explicit selected-slot state, final availability check, idempotent provider booking, attendee validation, provider event ID, confirmation details, and compensation for partial failures.
- Acceptance: Booking occurs only after explicit customer selection and is claimed only after provider confirmation.
- Verify: Race, duplicate, expired slot, provider timeout, partial failure, and replay tests.

### [x] P5-T006 — Implement follow-up creation and delivery

- Dependencies: P5-T001, P5-T003.
- Implement: Approved templates/content, channel consent, scheduling, idempotency, delivery status, provider confirmation, unsubscribe policy, and audit.
- Acceptance: Follow-ups respect consent and are not marked delivered from an enqueue response alone.
- Verify: Consent, duplicate, scheduling, bounce/failure, and provider callback tests.

### [x] P5-T007 — Implement deterministic escalation policy

- Dependencies: P2-T005–P2-T007.
- Implement: Explicit request, enterprise, negotiation, security/legal, low-confidence, frustration, unsupported-question, and unauthorized-discount triggers; priority and approval rules.
- Acceptance: High-impact paths cannot be overridden by free-form model output; trigger reason is persisted.
- Verify: Trigger matrix, precedence, false-positive, and bypass tests.

### [x] P5-T008 — Implement structured human handoff

- Dependencies: P5-T007, P5-T003.
- Implement: Required FR-13 context packet, summary validation, target routing, availability, acknowledgement, transfer status, customer messaging, and fallback queue.
- Acceptance: Every handoff contains complete current structured context and never claims acceptance without acknowledgement.
- Verify: Completeness, unavailable-agent, timeout, duplicate, and transfer tests.

### [x] P5-T009 — Implement outcomes and reconciliation

- Dependencies: P5-T003, P5-T005, P5-T006, P5-T008.
- Implement: All FR-14 outcomes, allowed transitions, provider reconciliation jobs, pending work, dead-letter handling, operator replay, and discrepancy alerts.
- Acceptance: Internal state converges with provider truth; every unresolved transaction is visible and recoverable.
- Verify: Reconciliation, stale pending, manual replay, and duplicate callback tests.

### [ ] P5-T010 — Certify integration security and operations

- Dependencies: P5-T001–P5-T009.
- Implement: Audit dashboards, provider quotas, alerts, runbooks, access review, webhook signature verification, egress allowlists, disaster exercises, and SLOs.
- Acceptance: Integration failures and credential compromise scenarios have tested detection and response procedures.
- Verify: Security review, webhook attacks, quota/load test, and incident drill.
- Status: Control implementation and sandbox attack/load tests are complete. Production certification remains `INCOMPLETE` pending dated access-review, actual provider quota/egress, credential-rotation, and incident-drill evidence for the release environment.

## Phase gate

All actions are provider-confirmed, idempotent, reconciled, secure, and observable; booking and handoff races pass; failures remain pending/recoverable without fabricated success.
