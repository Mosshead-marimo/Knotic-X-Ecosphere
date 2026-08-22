# Backend

Python service boundary for Flask APIs, session management, Agora token generation, service composition, and LangGraph orchestration.

The Python namespace is `knotic_api`; the process entry point is `knotic_api.__main__:main`. Python, uv, Flask, LangGraph, state/database clients, and the MCP client are locked at the workspace root. Startup validates required configuration before serving the Flask application.

Persistence code lives under `knotic_api.persistence`. PostgreSQL is durable authority and is changed only through Alembic. Redis holds a rebuildable, size-bounded `SalesState` projection with environment/tenant/session key isolation, a sliding TTL, schema and event watermarks, atomic optimistic updates, and lease fencing. Redis misses or corrupt values require hydration from PostgreSQL; Redis outages fail explicitly and never imply a successful write.

All durable application writes run inside `UnitOfWork`, which opens one short PostgreSQL transaction and sets trusted tenant/actor context before exposing typed repositories. Repositories repeat tenant predicates even with forced RLS. External calls are forbidden inside this boundary; reserve idempotency, write business state plus events/outbox work, commit, and only then invoke providers.

Structured memory updates are pure domain operations in `knotic_api.domain.memory`. Current facts retain confirmation, confidence, actor, source-turn, capture-time, and version provenance. Confirmed values cannot be displaced by tentative extraction, same-turn conflicts fail closed, and accepted facts are projected into typed customer, requirement, competitor, topic, next-action, and objection state rather than relying on raw transcript text.

Confirmed requirement revisions are serialized by the owning session row. Replacing `requirements_current`, appending immutable old/new history, and emitting the ordered `requirement.updated` event occur in the same `UnitOfWork` transaction. Source-turn replays return the original event without adding history; conflicting replays and stale concurrent versions fail closed. Revision and event rows retain actor, source, causation, and correlation metadata.

`SalesStateHydrator` is the Redis-first state loading boundary. A miss or corrupt value is reconstructed under a tenant-scoped durable lock from session, lead, requirement/history, objection, competitor, qualification, outcome, and event-watermark rows; encrypted fields are authenticated with purpose-separated AES-256-GCM before entering state. Cache warming is create-only and race checked. Checkpoints use durable optimistic versions and cannot advance past immutable event history. Legacy pre-watermark cache envelopes are atomically migrated in place, and recovery never appends events.

Privacy operations live in `knotic_api.privacy` and follow `docs/DATA_LIFECYCLE.md`. Exports, erasure requests, provider-confirmed deletion, legal holds, bounded retention, and housekeeping are tenant scoped and auditable. Erasure requests install a Redis privacy tombstone that active-state hydration treats as a hard processing block. Operational application logs install `SensitiveDataFilter`. Database migration `20260822_0005` defines non-login runtime, retention, and auditor roles without RLS bypass.

The `/api/v1/sessions` create/read/end lifecycle is registered by the Flask factory. Browser access requires an opaque `knotic_session` backed by Redis; mutations additionally require an allowed exact origin, the session-bound CSRF token, and an idempotency key. Rate limits fail closed. Idempotent response bodies are encrypted with AES-256-GCM, and request/correlation identifiers appear in every API response. OIDC routes minting browser sessions are a separate contract implementation; until those routes exist, no production client can self-issue an authenticated cookie.

Settings are defined in `knotic_api.config`; shared provider and redaction primitives live in `packages/config`. See `../docs/CONFIGURATION.md` and the root `.env.example`.

External business integrations must be accessed through the MCP boundary. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.
