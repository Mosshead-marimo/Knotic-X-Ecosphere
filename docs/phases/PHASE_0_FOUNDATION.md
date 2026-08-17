# Phase 0 — Production Foundation and Contracts

## Objective

Create a reproducible, secure foundation and remove contract ambiguity before business implementation begins.

## Entry criteria

- `AGENTS.md`, `REQUIREMENTS.md`, and `System_Design.md` are reviewed.
- Mandatory stack choices are accepted.

## Tasks

### [x] P0-T001 — Establish repository structure and ownership

- Dependencies: None.
- Implement: Next.js/TypeScript frontend, Flask/Python backend, MCP service, infrastructure, migrations, tests, and documentation directories; add scoped `AGENTS.md` files only where additional rules are required.
- Acceptance: Each component has a clear entry point, owner, and dependency boundary; no generated secrets or build output are committed.
- Verify: Repository tree review and clean bootstrap on a fresh checkout.

### [x] P0-T002 — Pin supported runtimes and dependencies

- Dependencies: P0-T001.
- Implement: Pin Node, Python, package-manager, database, Redis, and container versions; commit lockfiles; configure automated dependency updates and vulnerability reporting.
- Acceptance: Repeated clean installs resolve identical versions; unsupported runtime versions fail with a useful message.
- Verify: Clean frontend/backend install, lockfile consistency, dependency audit.

### [x] P0-T003 — Create production configuration and secret model

- Dependencies: P0-T001.
- Implement: Typed configuration loading, environment separation, `.env.example`, startup validation, secret-provider interface, key rotation guidance, and log redaction.
- Acceptance: Missing or malformed required configuration fails fast; server credentials never enter frontend bundles or logs.
- Verify: Configuration unit tests and production bundle secret scan.

### [ ] P0-T004 — Define architecture decisions

- Dependencies: P0-T001.
- Implement: `docs/ARCHITECTURE_DECISIONS.md` covering service boundaries, deployment topology, identity, model/provider choices, realtime voice path, failure strategy, and rejected alternatives.
- Acceptance: Every consequential decision records context, choice, consequences, status, and owner.
- Verify: Cross-check against mandatory stack and `System_Design.md`.

### [ ] P0-T005 — Define API contracts

- Dependencies: P0-T004.
- Implement: `docs/API_CONTRACTS.md` with versioned endpoints/events, authentication, request/response schemas, error envelope, idempotency, pagination, rate limits, and compatibility policy.
- Acceptance: Frontend and backend can be independently implemented from the contract; no ambiguous success or error states remain.
- Verify: OpenAPI/schema validation and contract examples.

### [ ] P0-T006 — Define durable and active data models

- Dependencies: P0-T004.
- Implement: `docs/DATA_MODEL.md` covering PostgreSQL entities, Redis keys/TTL, pgvector records, identifiers, relationships, invariants, retention, deletion, encryption, and migration strategy.
- Acceptance: Every required state field and audit event has one authoritative storage location and lifecycle.
- Verify: Trace FR-04, FR-05, FR-11–FR-14 to the model.

### [ ] P0-T007 — Define MCP contracts and policies

- Dependencies: P0-T004, P0-T005.
- Implement: `docs/MCP_TOOLS.md` with versioned tool schemas, authentication, authorization, approval levels, idempotency, timeouts, retries, audit fields, and failure semantics.
- Acceptance: Every tool in `System_Design.md` has validated inputs, outputs, side effects, and safe failure behavior.
- Verify: Schema validation and tool/requirement traceability review.

### [ ] P0-T008 — Create local and CI service topology

- Dependencies: P0-T002, P0-T003.
- Implement: Dockerfiles and Compose services for frontend, backend, MCP, PostgreSQL/pgvector, and Redis; health checks, non-root containers, persistent volumes, and resource limits.
- Acceptance: One documented command starts a healthy stack; service startup order does not rely on fixed sleeps.
- Verify: Build from no cache, health checks, restart test, container security scan.

### [ ] P0-T009 — Establish quality gates

- Dependencies: P0-T002.
- Implement: Formatting, linting, strict TypeScript, Python type checking, unit tests, contract tests, secret scanning, dependency scanning, and CI branch gates.
- Acceptance: CI blocks merge on any required check; local commands match CI behavior.
- Verify: Passing pipeline plus deliberate failing-change test.

### [ ] P0-T010 — Create operational documentation baseline

- Dependencies: P0-T003, P0-T008, P0-T009.
- Implement: Developer setup, test, migration, configuration, incident-contact, release, and rollback instructions.
- Acceptance: A new engineer can bootstrap and validate the stack without undocumented steps.
- Verify: Fresh-environment walkthrough by someone other than the author.

## Phase gate

All tasks are complete; contracts are reviewed; the stack builds reproducibly; CI enforces quality and security gates; a fresh checkout reaches healthy state using documented commands.
