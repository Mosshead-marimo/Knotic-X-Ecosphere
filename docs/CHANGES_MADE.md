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
| 1 | Core backend, state, and persistence | COMPLETE |
| 2 | Adaptive sales workflow | COMPLETE |
| 3 | MCP tools and grounded knowledge | IN_PROGRESS |
| 4 | Realtime Agora voice experience | IN_PROGRESS |
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

### 2026-08-21 — Implemented durable repositories and unit-of-work boundaries

- Phase: 1 (`P1-T004`, GitHub #17)
- Status: Added
- Files: `backend/src/knotic_api/persistence/repositories.py`, `unit_of_work.py`, session-state alignment migration, durable repository integration tests, CI persistence job, backend documentation, Phase 1 task status
- Summary: Added one explicit short-lived SQLAlchemy transaction boundary that sets trusted tenant and optional actor context before exposing repositories. Added typed session, lead, message, confirmed-requirement, objection, meeting, follow-up, tool call/result, outcome, domain-event, and idempotency operations. Every read/write repeats tenant predicates; domain records must match the transaction tenant; session writes use legal-transition and optimistic-version predicates; confirmed requirement writes lock current state and append history; idempotency uses a unique atomic reservation before business writes. Added a reversible migration aligning durable session statuses with the accepted domain state machine.
- Verification: Ruff and strict mypy passed. Six real-PostgreSQL migration/repository tests passed for fresh/head migration, populated rollback, injected mid-transaction rollback, tenant-scoped reads, legal optimistic transitions, stale-version rejection, unique idempotency reservation, different-payload replay detection, and exactly one business record. The status-alignment migration upgraded cleanly and its downgrade translates in-flight/failed states to the prior schema vocabulary before restoring its constraints. CI includes these tests in the pinned persistence job.
- Follow-up: Implement `P1-T005` authenticated, idempotent, rate-limited session lifecycle APIs on these boundaries.

### 2026-08-21 — Implemented authenticated session lifecycle APIs

- Phase: 1 (`P1-T005`, GitHub #18)
- Status: Added
- Files: Flask lifecycle API/security composition, server-session/rate-limit/replay security primitives, lifecycle contract tests, configuration and secret inventory, dependency manifests/lock, CI persistence job, backend documentation, Phase 1 task status
- Summary: Implemented contract-aligned create/read/end session routes with strict JSON and IANA locale/timezone validation, UUID request/correlation propagation, opaque Redis-backed browser authentication, exact-origin and session-bound HMAC CSRF enforcement, fixed-window per-actor rate limits, tenant-hidden reads, optimistic end transitions, durable lifecycle events/outcomes, and safe error envelopes. Mutation idempotency uses HMAC-blinded keys, canonical request hashes, atomic PostgreSQL reservations, different-payload conflict detection, and AES-256-GCM encrypted response replay. Added the required session security secret and purpose-separated derived keys. Aligned the domain outcome vocabulary and reviewed schema snapshots with the already-accepted OpenAPI/requirements contract.
- Verification: Ruff, strict mypy, domain schema snapshots, and configuration/health tests passed; the full suite passed 38 tests with both real persistence services enabled. Three end-to-end Flask tests covered unauthenticated/origin/CSRF/validation failures, OpenAPI JSON Schema validation of live success/error bodies, create and end replay, encrypted replay-at-rest proof, different-payload conflict, stale version conflict, cross-tenant 404 isolation, exactly one session/outcome, ordered lifecycle events, and rate-limit headers. The digest-based backend image rebuilt successfully and ran healthy as UID 10001 with a read-only root; disabling Gunicorn's optional control socket removed its only attempted home-directory write. The full persistence CI job now runs migration, Redis, repository, and lifecycle suites on pinned service images.
- Follow-up: Continue with `P1-T006`; OIDC login/session issuance remains required before external production access, per the existing API contract.

### 2026-08-22 — Implemented structured memory updates

- Phase: 1 (`P1-T006`, GitHub #26)
- Status: Added
- Files: structured-memory domain models and merge rules, domain exports/schema snapshot, table-driven memory tests, backend documentation, Phase 1 task status
- Summary: Added immutable structured-memory facts with tenant/session identity, confirmation, confidence, actor, source-turn, capture-time, and version provenance. Added deterministic pure merge rules for customer name, company, role, users, use cases, integrations, budget, timeline, competitors, current topic, next action, and objections. Confirmed values resist tentative extraction, exact replays are idempotent, stale/cross-session/same-source conflicts fail closed, and accepted facts update typed `SalesState` projections without treating transcript text as memory.
- Verification: Ruff formatting and linting passed; strict mypy passed for all 25 source modules; 19 focused domain/memory tests and the complete 41-test non-integration suite passed. Coverage includes every required field, provenance retention, confirmation precedence, replay, conflict, tenant/session ownership, typed requirement projection, objection revision, serialization, transition, and the reviewed schema snapshot. API, data-model, MCP, operations-document, repository-secret, and whitespace validators passed. The host has Node 22 instead of the repository-pinned Node 24, so JavaScript validators were invoked directly; pinned-runtime enforcement remains covered by CI.
- Follow-up: Continue with `P1-T007` to make confirmed requirement replacements, revision history, and `requirement.updated` events one atomic durable operation.

### 2026-08-22 — Implemented requirement revision and event history

- Phase: 1 (`P1-T007`, GitHub #27)
- Status: Added
- Files: requirement/source domain vocabulary, durable requirement/event repositories, PostgreSQL head metadata, Alembic revision `20260822_0003`, migration/repository integration tests, backend documentation, Phase 1 task status
- Summary: Added one serialized and atomic durable operation for latest-confirmed requirement replacement, immutable old/new revision history, and ordered `requirement.updated` event creation. Revision rows and events retain actor, source, source-turn, causation, correlation, and version metadata. Exact source-turn replays return the original event without duplication; conflicting replays, unconfirmed values, invalid identities, stale versions, and concurrent losing corrections fail closed. Added a unique per-session event-sequence boundary and a compatibility migration that handles both historical release-0002 databases and fresh databases created through the repository's legacy live-metadata initial migration.
- Verification: Ruff formatting/linting, strict mypy, and Alembic one-head validation passed. Seven real-PostgreSQL tests passed for the FR-05 `50 -> 250` example, current value, ordered old/new history, event payload and actor/source metadata, exact replay, conflicting replay, simultaneous corrections with exactly one winner, transaction rollback, tenant scoping, fresh migration, populated release-0002 upgrade, downgrade, re-upgrade, RLS, constraints, and query plans. The complete suite passed 55 tests against the pinned PostgreSQL 17/pgvector and Redis 8.8.1 containers.
- Follow-up: Continue with `P1-T008` to hydrate cache misses from durable state, checkpoint watermarks, migrate projection versions, and recover after cache/process loss.

### 2026-08-22 — Implemented state hydration and recovery

- Phase: 1 (`P1-T008`, GitHub #28)
- Status: Added
- Files: Redis envelope migration, durable projection/checkpoint repository, state hydration and field-encryption boundary, checkpoint/confidence Alembic revision `20260822_0004`, recovery tests, persistence CI coverage, backend documentation, Phase 1 task status
- Summary: Added Redis-first state loading with tenant-scoped durable reconstruction, authenticated decryption of sensitive lead/session/objection fields, typed current requirements plus revision provenance, qualification/outcome reconstruction, event-watermark validation, create-only cache warming, race handling, optimistic durable checkpoints, and atomic migration of legacy pre-watermark Redis envelopes. Requirement changes now advance the owning durable session version and checkpoint watermark. Recovery reads committed events but never appends or duplicates them. Corrected objection upsert confirmation to use PostgreSQL `RETURNING` rather than driver-dependent row counts.
- Verification: Ruff formatting/linting and strict mypy passed across 26 source modules; Alembic reported one head at `20260822_0004`. The complete 58-test suite passed against pinned PostgreSQL 17/pgvector and Redis 8.8.1. Recovery coverage flushed Redis, rebuilt encrypted customer/requirement/provenance/objection state, instantiated a replacement service process, loaded the warmed cache, committed and recovered a newer checkpoint, verified exact event watermarks and unchanged event count, hid cross-tenant sessions, migrated a legacy cache envelope in place, and repeated fresh/populated migration downgrade/re-upgrade tests.
- Follow-up: Continue with `P1-T009` to add least-privilege runtime roles, retention/erasure/export workflows, data minimization, and sensitive log filtering.

### 2026-08-22 — Enforced privacy, retention, and tenant isolation

- Phase: 1 (`P1-T009`, GitHub #29)
- Status: Added
- Files: privacy lifecycle and logging modules, tenant-scoped privacy repository, Redis privacy tombstone, least-privilege role migration `20260822_0005`, lifecycle/security tests, persistence CI, data model/validator, data-lifecycle runbook, backend documentation, Phase 1 task status
- Summary: Added AES-256-GCM field protection with tenant/aggregate/field authentication, allowlisted minimized audit metadata, versioned exports, idempotent erasure requests, atomic Redis processing tombstones, provider-confirmation gating, legal-hold enforcement, foreign-key-ordered erasure, 365-day minimization, 400-day purge, expired idempotency cleanup, terminal-operation sanitization, and replay-safe audit actions. Installed operational log redaction on Flask handlers. Added `NOLOGIN`/`NOBYPASSRLS` runtime, retention, and auditor group roles with append-only history restrictions and no auditor writes. Documented exact export/erasure/retention/restore procedures and external provider/backup gates.
- Verification: Ruff formatting/security linting and strict mypy passed across 28 source modules; Alembic reported one head at `20260822_0005`. Three real-service privacy integration tests passed for encrypted export, audit minimization, erasure request/completion, Redis eviction and persistent processing block, retained proof, 365-day minimization, 400-day purge, active legal-hold skip, runtime cross-tenant hiding, and auditor write denial. Two unit tests passed for credential/email/connection/payload log redaction and invalid retention-window rejection. The complete suite passed 63 tests against the pinned PostgreSQL 17/pgvector and Redis 8.8.1 containers after tombstone hardening. API, eight-key data-model, MCP, operations-document, repository-secret, and whitespace validators passed.
- Follow-up: Continue with `P1-T010` for metrics, traces, latency/load baselines, dashboards, alerts, and approved service-level targets. Production activation still requires provider deletion adapters and backup tombstone replay rehearsal per `DATA_LIFECYCLE.md`.

### 2026-08-22 — Added state/data observability and performance baselines

- Phase: 1 (`P1-T010`, GitHub #30)
- Status: Added
- Files: state/data observability module and instrumentation, private metrics route, telemetry configuration, SLO runbook, Prometheus alerts, Grafana dashboard, observability and load tests, dependency lock, persistence CI, Phase 1 task status
- Summary: Added a per-process Prometheus registry with bounded labels for state latency, cache results, concurrency conflicts, retries, durable recovery, failures, and SQLAlchemy pool health. Added correlation-aware OpenTelemetry state-load spans with optional OTLP/HTTP export and no business identifiers or content. The internal metrics route is disabled by default, requires a dedicated constant-time-compared bearer token when enabled, and managed environments now require both metrics authentication and an OTLP destination. Added executable alert thresholds, a six-panel dashboard, response guidance, and service-level objectives for cache/recovery latency, cache hits, conflicts, recovery failures, and pool saturation.
- Verification: Ruff formatting/security linting and strict mypy passed across 29 backend source modules. The complete 69-test repository suite passed against pinned PostgreSQL 17/pgvector and Redis 8.8.1 containers, including the expected-load baseline of 500 cache reads across 25 workers with measured p95 at or below 75 ms. Tests also verified metrics authentication/disablement, bounded metric output, correlation-only span attributes, alert threshold activation, dashboard JSON parsing, and complete Prometheus rule coverage. API, data-model, MCP, operations-document, frontend-secret, repository-secret, and whitespace validators passed. The production backend image built successfully from its digest-pinned Python 3.13 base with the locked runtime dependency set.
- Follow-up: Run the documented 30-minute staging workload and attach its dashboard snapshot before production release; configure private monitoring ingress, a rotated metrics token, and the production OTLP collector endpoint.

### 2026-08-26 — Defined LangGraph state and node contracts

- Phase: 2 (`P2-T001`, GitHub #43)
- Status: Added
- Files: `knotic_api.workflow` state/checkpoint/error contracts, compile-only graph topology, workflow contract tests, backend documentation, Phase 2 task status
- Summary: Added a versioned typed LangGraph state carrying the durable `SalesState`, authenticated semantic turn, tenant/session-derived checkpoint identity, bounded workflow artifacts, events, route, objection decision, and safe failure. Defined every planned graph node, its pure-versus-I/O classification, and an explicit state-field mutation allowlist. Added safe, retry-aware failure codes and a stable compile-only graph whose contract nodes cannot perform model, provider, database, or MCP work.
- Verification: Ruff formatting/linting and strict mypy passed across 32 backend source modules. Three focused tests passed for checkpoint/session identity binding, strict turn schemas, forbidden state mutation rejection, complete node-contract coverage, pure/I/O boundary coverage, stable graph node/edge inventory, and successful LangGraph compilation.
- Follow-up: Implement `P2-T002` through the typed model port and replace only the `understand_turn` contract node.

### 2026-08-26 — Implemented turn understanding and structured extraction

- Phase: 2 (`P2-T002`, GitHub #44)
- Status: Added
- Files: workflow intent/entity/ambiguity contracts, provider-neutral understanding port, OpenAI Responses adapter, injection-resistant prompt policy, golden conversation fixtures/tests, configuration, dependency lock, backend documentation, Phase 2 task status
- Summary: Added all 12 FR-06 intents, bounded confidence and ambiguity contracts, trusted turn/provider provenance, exact source offsets, typed customer entity proposals, and a strict extraction schema that excludes model control over topic, next action, tools, and outcomes. Added a provider-neutral port and an OpenAI Responses structured-output adapter using the approved `gpt-5.6-terra` model, non-retained requests, zero SDK retries, a 12-second bounded timeout, low reasoning effort, and safe error mapping. Customer utterances are JSON-encoded as explicitly untrusted prompt data; malformed/refused output raises a safe workflow failure before any graph state mutation.
- Verification: Ruff formatting/security linting and strict mypy passed across 35 backend source modules. All 55 non-integration repository tests passed. Four focused understanding tests covered the complete FR-06 inventory, Spanish and accented-English fixtures, an adversarial prompt-injection fixture, entity type/span/ambiguity validation, forbidden model-controlled fields, malformed-output non-mutation, graph integration, non-retained structured Responses calls, and trusted provenance attachment.
- Follow-up: Run the versioned golden set against the deployment account/model during `P2-T010`; implement deterministic confirmed-versus-uncertain memory application in `P2-T003`.

### 2026-08-26 — Implemented the memory update graph node

- Phase: 2 (`P2-T003`, GitHub #45)
- Status: Added
- Files: deterministic memory graph node, uncertain-claim/checkpoint contracts, `memory.updated` domain/API event, graph composition, schema snapshot, workflow tests, API/data-model documentation and validators, backend documentation, Phase 2 task status
- Summary: Added a pure memory node that converts only explicit, unambiguous entity proposals at confidence 0.85 or higher into confirmed structured facts. Inferred, ambiguous, and low-confidence proposals are preserved separately with value, confidence, reason, and source-turn time and cannot displace confirmed state. Confirmed facts use replay-stable derived UUIDv7 identifiers, existing confirmation/revision precedence, typed budget/list/user values, and automatic customer projection initialization. Accepted requirement and non-requirement changes emit ordered `requirement.updated` or new `memory.updated` events with old/new values and provenance, while checkpoint state and event watermarks advance together. Current topic is derived deterministically from validated intent and is retained across ambiguous turns.
- Verification: Ruff formatting/security linting and strict mypy passed across 36 backend source modules. All 58 non-integration repository tests passed. Three focused node tests covered the FR-05 `50 -> 250` revision, event history and watermark order, topic updates, exact replay without duplicate events, ambiguous/inferred preservation, confirmed-value protection, and missing-provenance failure. The reviewed domain schema snapshot, 9-event OpenAPI contract, API validator, data-model lifecycle validator, repository-secret scan, and whitespace checks passed.
- Follow-up: Persist the emitted event batch atomically with graph checkpoints in `P2-T009`; implement nonlinear intent/topic routing in `P2-T004`.

### 2026-08-26 — Implemented deterministic intent routing and nonlinear topic control

- Phase: 2 (`P2-T004`, GitHub #46)
- Status: Added
- Files: route/topic-control contracts, deterministic routing node, conditional LangGraph topology, route matrix and multi-topic tests, prompt policy, memory-topic integration, backend documentation, Phase 2 task status
- Summary: Added a closed route for every FR-06 intent plus a safe clarification route. The compiled LangGraph now conditionally dispatches from `route_turn` into 13 typed, no-side-effect branch boundaries and rejoins before qualification. Added explicit `CONTINUE`, `SWITCH`, and `RETURN_PREVIOUS` controls backed by a typed 32-frame topic history. Routing uses only validated understanding and graph state: ambiguity, missing prior topics, or impossible return requests choose clarification; successful returns search prior distinct frames deterministically. Memory topic updates use the same route decision, so continuing a topic cannot be overwritten by a nested general question.
- Verification: Ruff formatting/security linting and strict mypy passed across 37 backend source modules. All 73 non-integration repository tests passed. Fifteen focused route tests covered the complete 12-intent matrix, unique route-node ownership, switch/continue/return behavior, missing-history clarification, ambiguous-history preservation, memory/current-topic alignment, and conditional branch/rejoin graph edges. API and data-model validators and whitespace checks passed.
- Follow-up: Implement objection classification and escalation before route dispatch in `P2-T005`; branch-specific knowledge/action behavior remains scoped to later Phase 2/3 tasks.

### 2026-08-26 — Implemented objection detection and handling policy

- Phase: 2 (`P2-T005`, GitHub #47)
- Status: Added
- Files: FR-07 objection/risk/policy contracts, pure detection node, replay-stable identifiers, prompt constraints, immutable evidence repository/schema, Alembic revision `20260826_0006`, `objection.updated` domain/API event, workflow and persistence tests, data/API documentation and validators, backend documentation, Phase 2 task status
- Summary: Added exact classification for PRICE, COMPETITOR, SECURITY, TRUST, FEATURE_GAP, IMPLEMENTATION, TIMELINE, BUDGET, and AUTHORITY objections using validated source offsets and confidence. A closed deterministic policy grounds pricing, comparisons, and capabilities; requests discovery for missing implementation/timeline/authority facts; and escalates security, legal, trust, and high-risk cases. Unsupported claims require grounding or escalation. Repeated objections preserve stable identity, first/latest turns, versioned current state, and immutable per-turn evidence without being erased by topic changes. Evidence persistence stores hashes, offsets, risk flags, policy, and escalation decisions under forced RLS; plaintext detail remains encrypted in the existing current-state table. The compatibility migration corrects active-objection uniqueness to the actual `OPEN` status and maps legacy TIMING/INTEGRATION categories.
- Verification: Ruff formatting/security linting and strict mypy passed across 31 backend source modules. All 72 non-integration tests and the complete 94-test suite passed; the latter ran against isolated pinned PostgreSQL 17/pgvector and Redis 8.8.1 containers. Coverage includes the complete nine-category matrix, security/legal/trust escalation, unsupported-claim grounding, exact evidence spans, repeated objection version/history preservation, exact replay without duplicate evidence/events, topic-change retention, conflicting durable replay rejection, runtime RLS cross-tenant hiding, fresh migration, populated rollback/re-upgrade, and one Alembic head at `20260826_0006`. The 10-event OpenAPI contract, 31-entity data model, MCP contract, operations documentation, repository-secret, and frontend-secret validators passed.
- Follow-up: Implement deterministic qualification scoring in `P2-T006`; atomically persist the graph's objection current state, immutable evidence, event batch, and checkpoint as one unit in `P2-T009`.

### 2026-09-03 — Implemented deterministic qualification policy

- Phase: 2 (`P2-T006`, GitHub #59)
- Status: Added
- Files: FR-09 stage vocabulary, qualification evidence/override/assessment contracts, pure qualification node, graph integration, Hypothesis property tests and lock, reviewed domain schema snapshot, backend documentation, Phase 2 task status
- Summary: Added deterministic scoring for Business Need (25), Product Fit (20), Deployment Fit (15), Timeline (15), Authority (10), Budget (5), and Purchase Intent (10) from confirmed structured memory and validated routes. Every calculation records points, maxima, closed evidence codes, missing-data state, prior score, source turn, and replay-stable identity. Score-derived stages use the exact 0/40/60/75 boundaries. Explicit booking, demo, follow-up, and closing requests can override only the stage through an auditable closed reason; the underlying score remains evidence-derived. Same-turn replay cannot duplicate history or `qualification.updated` events.
- Verification: Ruff and strict mypy passed across 41 source modules. Thirty-two focused tests passed, including the exact 39/40/59/60/74/75 boundaries, Hypothesis coverage of every score from 0 through 100, complete seven-dimension scoring, missing-data behavior, explicit booking override provenance, checkpoint/event advancement, and exact replay.
- Follow-up: Implement the deterministic FR-10 action policy in `P2-T007`; durable atomic graph persistence remains scoped to `P2-T009`.

### 2026-09-03 — Implemented deterministic next-best-action policy

- Phase: 2 (`P2-T007`, GitHub #60)
- Status: Added
- Files: complete FR-10 action vocabulary, action input/approval/decision contracts, deterministic action policy and graph node, persisted-vocabulary compatibility migration `20260903_0007`, decision/prohibited-transition tests, backend documentation, Phase 2 task status
- Summary: Added one closed policy decision for every route and all twelve FR-10 actions, with explicit precedence for high-risk objection handoff and safe failure fallback. Each decision records its source turn, reason, required/missing inputs, MCP tool boundary, approval requirement, grounding rule, provider-confirmation rule, and safe fallback. Calendar booking remains non-executable without a selected slot and bound customer confirmation and always requires a provider-confirmed `calendar.book_meeting` result. Follow-up and handoff remain policy-governed; pricing, product, competitor, and objection answers remain grounded. A reversible migration translates the two legacy persisted action names to the accepted API vocabulary.
- Verification: Decision-table tests cover all thirteen routes and twelve FR-10 actions. Prohibited-transition tests verify booking input/confirmation gates, high-risk objection precedence, tool/approval metadata, state/checkpoint mutation, and exact same-turn replay. Ruff, strict mypy, full repository tests, migration, and contract gates run before the component commit.
- Follow-up: Implement grounded response planning and generation in `P2-T008`; action execution remains within the governed MCP tasks.

### 2026-09-01 — Added Agora Voice AI onboarding and recipe guidance

- Phase: 4 (documentation preparation; no task marked complete)
- Status: Added
- Files: Agora Voice AI guide, repository README, Phase 4 implementation references
- Summary: Documented the microphone-to-STT-to-workflow/model-to-TTS pipeline, Agora-managed Deepgram/OpenAI/MiniMax prototype path, the session's 300-minute signup note with a console-verification caveat, BYOK/provider evaluation requirements, CLI onboarding, server-only credential rules, and official recipes for Next.js, Python, interruption, MCP, tool calling, webhooks, and observability. Clarified where recipe architectures must be adapted to preserve Knotic's binding Flask, LangGraph, MCP, state, and transaction-truth boundaries.
- Verification: Reviewed the linked Agora Start with AI guide and Voice AI recipes catalog plus the relevant official recipe pages; checked all added repository-relative documentation links. The X source returned HTTP 403 to the web reader, so its message content is represented from the user-provided text and the link is retained as a source requiring browser access.
- Follow-up: Revalidate provider availability, promotional entitlement, regions, quotas, billing, and current recipe behavior in the Agora Console and official documentation when Phase 4 implementation begins.

### 2026-09-03 — Implemented grounded response planning and generation

- Phase: 2 (`P2-T008`, GitHub #61)
- Status: Added
- Files: grounding/citation/response-plan contracts, versioned generation prompt, stateless OpenAI Responses adapter, deterministic safe responses, LangGraph node integration, golden/security/rubric tests, backend documentation, Phase 2 task status
- Summary: Added a pure response-planning boundary that filters typed server-validated facts by the approved FR-10 action and limits voice plans to four talking points. Required grounding with no evidence now returns deterministic uncertainty language without a model call. Booking, follow-up, and handoff use fixed pre-confirmation language; closing is silent. The generation adapter sends only the approved plan through stateless structured output and excludes raw customer content. Validation rejects unknown or missing citations, more than three sentences, ungrounded prices/capabilities, and claims that a business action completed without a validated provider result. A recorded four-part human review rubric requires full grounding and transaction-safety scores.
- Verification: Ruff and strict mypy passed. The complete non-integration backend suite passed, including golden plan/disposition cases, missing-grounding behavior, structured-output and non-retention settings, prompt-injection isolation, hallucinated pricing/integration probes, unknown/missing citations, transactional wording, provenance, replay, and human-rubric approval.
- Follow-up: Add durable per-session checkpoints, bounded retry policy, and duplicate-turn protection in `P2-T009`.

### 2026-09-03 — Implemented durable workflow recovery and duplicate-turn protection

- Phase: 2 (`P2-T009`, GitHub #62)
- Status: Added
- Files: workflow execution/retry contracts, bounded graph adapter, encrypted PostgreSQL checkpoint store/repository, checkpoint schema and Alembic revision `20260903_0008`, UnitOfWork integration, fault/replay/persistence tests, data/backend documentation, Phase 2 task status
- Summary: Added one forced-RLS checkpoint row per tenant/session/turn with an input hash, bounded lease, attempt counter, deterministic status, safe failure metadata, and authenticated encrypted committed graph state. The executor returns committed or terminal records before invoking LangGraph, so replay cannot duplicate graph-side effects. Retryable failures resume for at most the configured one-to-three attempts; non-retryable failures, exhausted retries, unexpected exceptions, active concurrent leases, and deadlines resolve to explicit safe outcomes. A bounded shared worker pool enforces whole-attempt deadlines while provider adapters retain tighter I/O timeouts.
- Verification: Ruff and strict mypy passed. Fault injection covers every workflow node, bounded retry/resume, timeout exhaustion, active-lease conflicts, terminal replay, committed replay, and no duplicate invocation. The PostgreSQL integration test verifies encrypted state at rest, authenticated replay, conflicting-input rejection, one Alembic head, migration constraints, and the tenant-scoped repository boundary.
- Follow-up: Build the versioned scenario dataset and enforced multi-metric evaluation thresholds in `P2-T010`.

### 2026-09-03 — Added the versioned conversation evaluation gate

- Phase: 2 (`P2-T010`, GitHub #63)
- Status: Added
- Files: versioned conversation corpus/reference predictions, typed evaluation engine/CLI, CI quality-gate command, evaluation operating guide, regression tests, backend documentation, Phase 2 task status
- Summary: Added the `sales-conversations-v1` offline corpus covering discovery, confirmed revision, objections, pricing, competitors, demo, follow-up, handoff, closing, unsafe requests, and provider failure. The deterministic evaluator requires one version-matched prediction per case and measures routing, structured extraction, qualification score/stage, next-action policy, supported-citation grounding, and concise transaction-safe response quality. Approved thresholds require 1.00 for the first five metrics and 0.95 for response quality. The command emits a stable JSON report and returns nonzero below threshold, and now runs explicitly in the required quality gate.
- Verification: The reference evaluation reproduced six 1.00 metric scores. Regression tests proved that a wrong route, unknown citation, and unconfirmed follow-up success claim each fail their corresponding threshold. Ruff and strict mypy passed; all 154 non-integration tests and the exact 23-test CI persistence suite passed against PostgreSQL 17/pgvector and Redis 8.8.1. The direct evaluation CLI, API/data/MCP/operations validators, migration head, repository secret scans, and whitespace checks passed.
- Follow-up: Phase 2 is ready for its stacked review sequence; Phase 3 may consume only committed replay-safe state and governed MCP action boundaries.

### 2026-09-04 — Implemented Knowledge MCP tools

- Phase: 3 (`P3-T006`)
- Status: Implemented, verification not yet run
- Files: `mcp/src/knotic_mcp/knowledge.py` (`KnowledgeQueryService`, `RetrievalPort`, `_support_status`, citation `classification` field added to both retrieval services), `mcp/src/knotic_mcp/app.py` (handlers for `product.search`, `product.get_feature`, `product.get_integration`, `competitor.compare`, `security.get_information`; `create_app` now accepts an optional `knowledge_store` for test seeding), `mcp/src/knotic_mcp/registry.py` (per-tool argument validators matching `docs/contracts/mcp-tools.v1.json`), `mcp/tests/test_knowledge.py`, `mcp/tests/test_gateway.py`, this file.
- Summary: Added the five remaining Knowledge MCP tools on top of the existing `knowledge.search` retrieval path (`PgvectorRetrievalService`/`PostgresPgvectorRetrievalService`). Each tool queries domain-filtered grounded chunks and returns typed data with citations traceable to an approved source, chunk, and document version; every tool returns an explicit `NO_GROUNDED_RESULT` miss (never an inferred fact) when no approved evidence supports the request. `security.get_information` additionally denies `CUSTOMER_CONFIDENTIAL`-classified answers with `PERMISSION_DENIED` unless the caller supplies matching `customer_clearance`. `competitor.compare` grounds each requested dimension independently and reports ungrounded dimensions in `unknowns` rather than failing the whole call. Argument validation was tightened from the previous fail-open fallback (`bool(arguments) or not definition.side_effect`) to schema-matching validators for all five new tools.
- Verification: Not run in this session — the sandboxed shell was unavailable (`VM_DISK_SPACE_INSUFFICIENT`), so `ruff`, `mypy`, and `pytest` could not be executed here. Added unit tests (`test_knowledge.py`) covering grounded success and explicit-miss cases for all five tools, support-status heuristics, partial competitor grounding with `unknowns`, and confidential-topic denial/allow; added gateway-level tests (`test_gateway.py`) covering per-tool `INVALID_ARGUMENT` rejection, `NO_GROUNDED_RESULT` on an empty store, an end-to-end `product.search` success with citations, and `security.get_information` `PERMISSION_DENIED`. Before treating `P3-T006` as complete, run `pytest mcp/tests`, `ruff check mcp/src mcp/tests`, and `mypy mcp/src` and confirm they pass.
- Follow-up: Contract/fuzz coverage against `docs/contracts/mcp-tools.v1.json` and a curated question benchmark (per `P3-T006`'s acceptance criteria) are still outstanding. `P3-T007` (Sales MCP read tools) is next and is greenfield — no pricing domain model or data source exists yet.

### 2026-09-04 — Implemented Sales MCP read tools

- Phase: 3 (`P3-T007`)
- Status: Implemented, verification not yet run
- Files: `mcp/src/knotic_mcp/sales.py` (new — `PricingCatalog`/`PricingPlan`, `get_quote`, `compare_plans`, `qualify_lead`, `stage_for_score`, `decide_next_action`), `mcp/src/knotic_mcp/data/pricing_catalog.json` (new versioned pricing fixture, `source_version: 2026.09.1`), `mcp/src/knotic_mcp/app.py` (handlers for `pricing.get_quote`, `pricing.compare_plans`, `lead.qualify`, `lead.next_action`; `create_app` now accepts an optional `pricing_catalog` for test seeding), `mcp/src/knotic_mcp/registry.py` (argument validators for the four tools), `mcp/tests/test_sales.py` (new), `mcp/tests/test_gateway.py`, this file.
- Summary: `P3-T007` was greenfield — no pricing/plan domain model or data source existed. Per product decision, pricing/plan data is a static, source-controlled JSON fixture (`pricing_catalog.json`) rather than a live external call or a new database schema, versioned with a `source_version` and per-plan `effective_at`/`expires_at`/`source`/`region`/`currency`. `pricing.get_quote` and `pricing.compare_plans` return `PRICE_UNAVAILABLE` (never an invented or stale price) for an unknown plan, a plan outside its effective/expiry window, or a user count below the plan minimum; a quote's `valid_until` is bounded to the lesser of 30 days out or the plan's own expiry so a quote can never outlive its price authority. `lead.qualify` reimplements the same deterministic component maxima and 40/60/75 stage thresholds used by the backend's `assess_qualification`/`buying_stage_for_score` (the `mcp` package intentionally does not import `knotic_api`, per its `pyproject.toml`, so the policy is duplicated rather than shared — flagged as a follow-up risk below). `lead.next_action` maps the `SalesRoute`-shaped `intent` string to one of the twelve contract actions with `reason_codes`, and lets an `explicit_request` (book/demo/follow-up/human-handoff phrases) override the routed action, mirroring the backend's explicit-override pattern without importing it.
- Verification: Not run in this session — the sandboxed shell was unavailable (`VM_DISK_SPACE_INSUFFICIENT`), so `ruff`, `mypy`, and `pytest` could not be executed here. Added `test_sales.py` covering quote computation/provenance, unknown-plan/below-minimum/stale-plan rejection, plan comparison requiring every requested plan to be current, qualification summation and stage boundaries (0/39/40/59/60/74/75/100), out-of-bounds component rejection, default intent routing, explicit-request override, and unrecognized-stage rejection. Added gateway tests in `test_gateway.py` covering `PRICE_UNAVAILABLE` on a stale plan (both single-quote and compare-plans), `INVALID_ARGUMENT` on out-of-range arguments, and end-to-end qualify/next-action success. Before treating `P3-T007` as complete, run `pytest mcp/tests`, `ruff check mcp/src mcp/tests`, and `mypy mcp/src`, and confirm they pass.
- Follow-up: The qualification-threshold and next-action-policy duplication between `mcp/src/knotic_mcp/sales.py` and `backend/src/knotic_api/workflow/{qualification,next_action}.py` should be reconciled (shared package or generated from one source) before this is considered production-safe — today a future change to one policy will not automatically apply to the other. Currency/region tests beyond the single `USD`/`GLOBAL` fixture plan, and a contract/fuzz test against `docs/contracts/mcp-tools.v1.json`, are still outstanding. `P3-T008` (prompt-injection/data-exfiltration defenses) is next.

### 2026-09-04 — Implemented prompt-injection and data-exfiltration defenses

- Phase: 3 (`P3-T008`)
- Status: Implemented, verification not yet run
- Files: `backend/src/knotic_api/workflow/untrusted_content.py` (new — `contains_injection_signal`, `contains_secret_signal`, `is_safe_untrusted_text`), `backend/src/knotic_api/workflow/mcp_boundary.py` (rewritten to extract and sanitize all six T006 knowledge-tool result shapes, not just `knowledge.search`, and to fail closed on oversized/adversarial/malformed payloads rather than raising or partially parsing), `backend/src/knotic_api/workflow/mcp_client.py` (`ALLOWED_TOOLS` allowlist enforced in `HttpMcpClient.call` before any network call; `TOOL_SCOPES`/`least_privilege_scopes` for minimal-scope context construction), `backend/src/knotic_api/workflow/response_generation.py` (output filtering: `validate_response` now rejects an injection echo or a credential-shaped string in the drafted text), `backend/src/knotic_api/workflow/__init__.py` (new exports), `mcp/src/knotic_mcp/knowledge.py` (`compare_competitor` now attaches a citation to each comparison item so the boundary can ground every dimension individually instead of only the aggregate), `backend/tests/test_untrusted_content.py`, `backend/tests/test_mcp_boundary.py`, `backend/tests/test_mcp_client.py`, `backend/tests/test_response_generation.py`, this file.
- Summary: Retrieved chunk text and other tool payloads are treated as untrusted data at every layer they cross. The MCP boundary now recognizes the shape of all six knowledge tools (previously only `knowledge.search` produced facts, silently discarding evidence from the five tools added in `P3-T006`) and applies one sanitizer to every extracted string: injection-phrase detection (ignore/disregard instructions, role-override, system/assistant prefixes, delimiter smuggling, "call the tool", "delete all", "disable audit"), credential-shape detection (API-key/AWS-key/JWT/bearer-token patterns), and a raw-length bound checked *before* truncation so an oversized payload cannot be laundered into a plausible-looking truncated string. A citation whose `chunk_id` isn't a clean identifier, or whose required fields aren't present, is dropped. Failures are indistinguishable from a genuine no-evidence miss, so an attacker probing the defense gets no signal. Tool routing is now allowlisted independently at the `HttpMcpClient` boundary (20 canonical tool names mirroring `docs/contracts/mcp-tools.v1.json`), and `least_privilege_scopes` lets a future caller request only the scopes its named tools need rather than a blanket grant — infrastructure ready for when the graph is wired to MCP (still pending, per `P3-T002`'s status). Output filtering closes the loop on the customer-facing side: even though tool content is pre-sanitized and model instructions are supplied on a separate channel from data, `validate_response` now also rejects a drafted response that echoes an injection phrase or contains a credential-shaped value, regardless of how it got there.
- Verification: Not run in this session — the sandboxed shell was unavailable (`VM_DISK_SPACE_INSUFFICIENT`), so `ruff`, `mypy`, and `pytest` could not be executed here. Added an adversarial corpus in `test_mcp_boundary.py` (poisoned instruction-injection text in both chunk text and citation title, oversized payload, credential-shaped text, mismatched-array-length product results, missing/malformed citations, unknown tool shape, a partially-poisoned result that keeps only its safe match, and per-tool-shape success cases for all six knowledge tools) and confirmed by inspection that none of the sanitizer's trigger phrases appear in the existing evaluation corpus (`backend/evaluations/*.json`) or other test fixtures, so this should not regress the `P2-T010` quality gate. Added `test_mcp_client.py` covering allowlist rejection (no network call attempted) and `least_privilege_scopes` bounds. Extended `test_response_generation.py` with four injection/secret output-filtering probes. Before treating `P3-T008` as complete, run `pytest backend/tests`, `ruff check backend/src backend/tests`, `mypy backend/src`, and re-run the `P2-T010` evaluation gate to confirm no regression.
- Follow-up: This hardens the boundary and client that exist today, but `workflow/graph.py` still does not call `mcp_client`/`mcp_boundary` from any node (per `P3-T002`'s outstanding status) — the defenses here have no adversarial corpus running end-to-end through the graph yet, only at the unit boundary. Canary tenant-isolation coverage lives in `mcp/tests/test_knowledge.py` (`P3-T004`); a cross-package adversarial test that exercises the real gateway plus this boundary together is not yet built. `P3-T009` (freshness/cache/degradation) is next.

### 2026-09-04 — Implemented MCP freshness, cache, and degradation strategy

- Phase: 3 (`P3-T009`)
- Status: Implemented, verification not yet run
- Files: `mcp/src/knotic_mcp/cache.py` (new — `CachePolicy`, `TOOL_CACHE_POLICIES`, `CacheEntry`, `InMemoryToolResultCache`, `RedisToolResultCache`, `ProviderHealthTracker`, `cache_key`), `mcp/src/knotic_mcp/app.py` (`create_app` gains `tool_cache`/`provider_health` parameters; dispatch now checks/serves/populates the cache, tracks provider health, and reports `X-Knotic-Cache`/`X-Knotic-Degraded` response headers; `/health/ready` merges in provider health; default tool handlers now register only if a caller-supplied `registry` didn't already provide one, enabling deterministic provider-failure tests), `mcp/src/knotic_mcp/registry.py` (`ToolRegistry.has_handler`), `mcp/src/knotic_mcp/audit.py` (`AuditRecord`/`audit_record` gain `cache_status`), `mcp/tests/test_cache.py` (new), `mcp/tests/test_gateway.py`, this file.
- Summary: Each of the eight read-only knowledge/pricing tools has an explicit per-tool `CachePolicy` (TTL and stale-if-safe grace window); `lead.qualify`/`lead.next_action` (cheap deterministic functions) and all side-effect tools are deliberately never cacheable. A cache entry carries its own provenance (`cached_at`/`fresh_until`/`stale_until`). Within the fresh window a request is served from cache without touching the provider at all (`X-Knotic-Cache: HIT`). Outside it, the gateway calls the real handler; on a genuine provider failure (`TIMEOUT`/`DEPENDENCY_UNAVAILABLE`, not a deterministic miss like `NO_GROUNDED_RESULT`) for a tool whose policy allows it, a still-in-grace cached answer is served instead (`X-Knotic-Cache: STALE`, `X-Knotic-Degraded: true`) — degraded service is visible on the wire, not silently indistinguishable from a fresh answer. Both pricing tools set `stale_grace_seconds=0`, so a pricing provider failure always surfaces as `DEPENDENCY_UNAVAILABLE`/`PRICE_UNAVAILABLE` and never serves an out-of-date number, satisfying the phase's core pricing-safety acceptance criterion independent of any cache-timing edge case. `ProviderHealthTracker` records consecutive failures per dependency (`knowledge_retrieval`, `pricing_catalog`) and is merged into `/health/ready`'s `checks`, making degraded provider state operationally visible even without an active request. Cache invalidation (`invalidate`/`invalidate_prefix`) is implemented and unit-tested but not yet wired to any automatic trigger (see follow-up).
- Verification: Not run in this session — the sandboxed shell was unavailable (`VM_DISK_SPACE_INSUFFICIENT`), so `ruff`, `mypy`, and `pytest` could not be executed here. Added `test_cache.py` covering cache-key stability/uniqueness, policy `allows_stale` truth table, pricing tools' `allows_stale is False`, freshness/stale-grace window boundaries, in-memory get/set/invalidate/invalidate-prefix, and provider-health open/reset/status behavior. Added gateway-level tests in `test_gateway.py`: a fresh-cache-hit test that clears the underlying knowledge store between calls and proves the second call still succeeds from cache; a stale-if-safe test that pre-seeds an expired-but-in-grace cache entry and injects a handler that raises to simulate a real provider outage (via the new `has_handler`/conditional-registration seam, since a caller-supplied `registry` can now pre-empt any tool's default handler); and a test proving the identical provider-outage scenario for `pricing.get_quote` never serves the pre-seeded entry. Before treating `P3-T009` as complete, run `pytest mcp/tests`, `ruff check mcp/src mcp/tests`, and `mypy mcp/src`.
- Follow-up: Cache invalidation is not yet triggered automatically when a document is re-ingested/tombstoned (`P3-T004`'s `KnowledgeIngestionService.tombstone`) — today staleness is bounded only by each tool's TTL (60–120s), which is an acceptable but not ideal freshness guarantee for a just-updated document; wiring ingestion to call `invalidate_prefix` for the affected tenant/tool is a reasonable next step. `RedisToolResultCache` is written but untested against a live Redis instance (mirrors `PostgresPgvectorRetrievalService`'s existing untested-production-path pattern from `P3-T005`). `P3-T010` (production SLOs) is next.

### 2026-09-04 — Established MCP/RAG production SLOs

- Phase: 3 (`P3-T010`)
- Status: Implemented, verification not yet run — dependency lockfile also needs updating (see below, blocking)
- Files: `mcp/src/knotic_mcp/observability.py` (new — `McpObservability`, `McpSloAlertPolicy`, `McpSloSnapshot`), `mcp/src/knotic_mcp/app.py` (`create_app` gains `observability`; every terminal call — including pre-dispatch policy rejections — now records a metric via `telemetry.record_call`; new `/internal/metrics` route gated by a bearer token, mirroring the backend's route), `mcp/src/knotic_mcp/config.py` (`metrics_auth_token`/`otel_exporter_otlp_endpoint` settings, required in staging/production like the backend), `mcp/pyproject.toml` (adds `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `prometheus-client`, pinned to the same versions as `backend/pyproject.toml`), `docs/MCP_RAG_SLO.md` (new), `infra/observability/prometheus/mcp-rag-alerts.yaml` (new), `infra/observability/grafana/mcp-rag-dashboard.json` (new), `infra/observability/README.md`, `mcp/tests/test_observability.py` (new), this file.
- Summary: Followed the same pattern Phase 1 established for state/data SLOs (`knotic_api.observability`, `docs/STATE_DATA_SLO.md`, the `state-data-*` alert/dashboard pair), applied to the MCP gateway. Every tool call — success, failure, cache hit/stale-serve, and pre-dispatch policy rejection — updates one bounded label set (tool, terminal status, safe error code, cache status, provider name, domain; never a tenant ID, session ID, query, or retrieved chunk). `McpSloAlertPolicy` is an executable mirror of the seven ratio-based thresholds in `docs/MCP_RAG_SLO.md` and the Prometheus rules file (knowledge/sales tool p95 latency, tool error ratio, knowledge miss ratio, cache hit ratio, degraded/stale-serve ratio, policy denial ratio); an eighth alert, `McpProviderDegraded`, is a direct gauge check on `knotic_mcp_provider_health` with no ratio to compute. Pricing correctness is treated as a structural, not statistical, guarantee — enforced by `stale_grace_seconds=0` (`P3-T009`) and tested directly, not just monitored as an SLO ratio, per the SLO doc's explicit callout. `/internal/metrics` requires `KNOTIC_METRICS_AUTH_TOKEN` (mandatory in staging/production, mirroring the backend's `metrics_auth_token`) and returns 404 when unconfigured.
- Verification: Not run in this session — the sandboxed shell was unavailable (`VM_DISK_SPACE_INSUFFICIENT`), so `ruff`, `mypy`, and `pytest` could not be executed here. **Blocking**: `mcp/pyproject.toml` gained three new dependencies but `uv.lock` (the single workspace lockfile) was not regenerated — `uv sync --locked` will fail until someone runs `uv lock` from the repository root. Added `test_observability.py` covering the metrics route's auth/404/no-token-leak behavior, a real rejected tool call's outcome appearing in exported metrics with no tenant/query content, `record_call`'s counter/histogram/trace output, knowledge-miss vs. policy-denial label separation (and that a pricing failure is neither), the provider-health gauge, the full alert-policy-to-artifact traceability check (mirroring `backend/tests/test_observability.py::test_alert_policy_and_monitoring_artifacts_cover_every_slo`), and a healthy snapshot raising no alerts. Before treating `P3-T010` as complete: run `uv lock`, then `pytest mcp/tests`, `ruff check mcp/src mcp/tests`, `mypy mcp/src`, and separately run the load/failure exercise and alert/runbook drill `docs/MCP_RAG_SLO.md` describes (30-minute staging run at 25 and 50 concurrent callers, with a deliberate provider outage) — none of that can be executed from this environment.
- Follow-up: `knotic_mcp_knowledge_index_age_seconds` is defined but not yet populated automatically by the ingestion pipeline (`P3-T004`'s `KnowledgeIngestionService`) — it needs a periodic job reading `knowledge_index_versions`, called out explicitly in `docs/MCP_RAG_SLO.md`. Phase 3 (`P3-T001` through `P3-T010`) is now implemented end-to-end at the unit/component level; the phase gate itself (`docs/phases/PHASE_3_MCP_AND_KNOWLEDGE.md`) still requires the outstanding items logged across `P3-T001`–`P3-T009`'s entries above — most notably that `workflow/graph.py` does not yet call `mcp_client`/`mcp_boundary` from any node, the `uv.lock` regeneration above, and every verification command this session could not run.

### 2026-09-04 — Implemented the Agora session/token service

- Phase: 4 (`P4-T001`)
- Status: Implemented, verification not yet run
- Files: `backend/src/knotic_api/voice/agora_token.py` (new — `build_rtc_token`/`decode_rtc_token`, a typed port of Agora's published AccessToken2 "007" wire format), `backend/src/knotic_api/voice/session_service.py` (new — `AgoraSessionTokenService`, `channel_name_for`/`uid_for` deterministic derivation, `InMemoryAgoraSessionStore`/`RedisAgoraSessionStore`, `LoggingAgoraAuditSink`), `backend/src/knotic_api/voice_api.py` (new — authenticated `POST/DELETE /api/v1/sessions/<id>/voice/token` and `POST .../voice/token/renew`), `backend/src/knotic_api/app.py` (wires `VoiceDependencies` alongside the existing lifecycle dependencies), `backend/tests/test_agora_token.py`, `backend/tests/test_agora_session_service.py`, `backend/tests/test_voice_api.py` (new, `@pytest.mark.integration`), `.github/workflows/quality.yml` (added `test_voice_api.py` to the `persistence-integration` job's explicit file list), `.env.example`, this file.
- Summary: The channel name and Agora numeric UID are always derived server-side from the caller's own tenant, session, and actor identifiers (SHA-256-hashed to fit Agora's 64-byte channel-name limit) — a caller has no way to name another tenant's or session's channel, so cross-tenant/cross-session access is structurally impossible rather than merely checked. `voice_api.py` reuses the existing browser-session/CSRF/origin/rate-limit checks and additionally requires the session to exist for the caller's own tenant (via the existing `RedisSalesStateRepository`) and not be `ENDED`/`FAILED` before minting a token. `AgoraSessionTokenService` tracks one active-session record per (tenant, session) for renewal (`renew` fails closed if there is no prior active issuance) and revocation (`revoke` blocks all further issuance/renewal until a session is reissued), and records an audit event for every issue/renew/revoke/deny outcome without ever logging the token itself. The App Certificate is a server-only constructor argument end to end and is never returned to a caller. `build_rtc_token`/`decode_rtc_token` are a faithful, independently-typed port of Agora's own published `AccessToken2`/`Packer` reference implementation (`AgoraIO/Tools`, `DynamicKey/AgoraDynamicKey`), not a novel reimplementation of the signing scheme.
- Verification: Not run in this session — no working sandbox shell was available, so `ruff`, `mypy`, and `pytest` could not be executed here. `test_agora_token.py`'s subscriber-token test asserts a byte-for-byte match against a fixed `(app_id, app_certificate, channel_name, uid, issue_ts, salt, expire)` input and its expected output token taken verbatim from Agora's own published test suite (`AccessToken2Test.py::test_service_rtc`), which is the strongest verification available without a live Agora account or a working local Python interpreter; the publisher-role and tampered-signature cases are verified by round-tripping through this module's own `decode_rtc_token`. `test_agora_session_service.py` covers deterministic channel/UID derivation, issuance auditing, renew-without-prior-issuance denial, revoke blocking further issuance/renewal, and that two different sessions for the same tenant/actor never share a channel. `test_voice_api.py` (integration, requires `KNOTIC_TEST_DATABASE_URL`/`KNOTIC_TEST_REDIS_URL`) exercises the full HTTP path: a successful publisher-token issuance whose token decodes and verifies against the configured App Certificate and is never present in the response body's raw text as the certificate value; unauthenticated and cross-tenant 401/404; an ended session's own tenant returning 409; and the renew-without-issuance / revoke-then-denied sequence. Before treating `P4-T001` as complete, run `pytest backend/tests`, `ruff check backend/src backend/tests`, `mypy backend/src`, and (for the integration file) `pytest -m integration backend/tests/test_voice_api.py` against real PostgreSQL/Redis.
- Follow-up: There is no live Agora, STT, or TTS provider account configured or exercised anywhere in this session — `build_rtc_token`'s wire-format correctness is verified against Agora's own published fixture, but no token minted here has been presented to a real Agora RTC service. The frontend has no login/session-bootstrap flow yet (a pre-existing gap outside Phase 4's scope), so nothing in this codebase yet obtains the `knotic_session` cookie or CSRF token the new endpoints require — `P4-T002`'s call UI accepts them as inputs rather than fetching them itself. No CORS response headers are configured anywhere in the Flask app; the existing origin/CSRF checks assume a same-origin or reverse-proxied deployment, which is unverified for a genuinely cross-origin frontend/backend split. `P4-T002` (production call UI) is next.

### 2026-09-04 — Implemented the production Agora call UI

- Phase: 4 (`P4-T002`)
- Files: `frontend/src/features/voice/useAgoraCall.ts` (new — `useAgoraCall` hook: join/leave/mute lifecycle, permission/connecting/connected/reconnecting/error states, token renewal on `token-privilege-will-expire`), `frontend/src/features/voice/VoiceCallPanel.tsx` (new — consent checkbox, live call-status region, start/end and mute/unmute controls, visible error text with no dead-end failure state), `frontend/src/app/call/[sessionId]/page.tsx` (new demo/wiring page), `.env.example` (`NEXT_PUBLIC_KNOTIC_API_BASE_URL`), this file.
- Status: Implemented, verification not yet run
- Summary: The hook never imports the Agora SDK at module scope (only `import type` for its TypeScript types); the real SDK is loaded with a dynamic `import()` inside `join()` so the file stays safe during server-side rendering and the App ID/App Certificate never need to reach the browser beyond the short-lived token `P4-T001` mints. Every state transition — requesting microphone permission, connecting, connected, reconnecting after a dropped connection, ending, and every error (denied microphone permission, missing microphone, a failed join) — is rendered through a single `role="status" aria-live="polite"` region plus, on error, a visible `role="alert"` message; native `<button>` elements keep every control keyboard-accessible without extra ARIA wiring. Explicit consent is required (a checkbox) before the start-call button is enabled. Cleanup (leaving the channel, closing the local audio track) runs on unmount and on every `leave()`/error path, so a component unmount can never leave a published microphone track running.
- Verification: Not run in this session — no working sandbox shell was available, so `npm run lint`/`npm run typecheck` could not be executed here, and `agora-rtc-sdk-ng`'s real type definitions were not available to check against locally (no `node_modules` in this session). The event names, method signatures, and `UID`/`ConnectionState` types used (`createClient`, `createMicrophoneAudioTrack`, `client.join`/`publish`/`leave`/`renewToken`, `connection-state-change`, `token-privilege-will-expire`) are the long-stable, documented v4 API surface, but this has not been compiled against the installed package. Before treating `P4-T002` as complete, run `npm run lint` and `npm run typecheck` from `frontend/` (or via `npm run quality` at the repo root) and fix any mismatch against the installed `agora-rtc-sdk-ng` types.
- Follow-up: No accessibility audit tool or real browser/device matrix was run (the phase's own acceptance criterion) — only the source-level a11y choices above. The demo page reads the CSRF token from a query parameter as an explicit placeholder because no login/session-bootstrap flow exists yet anywhere in this codebase (see `P4-T001`'s follow-up); replace it once that flow lands. There is no reconnect-token-renewal integration test (would require a real or mocked Agora client). `P4-T003` (speech input pipeline) is next.

### 2026-09-04 — Implemented the realtime speech input turn-aggregation pipeline

- Phase: 4 (`P4-T003`)
- Files: `backend/src/knotic_api/voice/speech_input.py` (new — `ProviderTranscriptEvent`, `DiscardedTranscript`, `SpeechTurnAggregator`), `backend/tests/test_speech_input.py` (new), this file.
- Status: Implemented, verification not yet run
- Summary: `SpeechTurnAggregator` turns a stream of provider transcription events (partial hypotheses are ignored; only `is_final` events are decided) into an ordered, deduplicated stream of the existing `SemanticTurn` contract (`workflow/contracts.py`), ready for the existing `understand_turn_node`. Ordering is assigned by acceptance order, not by provider-reported timing, so reordered or retransmitted events (packet loss, webhook redelivery) cannot corrupt turn order. Deduplication is by the provider's own per-utterance identifier. A final event is discarded (never becomes a turn) for five explicit reasons — cancelled, duplicate, empty/whitespace-only text (silence), oversized text, or below-confidence-threshold (noise) — each returned as a typed `DiscardedTranscript` rather than silently dropped, so a caller can still observe and count discards for observability. An unrecognized or missing language tag falls back to a configured default locale rather than failing the turn. The module never reads, stores, or logs an audio byte — only text, confidence, timestamps, and language ever pass through it.
- Verification: Not run in this session — no working sandbox shell was available, so `ruff`, `mypy`, and `pytest` could not be executed here. Added unit tests covering: non-final events produce no decision; a final event becomes a correctly sequenced turn; acceptance-order sequencing under simulated packet-loss reordering; duplicate provider-turn-ID discarding; low-confidence (noise) discarding; silence (empty text) discarding; cancelled-event discarding; oversized-text discarding; unrecognized/missing-language fallback; an accented/regional language tag (e.g. `en-IN`) passing through unchanged; and out-of-range constructor validation. Before treating `P4-T003` as complete, run `pytest backend/tests`, `ruff check backend/src backend/tests`, and `mypy backend/src`.
- Follow-up: This module aggregates already-transcribed provider events; no actual STT vendor adapter, Agora webhook receiver, or webhook-authentication/idempotency layer (the "Server-side webhooks" pattern `docs/AGORA_VOICE_AI.md` calls out) exists yet to feed it from a live call — that wiring is a follow-up, not covered by this task's acceptance criteria (ordering/dedup/attribution/no-raw-audio), which this module satisfies at the unit level. `P4-T004` (speech output pipeline) is next.

### 2026-09-04 — Implemented the AI speech output streaming pipeline

- Phase: 4 (`P4-T004`)
- Files: `backend/src/knotic_api/voice/speech_output.py` (new — `chunk_response_text`, `SpeechOutputSession`, `VoiceConfig`, `SpeechSynthesisPort`), `backend/tests/test_speech_output.py` (new), this file.
- Status: Implemented, verification not yet run
- Summary: `chunk_response_text` splits an already-approved response (the output of `response_generation.validate_response`) on sentence boundaries first, so the first sentence can begin synthesis/playback without waiting for the rest of the response; a single sentence longer than the per-chunk limit is further split on word boundaries rather than dropped, and a pathologically long response (which an approved response should never be) is defensively truncated to a maximum chunk count. `SpeechOutputSession` streams chunks one at a time via a generator, tracking exactly which chunks were delivered versus cancelled; `cancel()` is idempotent and only ever records the *first* cancellation boundary, and `stream()` checks that boundary both before and after each synthesis call so a chunk whose synthesis was already in flight when cancellation landed is still never yielded — satisfying FR-02 (nothing already spoken is claimed unheard; nothing cut off is claimed heard). A per-chunk primary/fallback synthesis provider pair is supported: a `SynthesisProviderError` from the primary automatically retries the same chunk on the configured fallback before propagating.
- Verification: Not run in this session — no working sandbox shell was available, so `ruff`, `mypy`, and `pytest` could not be executed here. Added unit tests covering: sentence-boundary chunking; empty/whitespace input; oversized-sentence word-boundary splitting (with an exact reconstruction check); long-response truncation to a maximum chunk count; full in-order delivery for a normal response; cancel-before-streaming yielding nothing; cancel-between-chunks stopping at the confirmed boundary; cancellation triggered *during* an in-flight synthesis call (via a fake provider that cancels the session from inside its own `synthesize()`) never allowing that chunk to play; idempotent repeated cancellation; primary-failure-falls-back-to-fallback; and failure-with-no-fallback propagating. Before treating `P4-T004` as complete, run `pytest backend/tests`, `ruff check backend/src backend/tests`, and `mypy backend/src`.
- Follow-up: No real TTS provider adapter (managed MiniMax/ElevenLabs/etc., per `docs/AGORA_VOICE_AI.md`) implements `SpeechSynthesisPort` yet — only the chunking/cancellation/fallback state machine exists, verified against fakes. Playback-begins-within-latency-budget (the phase's own acceptance criterion) has not been measured against a real provider. `P4-T005` (barge-in) is next.

### 2026-09-04 — Implemented barge-in detection and interrupted-response state

- Phase: 4 (`P4-T005`)
- Files: `backend/src/knotic_api/voice/barge_in.py` (new — `VoiceActivitySignal`, `BargeInPolicy`, `InterruptionRecord`, `BargeInController`), `backend/tests/test_barge_in.py` (new), this file.
- Status: Implemented, verification not yet run
- Summary: `BargeInPolicy` rejects a voice-activity burst as a false positive unless it clears both a minimum confidence and a minimum duration, so a brief noise spike or cough cannot interrupt playback. `BargeInController.interrupt` cancels the given `P4-T004` `SpeechOutputSession` immediately (prioritizing input over output, per FR-02/ADR-005) and returns an `InterruptionRecord` carrying the exact delivered and truncated text; calling it again for the same, already-interrupted session returns the identical record rather than moving the boundary, so repeated barge-in and near-simultaneous ("race") interrupt calls are both safe. Because each `SpeechOutputSession` owns its own cancellation state, interrupting two different sessions in quick succession ("rapid-turn") never lets one response's boundary leak into another's. `should_resume_previous_topic` is a separate, explicit decision: it resumes the interrupted topic when the customer's interruption never became a real utterance (a false start) or still references the same topic, and treats anything else as a topic change so the agent addresses what was actually just said rather than continuing to talk over it.
- Verification: Not run in this session — no working sandbox shell was available, so `ruff`, `mypy`, and `pytest` could not be executed here. Added unit tests covering: low-confidence and too-brief bursts rejected as false positives; a confident, sustained burst accepted; invalid policy bounds rejected; `should_interrupt` matching the configured policy; a mid-playback interruption recording the correct delivered/truncated boundary and halting the stream; repeated interruption of the same session keeping the first boundary; two near-simultaneous interrupt calls producing one consistent boundary; two separate sessions' interruptions staying independent; resuming after a false start; resuming when the new utterance still references the previous topic; and not resuming after an actual topic change. Before treating `P4-T005` as complete, run `pytest backend/tests`, `ruff check backend/src backend/tests`, and `mypy backend/src`.
- Follow-up: `should_resume_previous_topic`'s topic match is a simple case-insensitive substring check on plain strings, not yet wired to `SalesState.current_topic` or the graph's actual next-action routing — that integration, plus the real voice-activity detector feeding `VoiceActivitySignal` from a live Agora session, are follow-ups. With `P4-T001` through `P4-T005` implemented at the unit level, Phase 4's remaining tasks (`P4-T006` synchronizing voice events with backend state, `P4-T007` recovery/graceful termination, `P4-T008` privacy/consent/abuse controls, `P4-T009` observability, `P4-T010` performance certification) and the phase gate itself are still outstanding, and none of this session's verification commands have actually been run — see each entry above for the exact commands still needed.

## Maintenance rules

- Update this file in the same change that modifies the project.
- Change a phase to `IN_PROGRESS` when its first implementation task begins.
- Mark a phase `COMPLETE` only after all exit criteria are satisfied.
- If work from a later phase is implemented early, log it under its actual phase rather than the current phase.
- Record verified behavior, not planned or assumed behavior, in the change log.
- Do not use this file to silently change requirements or architecture.
