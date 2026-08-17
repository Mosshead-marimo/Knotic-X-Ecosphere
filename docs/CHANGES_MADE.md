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

## Maintenance rules

- Update this file in the same change that modifies the project.
- Change a phase to `IN_PROGRESS` when its first implementation task begins.
- Mark a phase `COMPLETE` only after all exit criteria are satisfied.
- If work from a later phase is implemented early, log it under its actual phase rather than the current phase.
- Record verified behavior, not planned or assumed behavior, in the change log.
- Do not use this file to silently change requirements or architecture.
