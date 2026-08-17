# Backend

Python service boundary for Flask APIs, session management, Agora token generation, service composition, and LangGraph orchestration.

The Python namespace is `knotic_api`; the process entry point is `knotic_api.__main__:main`. Python, uv, Flask, LangGraph, state/database clients, and the MCP client are locked at the workspace root. The current entry point deliberately exits until configuration and Flask service bootstrapping are implemented by subsequent Phase 0 tasks.

External business integrations must be accessed through the MCP boundary. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.
