# MCP Service

MCP boundary for authenticated, validated, and audited knowledge retrieval and external business actions.

The reserved Python namespace is `knotic_mcp`; the process entry point is `knotic_mcp.__main__:main`. The current entry point deliberately exits until supported Python and MCP dependencies are established by `P0-T002`.

No integration may fabricate transactional success or allow arbitrary SQL. Follow `../docs/AGENTS.md` and `../docs/COMPONENT_OWNERSHIP.md`.

