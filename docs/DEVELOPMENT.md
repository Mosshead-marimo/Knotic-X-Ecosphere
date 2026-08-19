# Developer Setup and Validation

## Prerequisites

- Git
- Docker Desktop or Docker Engine with Compose v2
- Node.js 24.19.0 and npm 11.17.0
- Python 3.13.14 and uv 0.12.2

Use only the versions declared in `.node-version`, `.python-version`, `uv.toml`, and `package.json`. Do not substitute the system Python on machines where it is older.

## Fresh checkout

```console
git clone https://github.com/Mosshead-marimo/Knotic-X-Ecosphere.git knotic-sales-agent
cd knotic-sales-agent
git switch dev
npm ci
uv sync --locked --all-packages --all-groups
npm run quality
docker compose up -d --build --wait --wait-timeout 180
```

Confirm `docker compose ps` reports all five services healthy, then request:

```console
curl --fail http://127.0.0.1:8080/api/v1/health/ready
curl --fail http://127.0.0.1:3000/api/health
```

Stop services without deleting data with `docker compose down`. Use `docker compose down --volumes` only when intentionally resetting all local PostgreSQL and Redis state.

## Configuration

Compose uses explicitly public development sentinels and a trust-authenticated isolated database. Do not reuse it as a deployment definition. For native service development, copy `.env.example` to an ignored file, replace every placeholder, and pass that path explicitly to the application composition. See `CONFIGURATION.md`; environment files are never loaded implicitly.

## Tests and contracts

`npm run quality` is the authoritative local command and must pass before push. Targeted commands are documented in `QUALITY_GATES.md`. Use `docker compose build --no-cache` after dependency, base-image, or Dockerfile changes.

## Database changes

The current foundation initializes only the `vector` extension; no application schema migration exists yet. A feature that introduces the first schema must add the migration tool and documented upgrade command in the same task. Migrations must follow the expand/migrate/contract policy in `DATA_MODEL.md`, be forward-compatible with the previous application release, and have a tested recovery plan. Never edit a migration that has reached a shared environment.

## Troubleshooting

- Runtime verification failure: install the exact versions above and rerun the locked install.
- Unhealthy service: run `docker compose ps` and `docker compose logs --tail=200 <service>`; do not add startup sleeps.
- Port 3000 or 8080 in use: stop the conflicting local process; do not publish data-service ports.
- Stale local state: preserve evidence first, then intentionally reset volumes if the data is disposable.
- Lock mismatch: update the owning manifest and regenerate the lock with the pinned package manager; never hand-edit resolved package entries.
