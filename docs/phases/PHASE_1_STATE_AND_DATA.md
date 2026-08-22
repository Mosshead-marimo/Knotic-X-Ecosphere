# Phase 1 — State, Persistence, and Session APIs

## Objective

Build reliable conversation state with clear consistency, history, recovery, privacy, and audit guarantees.

## Entry criteria

- Phase 0 gate passed.
- API and data contracts are approved.

## Tasks

### [x] P1-T001 — Implement typed domain models

- Dependencies: P0-T006.
- Implement: Typed `SalesState`, customer, requirement, objection, qualification, message, tool-call, outcome, and event models with validation and schema versioning.
- Acceptance: Invalid enum values, scores, identifiers, and state transitions are rejected deterministically.
- Verify: Model unit tests, serialization round trips, schema snapshots.

### [x] P1-T002 — Implement PostgreSQL schema and migrations

- Dependencies: P1-T001.
- Implement: Tables, constraints, indexes, foreign keys, timestamps, soft/deletion policy, migration tooling, rollback rules, and pgvector extension setup.
- Acceptance: Required entities are durable; invariants are enforced at the database boundary; migrations work on empty and populated databases.
- Verify: Migration up/down rehearsal, constraint tests, query-plan review.

### [x] P1-T003 — Implement Redis active-state repository

- Dependencies: P1-T001.
- Implement: Namespaced keys, TTL, optimistic concurrency/version checks, atomic updates, serialization versioning, and connection-failure behavior.
- Acceptance: Concurrent turns cannot silently overwrite newer state; stale or corrupt cache entries recover safely.
- Verify: Repository, concurrency, TTL, corruption, and Redis outage tests.

### [x] P1-T004 — Implement durable repositories and unit-of-work boundaries

- Dependencies: P1-T002.
- Implement: Typed repositories for sessions, leads, messages, requirements, objections, meetings, follow-ups, tools, outcomes, and events; transaction boundaries and idempotent writes.
- Acceptance: Partial writes roll back; duplicate requests do not create duplicate business records.
- Verify: Integration tests with real PostgreSQL and failure injection.

### [x] P1-T005 — Implement session lifecycle APIs

- Dependencies: P0-T005, P1-T003, P1-T004.
- Implement: Authenticated create/read/end session endpoints, request validation, error mapping, idempotency keys, rate limits, and correlation IDs.
- Acceptance: Lifecycle transitions obey the contract and are auditable; unauthorized cross-tenant access is impossible.
- Verify: API contract, authorization, idempotency, and rate-limit tests.

### [x] P1-T006 — Implement structured memory updates

- Dependencies: P1-T001, P1-T003, P1-T004.
- Implement: Pure merge rules for customer, company, role, users, use cases, integrations, budget, timeline, competitors, objections, current topic, and next action.
- Acceptance: Raw transcript is never the sole source of memory; confirmed structured values have provenance and confidence.
- Verify: Table-driven merge and regression tests.

### [x] P1-T007 — Implement requirement revision and event history

- Dependencies: P1-T006.
- Implement: Latest-confirmed-value rule, immutable revisions, `REQUIREMENT_UPDATED` events, actor/source metadata, and conflict handling.
- Acceptance: A correction replaces the active value while preserving old/new values and order for audit.
- Verify: FR-05 example plus concurrent and repeated-update tests.

### [ ] P1-T008 — Implement state hydration and recovery

- Dependencies: P1-T003, P1-T004, P1-T007.
- Implement: Redis-first loading with durable reconstruction, checkpointing, cache warming, version migration, and recovery after process/cache loss.
- Acceptance: A session resumes consistently after backend restart or Redis loss without duplicating committed events.
- Verify: Kill/restart, Redis flush, replay, and version-upgrade tests.

### [ ] P1-T009 — Enforce privacy, retention, and tenant isolation

- Dependencies: P1-T002, P1-T004.
- Implement: Tenant scoping, least-privilege database roles, encryption policy, field minimization, retention jobs, deletion/export workflows, and sensitive-field log filters.
- Acceptance: Tenant leakage tests fail closed; deletion and retention behavior is auditable and documented.
- Verify: Security integration tests and data-lifecycle rehearsal.

### [ ] P1-T010 — Add state/data observability and performance baselines

- Dependencies: P1-T005, P1-T008.
- Implement: Metrics and traces for latency, conflicts, retries, cache hit rate, database pool health, and recovery; define service-level targets.
- Acceptance: State path failures are diagnosable by correlation ID and meet measured latency targets under expected load.
- Verify: Load test, dashboard review, alert test.

## Phase gate

All tasks pass against real Redis and PostgreSQL; restart and cache-loss recovery succeed; isolation and lifecycle controls are verified; state latency meets its approved service-level target.
