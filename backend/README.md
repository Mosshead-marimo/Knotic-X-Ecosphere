# Backend

Python service boundary for Flask APIs, session management, Agora token generation, service composition, and LangGraph orchestration.

The reserved Python namespace is `knotic_api`; the process entry point is `knotic_api.__main__:main`. The current entry point deliberately exits until supported Python and Flask dependencies are established by `P0-T002`.

External business integrations must be accessed through the MCP boundary. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.

