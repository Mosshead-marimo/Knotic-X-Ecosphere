# Backend

Python service boundary for Flask APIs, session management, Agora token generation, service composition, and LangGraph orchestration.

The Python namespace is `knotic_api`; the process entry point is `knotic_api.__main__:main`. Python, uv, Flask, LangGraph, state/database clients, and the MCP client are locked at the workspace root. Startup validates required configuration before serving the Flask application.

Persistence code lives under `knotic_api.persistence`. PostgreSQL is durable authority and is changed only through Alembic. Redis holds a rebuildable, size-bounded `SalesState` projection with environment/tenant/session key isolation, a sliding TTL, schema and event watermarks, atomic optimistic updates, and lease fencing. Redis misses or corrupt values require hydration from PostgreSQL; Redis outages fail explicitly and never imply a successful write.

All durable application writes run inside `UnitOfWork`, which opens one short PostgreSQL transaction and sets trusted tenant/actor context before exposing typed repositories. Repositories repeat tenant predicates even with forced RLS. External calls are forbidden inside this boundary; reserve idempotency, write business state plus events/outbox work, commit, and only then invoke providers.

Settings are defined in `knotic_api.config`; shared provider and redaction primitives live in `packages/config`. See `../docs/CONFIGURATION.md` and the root `.env.example`.

External business integrations must be accessed through the MCP boundary. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.
