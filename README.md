# Knotic Sales Agent

Production-oriented monorepo for an adaptive realtime voice AI sales agent.

## Architecture

- `frontend`: React/Next.js browser application and Agora Web SDK boundary.
- `backend`: Python/Flask APIs, sessions, Agora token service, service composition, and LangGraph workflow.
- `mcp`: MCP gateway and tools for grounded knowledge and external business actions.
- `infra`: container, deployment, and observability definitions.
- `tests`: cross-service contract, end-to-end, performance, and security tests.
- `docs`: requirements, system design, ownership, implementation tasks, and change history.

Redis owns active conversational state, PostgreSQL owns durable records, and pgvector owns semantic retrieval. See `docs/System_Design.md` and `docs/COMPONENT_OWNERSHIP.md` for the complete boundaries.

## Task workflow

Read `docs/AGENTS.md` before making changes. Executable work orders are indexed in `docs/phases/README.md`; record verified changes in `docs/CHANGES_MADE.md`.

## Runtime and dependency baseline

`P0-T002` pins Node.js, npm, Python, uv, application dependencies, database/cache versions, and future container bases. Install from the committed lockfiles:

```text
npm ci
uv sync --locked --all-packages --all-groups
```

The frontend, Flask API, MCP health shell, PostgreSQL/pgvector, and Redis run together locally with dependency-aware health checks:

```text
docker compose up -d --build --wait --wait-timeout 180
```

See `docs/DEPENDENCY_POLICY.md` for runtime pins, audits, and update rules, `docs/QUALITY_GATES.md` for local/CI enforcement, `docs/ARCHITECTURE_DECISIONS.md` for the binding production architecture, `docs/API_CONTRACTS.md` for versioned APIs, `docs/DATA_MODEL.md` for data ownership, and `docs/MCP_TOOLS.md` for governed tool contracts. P0-T009 and P0-T010 implementation is present; their external completion gates are recorded in the Phase 0 task file.

Configuration and secret-handling behavior is documented in `docs/CONFIGURATION.md`. The governed tenant MCP registration and activation procedure is documented in `docs/MCP_INTEGRATION_GUIDE.md`. `.env.example` is a placeholder-only local template; services never load it implicitly.

Developer bootstrap, release, rollback, and incident procedures are in `docs/DEVELOPMENT.md`, `docs/RELEASE.md`, `docs/ROLLBACK.md`, and `docs/INCIDENT_RESPONSE.md`.

Agora onboarding, managed-provider prototyping, BYOK considerations, and the most relevant official Voice AI recipes are collected in `docs/AGORA_VOICE_AI.md`.
