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

The frontend scaffold builds with the pinned dependencies. Flask and MCP dependencies are installed, but their process entry points deliberately remain non-operational until later Phase 0 tasks establish configuration and service bootstrapping. Health checks and Docker topology are also deferred to their numbered tasks.

See `docs/DEPENDENCY_POLICY.md` for runtime pins, audits, and update rules, `docs/ARCHITECTURE_DECISIONS.md` for the binding production architecture, and `docs/API_CONTRACTS.md` for versioned browser and internal voice APIs. The next task is `P0-T006`, which defines durable and active data models.

Configuration and secret-handling behavior is documented in `docs/CONFIGURATION.md`. `.env.example` is a placeholder-only local template; services never load it implicitly.
