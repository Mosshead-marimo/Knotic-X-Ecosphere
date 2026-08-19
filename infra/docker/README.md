# Docker

Dockerfiles, database initialization, health checks, and container security configuration live here. From the repository root, this one command builds and starts the complete local stack:

```console
docker compose up -d --build --wait --wait-timeout 180
```

Verify it with `docker compose ps`, `curl http://127.0.0.1:8080/api/v1/health/ready`, and `curl http://127.0.0.1:3000/api/health`. Stop it with `docker compose down`; add `--volumes` only when intentionally deleting local PostgreSQL and Redis data.

Compose waits on dependency health rather than fixed sleeps. PostgreSQL and Redis are isolated on the internal data network; MCP is not published to the host. Application images run as UID/GID 10001 with read-only root filesystems, a writable `/tmp` tmpfs, `no-new-privileges`, bounded logs, and resource limits. Base and data-service images are pinned by version and immutable digest.

The fixed sentinel MCP/Agora values and trust-authenticated database are public local-development values, not production credentials. This Compose file is not a production deployment profile. Production must use managed secrets, TLS, authenticated managed data services, private ingress, externally backed telemetry, backups, and the platform profile required by `ARCHITECTURE_DECISIONS.md`.
