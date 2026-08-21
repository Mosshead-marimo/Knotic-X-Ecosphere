# Database migrations

Alembic owns the durable PostgreSQL and pgvector schema. Migrations are immutable
after release and run only through the dedicated migration workflow; API startup
never creates or changes schema.

From the repository root:

```console
$env:KNOTIC_DATABASE_URL = "postgresql+psycopg://..."
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend alembic -c backend/alembic.ini downgrade base
```

The initial revision imports the frozen `schema_v1` metadata. Future revisions
must use explicit Alembic operations and must not edit `schema_v1`.

For host-run integration tests, opt in to the loopback-only database port:

```console
docker compose -f compose.yaml -f infra/docker/compose.integration.yaml up -d postgres
$env:KNOTIC_TEST_DATABASE_URL = "postgresql+psycopg://knotic@127.0.0.1:5433/knotic"
$env:KNOTIC_TEST_REDIS_URL = "redis://127.0.0.1:6380/15"
uv run pytest -m integration backend/tests/test_postgres_schema.py backend/tests/test_active_state_repository.py
```
