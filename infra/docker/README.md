# Docker

Dockerfiles, database initialization, health checks, and container security configuration live here. From the repository root, `docker compose up -d --build --wait` starts the local stack; `docker compose down` stops it without deleting durable volumes.

The fixed sentinel MCP/Agora values and trust-authenticated database are public local-development values, not production credentials. Production must use managed secrets, TLS, authenticated data services, immutable image digests, and the platform profile required by `ARCHITECTURE_DECISIONS.md`.
