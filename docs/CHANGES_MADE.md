# Changes Made and Implementation Phases

## Purpose

This document serves two related purposes:

1. Define the recommended implementation order for the requirements in `REQUIREMENTS.md` and the architecture in `System_Design.md`.
2. Maintain a concise, chronological record of changes made to the project.

This file records implementation progress; it does not override product requirements or architectural decisions.

The delivery target is production readiness. A demonstration using mocks may be useful during development, but no phase or project may be marked production ready while a mock, skipped control, or unverified manual workaround remains on a critical path.

## Executable phase task files

Each implementation task has a stable ID, dependencies, scope, acceptance criteria, and verification requirements. Start with the [phase task index](phases/README.md).

| Phase | Executable task file |
|---|---|
| 0 | [Production foundation and contracts](phases/PHASE_0_FOUNDATION.md) |
| 1 | [State, persistence, and session APIs](phases/PHASE_1_STATE_AND_DATA.md) |
| 2 | [Adaptive sales workflow](phases/PHASE_2_SALES_WORKFLOW.md) |
| 3 | [MCP, knowledge, and grounded facts](phases/PHASE_3_MCP_AND_KNOWLEDGE.md) |
| 4 | [Realtime Agora voice](phases/PHASE_4_REALTIME_VOICE.md) |
| 5 | [CRM, calendar, follow-up, and handoff](phases/PHASE_5_INTEGRATIONS.md) |
| 6 | [Production readiness and operations](phases/PHASE_6_PRODUCTION_READINESS.md) |

### Codex task instruction

```text
Read docs/AGENTS.md and its source-of-truth documents. Open the relevant file
under docs/phases and complete task <TASK_ID> only. Check its dependencies first,
implement its full scope, satisfy every acceptance criterion, run its verification
and relevant regression tests, then update docs/CHANGES_MADE.md. Do not mark the
task complete or claim production readiness if verification fails or evidence is
missing.
```

## Source-of-truth order

When documents disagree, use the following priority:

1. `REQUIREMENTS.md`
2. `System_Design.md`
3. `ARCHITECTURE_DECISIONS.md`
4. `API_CONTRACTS.md`
5. `DATA_MODEL.md`
6. `MCP_TOOLS.md`
7. Tests
8. Existing code
9. This progress document

## Phase status

Use one of these values for each phase: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, or `COMPLETE`.

| Phase | Scope | Status |
|---|---|---|
| 0 | Contracts and project foundation | IN_PROGRESS |
| 1 | Core backend, state, and persistence | NOT_STARTED |
| 2 | Adaptive sales workflow | NOT_STARTED |
| 3 | MCP tools and grounded knowledge | NOT_STARTED |
| 4 | Realtime Agora voice experience | NOT_STARTED |
| 5 | CRM, calendar, follow-up, and handoff | NOT_STARTED |
| 6 | Hardening, observability, testing, and deployment | NOT_STARTED |

---

## Phase 0 — Contracts and project foundation

### Goal

Remove ambiguity before implementing business logic and establish the mandatory technology stack.

### Implement

- Create the React/Next.js TypeScript frontend and Python/Flask backend structure.
- Add Docker-based local development services for the frontend, backend, Redis, and PostgreSQL with pgvector.
- Establish configuration and server-side secret handling.
- Define environment-variable examples without committing credentials.
- Create the missing design contracts:
  - `ARCHITECTURE_DECISIONS.md`
  - `API_CONTRACTS.md`
  - `DATA_MODEL.md`
  - `MCP_TOOLS.md`
- Define stable identifiers for sessions, customers/leads, calls, messages, tool calls, and events.
- Add formatting, linting, type-checking, and basic test commands.

### Requirements covered

- Non-functional requirements: modularity, server-side secrets, auditability, and safe failure.
- `System_Design.md`: canonical architecture, data plane, and ownership boundaries.

### Exit criteria

- All services start locally through documented commands.
- The frontend can reach a Flask health endpoint.
- Redis and PostgreSQL connectivity checks pass.
- API, data, and MCP contracts exist and are internally consistent.

## Phase 1 — Core backend, state, and persistence

### Goal

Create the stateful conversation foundation without depending on live voice or external business integrations.

### Implement

- Flask APIs for creating, reading, and ending a sales session.
- Typed `SalesState` models containing customer details, requirements, objections, competitors, messages, topic, intent, buying stage, score, tool history, next action, summary, and outcome.
- Redis storage for active conversation state and recent tool results.
- PostgreSQL tables and migrations for sessions, leads, messages, requirements, objections, meetings, follow-ups, tool calls, outcomes, and events.
- Structured memory update rules.
- Requirement replacement behavior where the latest confirmed value becomes active.
- Immutable requirement-change history and `REQUIREMENT_UPDATED` events.
- Repository/service boundaries that keep pure decision logic separate from I/O.

### Requirements covered

- FR-04 Structured memory.
- FR-05 Requirement revision.
- FR-14 Outcomes, at the storage/model level.
- `System_Design.md`: SalesState, memory, and data plane.

### Exit criteria

- A text turn can be saved and the complete active state can be reloaded.
- Requirement corrections replace the active value and retain the old value in history.
- Durable records remain available after Redis state is cleared or rebuilt.

## Phase 2 — Adaptive sales workflow

### Goal

Build and test the sales brain using text turns and mock tools before adding realtime voice complexity.

### Implement

- A LangGraph workflow reinvoked for every customer turn using the same session identity.
- Nodes for receiving and understanding a turn, updating memory, detecting intent and objections, routing, qualification, next-best action, response generation, and completion.
- Support for every intent listed in FR-06.
- Support for every objection category listed in FR-07.
- Deterministic qualification scoring and stage calculation from FR-09.
- Explicit-request overrides for demo, follow-up, and human handoff paths.
- Next-best-action selection for every action listed in FR-10.
- Nonlinear topic changes, follow-up questions, and return to earlier topics.
- Dedicated prompt modules and structured model outputs with validation.
- Unit tests for memory replacement, scoring, stage boundaries, routing, and escalation policy.

### Requirements covered

- FR-03 Dynamic conversation.
- FR-06 Intent support.
- FR-07 Objections.
- FR-09 Continuous qualification.
- FR-10 Next best action.
- `System_Design.md`: runtime turn loop and LangGraph design.

### Exit criteria

- Scripted text conversations can move nonlinearly through discovery, questions, objections, pricing, and closing.
- Qualification and next actions are reproducible from structured state.
- The workflow never relies on raw transcript as its only memory.

## Phase 3 — MCP tools and grounded knowledge

### Goal

Ground business facts in trusted sources and enforce MCP as the integration boundary.

### Implement

- MCP client and gateway with authentication, authorization, policy checks, validation, timeouts, and audit logging.
- Initial logical MCP namespaces; they may share one physical server for the MVP.
- Sales MCP tools for pricing, plan comparison, qualification, next action, and follow-up creation.
- Knowledge MCP tools for product, features, integrations, security, competitors, and general knowledge search.
- Document ingestion, cleaning, chunking, embedding, and pgvector retrieval.
- Citations or source metadata in retrieved knowledge results.
- Tool input/output schemas and typed adapters.
- Safe fallback behavior for pricing failure, retrieval misses, invalid results, and timeouts.
- Policy preventing arbitrary SQL execution by the LLM.

### Requirements covered

- FR-08 Tool-grounded business facts.
- Parts of FR-10 that require retrieval or pricing tools.
- `System_Design.md`: MCP domains, RAG, failure principles, and pgvector knowledge storage.

### Exit criteria

- Product and pricing answers come from validated tool results rather than prompt constants.
- Every MCP call and important state transition is auditable.
- Missing or failed data produces a clarification, safe limitation, or escalation instead of fabricated facts.

## Phase 4 — Realtime Agora voice experience

### Goal

Connect the tested sales workflow to a natural, low-latency voice conversation.

### Implement

- Next.js call interface using the Agora Web SDK.
- Flask endpoint for secure Agora token generation.
- Join, leave, microphone, connection, and call-status flows.
- Realtime speech input and AI voice output integration.
- Mapping from semantic voice turns to Flask/LangGraph session turns.
- Barge-in detection that immediately stops or truncates AI playback.
- Recording of interrupted responses and relevance-based topic resumption.
- Reconnection and graceful-disconnect behavior.
- Latency measurement across speech input, reasoning/tools, synthesis, and playback.

### Requirements covered

- FR-01 Realtime voice.
- FR-02 Interruption/barge-in.
- Non-functional requirement: low conversational latency.
- `System_Design.md`: Agora RTC layer, realtime voice layer, and voice recovery behavior.

### Exit criteria

- A customer can complete a multi-turn voice session.
- Speaking over the AI stops playback and prioritizes the customer turn.
- Interrupted output and the resulting state transition are recorded.
- No Agora or provider secrets are exposed to the browser.

## Phase 5 — CRM, calendar, follow-up, and human handoff

### Goal

Complete business actions through confirmed, auditable integrations.

### Implement

- Integration MCP adapters for CRM lead lookup, creation, update, notes, and call summaries.
- Calendar slot retrieval and meeting booking.
- A confirmation state requiring the customer to select a slot before booking.
- Booking success only after the calendar provider confirms the transaction.
- Follow-up creation and pending/retry handling.
- Human escalation policies for explicit requests, enterprise opportunities, negotiation, security/legal concerns, low confidence, frustration, unsupported questions, and unauthorized discounts.
- Structured handoff packets containing all fields required by FR-13.
- Outcome assignment for every value listed in FR-14.
- Mock adapters for integrations that are unavailable during the MVP.

### Requirements covered

- FR-11 CRM.
- FR-12 Calendar.
- FR-13 Human escalation.
- FR-14 Outcomes.
- Transactional parts of FR-08 and FR-10.
- `System_Design.md`: Integration MCP, human handoff, and transactional failure behavior.

### Exit criteria

- CRM, calendar, follow-up, and handoff results are persisted and traceable.
- The system never reports a booking or external action as successful without provider confirmation.
- Failed CRM updates are stored as pending work and can be retried.
- Human handoff always includes a complete structured context packet.

## Phase 6 — Hardening, observability, testing, and deployment

### Goal

Make the complete system safe, measurable, recoverable, and deployable.

### Implement

- End-to-end tests covering the critical sales journeys and integration failures.
- Load and latency tests for concurrent voice sessions.
- Structured logs, traces, metrics, and correlation IDs across Agora, Flask, LangGraph, MCP, Redis, and PostgreSQL.
- Dashboards or reports for latency, tool failures, escalation rates, qualification outcomes, and booking outcomes.
- Deterministic approval gates for high-impact actions.
- Retry, idempotency, timeout, circuit-breaker, and dead-letter/pending-work strategies where appropriate.
- Security review covering authentication, authorization, secret handling, data minimization, prompt injection, MCP validation, and database access.
- Backup, recovery, retention, and deletion policies.
- Production Docker images and deployment configuration.
- Operational runbooks for voice, database, Redis, MCP, CRM, and calendar failures.

### Requirements covered

- All non-functional requirements.
- All failure principles and non-negotiable rules in `AGENTS.md`.

### Exit criteria

- Critical journeys pass automated end-to-end tests.
- Known external failures degrade safely and are observable.
- Deployment and rollback procedures are documented and tested.
- Security and production-readiness reviews have no unresolved critical findings.

---

## Development demonstration milestone

An internal demonstration may include Phases 0–4 with clearly labeled mock CRM/calendar adapters from Phase 5. It may demonstrate a voice conversation, structured memory, dynamic qualification, grounded product/pricing answers, interruption, and simulated demo or handoff outcomes. This milestone is not production ready. Production release requires every phase gate in `docs/phases`, real critical-path integrations, and the final Phase 6 readiness approval.

## Change log

Add a new entry for each meaningful code, configuration, schema, infrastructure, or documentation change. Keep entries short and link to the affected files when useful.

### Entry template

```markdown
### YYYY-MM-DD — Short change title

- Phase: 0–6
- Status: Added | Changed | Fixed | Removed | Documented
- Files: `path/to/file`
- Summary: What changed and why.
- Verification: Tests or manual checks performed.
- Follow-up: Remaining work, or `None`.
```

### 2026-08-17 — Added implementation roadmap and change ledger

- Phase: 0
- Status: Documented
- Files: `docs/CHANGES_MADE.md`
- Summary: Divided the product requirements and system design into ordered implementation phases and established a standard format for tracking future changes.
- Verification: Cross-checked the roadmap against `AGENTS.md`, `REQUIREMENTS.md`, and `System_Design.md`.
- Follow-up: Update phase statuses and append entries as implementation proceeds.

### 2026-08-17 — Added executable production task files

- Phase: 0–6
- Status: Documented
- Files: `docs/phases/README.md`, `docs/phases/PHASE_0_FOUNDATION.md` through `docs/phases/PHASE_6_PRODUCTION_READINESS.md`, `docs/CHANGES_MADE.md`
- Summary: Converted the roadmap into numbered production work orders with stable task IDs, dependencies, implementation scope, acceptance criteria, verification requirements, and phase gates.
- Verification: Confirmed all seven phase files exist and contain uniquely numbered tasks from `P0-T001` through `P6-T011`.
- Follow-up: Begin with `P0-T001`; mark tasks complete only after their recorded acceptance and verification requirements pass.

### 2026-08-17 — Established repository structure and ownership

- Phase: 0 (`P0-T001`)
- Status: Added
- Files: Root repository controls and workspace manifests; `frontend/`, `backend/`, `mcp/`, `infra/`, `tests/`, `scripts/`; `docs/COMPONENT_OWNERSHIP.md`; `docs/phases/PHASE_0_FOUNDATION.md`
- Summary: Initialized Git on `main` and created the npm/uv monorepo boundaries, reserved entry points, test areas, infrastructure areas, and authoritative component dependency ownership without installing dependencies or adding runtime behavior.
- Verification: Parsed both JSON manifests; uv parsed the Python workspace and stopped only at the intentionally absent `uv.lock`; confirmed all required component and entry-point paths; confirmed there are no nested repositories, secret-like files, dependency directories, build outputs, or retained validation caches; created the initial commit on `main`; cloned it without hard links into an isolated temporary checkout; confirmed the checkout was on `main`, clean, structurally complete, and had valid JSON manifests; removed the verified temporary checkout.
- Follow-up: Complete `P0-T002` to provision supported runtimes, dependencies, scripts, and committed lockfiles.

### 2026-08-17 — Pinned runtimes and reproducible dependencies

- Phase: 0 (`P0-T002`)
- Status: Added
- Files: Runtime pin files, root/component manifests, `package-lock.json`, `uv.lock`, `.npmrc`, `.github/dependabot.yml`, `infra/runtime-versions.env`, `scripts/verify-js-runtime.mjs`, `docs/DEPENDENCY_POLICY.md`, component READMEs, and task status
- Summary: Pinned Node.js 24.19.0 with npm 11.17.0, Python 3.13.14 with uv 0.12.2, PostgreSQL 17.10, pgvector 0.8.6, Redis 8.8.1, exact frontend/Python direct dependencies, future container bases, and weekly npm/uv dependency updates. Added committed universal lockfiles, strict runtime enforcement, uv malware checking, and dependency audit policy.
- Verification: Generated both lockfiles with the pinned toolchains; completed two clean npm and uv installs; confirmed the unsupported local Node 22/npm 10 pair fails with actionable version messages; verified the pinned Node/npm pair; passed TypeScript checking and a Next.js 16.3.1 production build; verified Python 3.13.14 and all required application imports; confirmed uv lock consistency; npm audit found zero vulnerabilities; pip-audit found no known third-party vulnerabilities and skipped only the two local non-PyPI workspace packages; removed all generated environments, build output, caches, and temporary tool distributions.
- Follow-up: Complete `P0-T003` to implement typed environment configuration, startup validation, secret-provider boundaries, and redaction.

### 2026-08-17 — Added production configuration and secret model

- Phase: 0 (`P0-T003`)
- Status: Added
- Files: `.env.example`, `packages/config`, backend and MCP configuration/startup modules and tests, `scripts/scan-frontend-secrets.mjs`, `docs/CONFIGURATION.md`, manifests and `uv.lock`, component documentation, and task status
- Summary: Added shared typed environment models, explicit-only environment-file loading, environment and mapping secret providers, required/optional secret resolution, masked configuration errors, recursive structured log redaction, exact-secret replacement, backend and MCP startup validation, production debug/origin/host restrictions including loopback rejection, rotation overlap fields and guidance, and source/production-bundle credential scanning.
- Verification: Locked and installed the 105-package Python workspace with malware checking; passed 16 configuration/provider/redaction/fail-fast tests; confirmed explicit environment files load only when supplied; confirmed malformed secret summaries never echo submitted values; passed TypeScript checking and a Next.js 16.3.1 production build; scanner self-test proved detection and the real source/server/static bundle scan passed across 93 files; npm reported zero vulnerabilities during the locked install; pip-audit found no known third-party vulnerabilities and skipped only the three local workspace packages; confirmed the uv lock is current.
- Follow-up: Complete `P0-T004` and record decisions for the deployment secret manager, identity model, provider-specific rotation mechanisms, and logging/observability backend.

### 2026-08-17 — Defined binding production architecture decisions

- Phase: 0 (`P0-T004`)
- Status: Documented
- Files: `docs/ARCHITECTURE_DECISIONS.md`, `docs/phases/PHASE_0_FOUNDATION.md`, `README.md`
- Summary: Accepted ten owned architecture decisions covering service and deployable-process boundaries, production topology and trust zones, human/browser/workload identity, initial OpenAI model and embedding adapters, the Agora realtime media and deterministic barge-in path, Redis/PostgreSQL/pgvector authority, safe failure and transactional truth, managed secrets and rotation, OpenTelemetry-based observability and durable audit, and compatible contract/provider evolution. Each decision records context, choice, consequences, status, owner, rejected alternatives, and required follow-through.
- Verification: Cross-checked all decisions against the mandatory stack and ownership rules in `AGENTS.md`, every functional and non-functional requirement in `REQUIREMENTS.md`, all canonical architecture, runtime, data, MCP, RAG, handoff, and failure sections in `System_Design.md`, and the established configuration/rotation boundary. Confirmed every accepted ADR contains the six required fields and traceability covers FR-01 through FR-14 plus every non-functional requirement. Verified current model capability statements against official provider documentation; account availability and production approval remain deployment gates.
- Follow-up: Complete `P0-T005`; `P0-T008` must select the environment-specific production platform, secret-manager adapter, managed data services, regions, and telemetry backend before production deployment approval.

### 2026-08-18 — Defined versioned HTTP and semantic-event contracts

- Phase: 0 (`P0-T005`)
- Status: Documented
- Files: `docs/API_CONTRACTS.md`, `docs/contracts/openapi.v1.json`, `scripts/validate-api-contract.mjs`, root scripts, task status, and repository README
- Summary: Defined fourteen versioned Flask/browser/voice-worker operations including provider-neutral OIDC session bootstrap, sales-session lifecycle, short-lived Agora credentials, synchronous-or-explicitly-pending turns, operation polling, cursor-paginated events, ordered semantic voice turns, and deterministic interruption acknowledgement. Added cookie/workload authentication, CSRF/origin controls, replay-safe idempotency, optimistic versions and sequence conflicts, exact success/pending/error states, rate-limit headers and defaults, one safe error envelope, eight versioned event types with typed payload mappings, compatibility/deprecation rules, and thirteen executable examples.
- Verification: Parsed and structurally validated the OpenAPI 3.1/draft-2020-12 document; resolved every local reference; verified fourteen unique versioned operation IDs and their narrative registry entries; enforced cookie or workload authentication with explicit unauthenticated health/OIDC exceptions; enforced CSRF/idempotency on state-changing browser calls and idempotency on internal calls; verified cursor/limit pagination, explicit success and safe error coverage, request IDs on every response, ErrorEnvelope on every 4xx/5xx response, all eight event-to-payload mappings, and absence of server-secret fields in examples. Validated thirteen positive examples against their declared schemas and confirmed five deliberately invalid fixtures are rejected, including missing fields, ambiguous response disposition, invalid terminal operation state, inconsistent pagination, and incorrectly typed requirement updates. `git diff --check` and generated-artifact scans passed.
- Follow-up: Complete `P0-T006` and align durable/active storage lifecycles with these identifiers, versions, event envelopes, idempotency records, and operation states; add the validator and producer/consumer contract tests to CI in `P0-T009`.

### 2026-08-20 — Defined durable, active, and vector data ownership

- Phase: 0 (`P0-T006`)
- Status: Documented
- Files: `docs/DATA_MODEL.md`, `scripts/validate-data-model.mjs`, root scripts, task status, and repository README
- Summary: Defined UUIDv7 and tenant-safe identifier conventions, 30 PostgreSQL entities with indexed composite foreign keys and forced RLS, exact Redis active-state keys/TTLs/rebuild behavior, versioned pgvector knowledge ownership, state/event authority, optimistic concurrency, outbox/inbox and idempotency patterns, provider-confirmation invariants, application envelope encryption, retention/erasure, and expand/migrate/contract rules.
- Verification: Passed the executable data-model validator for all entities, seven Redis key contracts, every structured SalesState field, all eight API event lifecycles, production controls, and explicit FR-04, FR-05, FR-11, FR-12, FR-13, and FR-14 traceability. Re-ran the API contract validator and `git diff --check` successfully.
- Follow-up: Implement physical migrations with their owning feature tasks and complete `P0-T007` MCP contracts.

### 2026-08-20 — Defined governed MCP tool contracts

- Phase: 0 (`P0-T007`)
- Status: Documented
- Files: `docs/MCP_TOOLS.md`, `docs/contracts/mcp-tools.v1.json`, `scripts/validate-mcp-contract.mjs`, root scripts, task status, and README
- Summary: Defined all 20 Sales, Knowledge, and Integration MCP tools with versioned input/output schemas, trusted invocation context, scopes, deterministic approval, side-effect/idempotency classification, bounded timeouts/retries, one result/error model, safe provider-specific failure behavior, and durable audit requirements.
- Verification: The executable MCP validator confirmed exact System Design inventory, unique versioned names, top-level closed schemas, required fields, scope/approval/timeout/retry policies, mandatory side-effect idempotency, audit/failure fields, and FR-08 through FR-14 traceability. API and data-model regressions and whitespace checks passed.
- Follow-up: Implement MCP gateway/tool adapters in Phase 3; complete `P0-T008` topology.

### 2026-08-20 — Added reproducible local service topology

- Phase: 0 (`P0-T008`)
- Status: Added
- Files: `compose.yaml`, `.dockerignore`, `infra/docker`, backend/MCP health applications, frontend health proxy, manifests, lockfile, and repository documentation
- Summary: Added digest-pinned multi-stage production images and a five-service Compose topology for frontend, Flask, MCP, PostgreSQL/pgvector, and Redis. Startup is dependency-health driven; state is persistent; data services are isolated; app containers are non-root, read-only, resource-limited, and protected with `no-new-privileges`.
- Verification: Passed Compose configuration, a clean image build, and a live `--wait` startup. All five containers became healthy; backend readiness confirmed PostgreSQL, Redis, and MCP; the frontend proxy reported backend readiness; pgvector 0.8.6 was installed; all application containers ran as UID 10001 with read-only roots; and a Redis restart recovered without fixed sleeps. Independent GitHub runners rebuilt and scanned frontend, backend, and MCP images with Trivy; all three HIGH/CRITICAL gates passed after npm/corepack were removed from the frontend runtime attack surface.
- Follow-up: None.

### 2026-08-20 — Established local and CI quality gates

- Phase: 0 (`P0-T009`)
- Status: Added
- Files: `.github/workflows/quality.yml`, `docs/QUALITY_GATES.md`, root/frontend manifests, Ruff/mypy/pytest/ESLint configuration, health tests, quality/secret scripts, Docker frontend build, and lockfiles
- Summary: Added one fail-fast local gate matching CI for runtime pins, formatting, linting, strict TypeScript and Python typing, unit and contract tests, credential scans, dependency audits, and deliberate invalid-fixture proof. Added isolated per-image Trivy jobs pinned to an immutable action commit and made frontend lint/type checks mandatory image-build stages. Corrected the unsupported ESLint 10/Next.js combination to the pinned ESLint 9 maintenance release.
- Verification: On stacked PR #9, the `quality` job passed clean installation, runtime verification, Ruff, strict mypy, 19 tests, three contract validators, frontend/repository secret scans, the deliberate credential rejection, dependency audits, ESLint, strict TypeScript, and the production build. All three independent Trivy HIGH/CRITICAL image gates passed. GitHub's branch-protection API returned HTTP 403 because protection is unavailable for this private repository on its current plan.
- Follow-up: Upgrade the repository plan or make it public and require all checks listed in `docs/QUALITY_GATES.md`; only then mark `P0-T009` complete.

### 2026-08-20 — Added operational documentation and clean bootstrap gate

- Phase: 0 (`P0-T010`)
- Status: Documented
- Files: `docs/DEVELOPMENT.md`, `docs/RELEASE.md`, `docs/ROLLBACK.md`, `docs/INCIDENT_RESPONSE.md`, operational validator, quality workflow, deployment documentation, and README
- Summary: Added exact fresh-checkout setup, validation, configuration, migration, troubleshooting, release, rollback/recovery, incident severity, triage, contact-role, reconciliation, and production release-gate guidance. Added an executable documentation validator and an independent clean-runner topology job that builds, starts, probes, restarts, recovers, and tears down the full stack.
- Verification: Operational documentation validation and repository secret scanning passed. On stacked PR #10, quality, all three Trivy image scans, and the clean-runner topology job passed; the topology reached healthy state, both HTTP readiness paths passed, and Redis restart recovery passed without fixed sleeps.
- Follow-up: Resolve `P0-T009`, map roles to named private on-call contacts, and obtain a new-engineer walkthrough sign-off before marking `P0-T010` complete.

### 2026-08-21 — Implemented versioned sales domain models

- Phase: 1 (`P1-T001`, GitHub #14)
- Status: Added
- Files: `backend/src/knotic_api/domain`, model tests and schema hash snapshots, Phase 1 task status
- Summary: Added strict immutable version-1 models for SalesState, customers, requirements, objections, qualification, messages, tool calls, outcomes, and domain events. Added RFC 9562 UUIDv7 generation/validation, bounded enums and scores, typed requirement values, tenant/session ownership checks, provider-confirmed booking invariants, and deterministic session-transition rules.
- Verification: Ruff formatting/linting and strict mypy passed; five unit tests passed for invalid identifiers/enums/scores/transitions, typed business invariants, JSON round trips, and nine reviewed schema snapshots; whitespace and repository secret scans passed.
- Follow-up: Implement `P1-T002` physical PostgreSQL migrations from these types.

### 2026-08-21 — Implemented tenant-isolated PostgreSQL schema

- Phase: 1 (`P1-T002`, GitHub #15)
- Status: Added
- Files: `backend/src/knotic_api/persistence/schema_v1.py`, Alembic configuration and initial revision, PostgreSQL schema tests, integration Compose override, CI persistence job, dependency lock, Phase 1 task status
- Summary: Added the immutable initial Alembic revision for all 30 documented entities, pgvector 0.8.6 setup, UUID tenant boundaries, composite foreign keys, encrypted-field storage columns, lifecycle and qualification checks, optimistic versions, partial/keyset/HNSW indexes, and enabled-and-forced RLS policies that fail closed without tenant context. API startup remains migration-free. Corrected the stale entity count in the earlier documentation ledger and data-model validator output.
- Verification: Alembic compiled the revision offline and reported one head; metadata contained exactly 30 tables and all foreign keys had supporting left-prefix indexes. Against the pinned PostgreSQL 17/pgvector container, three schema tests passed: fresh upgrade, 29 forced-RLS tenant tables, pgvector presence, database rejection of an out-of-range score, use of `ix_sales_sessions_tenant_status_updated_id` in `EXPLAIN`, populated downgrade to base, and clean re-upgrade. Ruff formatting/linting and strict mypy passed for the migration/schema code. CI now repeats the integration rehearsal against the same digest-pinned image.
- Follow-up: Implement `P1-T003` Redis active-state persistence and add its real-service checks to the persistence integration job.

### 2026-08-21 — Implemented atomic Redis active-state persistence

- Phase: 1 (`P1-T003`, GitHub #16)
- Status: Added
- Files: `backend/src/knotic_api/persistence/active_state.py`, Redis repository tests, integration Compose override, persistence CI job, backend documentation, Phase 1 task status
- Summary: Added a versioned, immutable `SalesState` cache envelope carrying tenant/session identity, state version, durable event watermark, update timestamp, and lease fencing token. Keys include environment and a tenant/session Redis Cluster hash tag. Writes use NX creation or a Lua compare-and-set that atomically rejects lost updates and stale fencing tokens. Reads have sliding TTLs, strict schema/identity/size validation, and race-safe compare-and-delete corruption recovery. Connection failures are explicit and never reported as successful writes.
- Verification: Ruff and strict mypy passed. Five repository tests passed against the pinned Redis 8.8.1 container for exact namespacing, serialization round trip, sliding and actual TTL expiry, simultaneous-writer conflict behavior, stale fencing rejection, duplicate creation, corrupt/schema-invalid cleanup, cache miss, and bounded connection-outage failure. CI repeats the Redis tests alongside PostgreSQL integration tests.
- Follow-up: Implement `P1-T004` durable repositories and unit-of-work transaction boundaries.

## Maintenance rules

- Update this file in the same change that modifies the project.
- Change a phase to `IN_PROGRESS` when its first implementation task begins.
- Mark a phase `COMPLETE` only after all exit criteria are satisfied.
- If work from a later phase is implemented early, log it under its actual phase rather than the current phase.
- Record verified behavior, not planned or assumed behavior, in the change log.
- Do not use this file to silently change requirements or architecture.
