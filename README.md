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

## Current bootstrap state

`P0-T001` establishes structure only. Dependencies, runtime pins, lockfiles, executable Flask/Next.js/MCP services, health checks, and Docker topology are intentionally deferred to their numbered Phase 0 tasks. Do not treat this scaffold as a runnable or production-ready release.

The next task is `P0-T002`, which provisions supported runtimes and reproducible dependencies with npm and uv.

