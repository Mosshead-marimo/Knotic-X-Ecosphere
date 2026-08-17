# MCP Service

MCP boundary for authenticated, validated, and audited knowledge retrieval and external business actions.

The Python namespace is `knotic_mcp`; the process entry point is `knotic_mcp.__main__:main`. Python, uv, MCP v2, validation, and ASGI server dependencies are locked at the workspace root. The current entry point deliberately exits until configuration, authentication, policy, and server bootstrapping are implemented by subsequent tasks.

No integration may fabricate transactional success or allow arbitrary SQL. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.
