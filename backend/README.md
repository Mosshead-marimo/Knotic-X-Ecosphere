# Backend

Python service boundary for Flask APIs, session management, Agora token generation, service composition, and LangGraph orchestration.

The Python namespace is `knotic_api`; the process entry point is `knotic_api.__main__:main`. Python, uv, Flask, LangGraph, state/database clients, and the MCP client are locked at the workspace root. The entry point validates all required configuration and secrets before deliberately exiting until Flask service bootstrapping is implemented by subsequent Phase 0 tasks.

Settings are defined in `knotic_api.config`; shared provider and redaction primitives live in `packages/config`. See `../docs/CONFIGURATION.md` and the root `.env.example`.

External business integrations must be accessed through the MCP boundary. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.
