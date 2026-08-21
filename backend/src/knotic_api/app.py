"""Flask application factory and dependency-aware health checks."""

from __future__ import annotations

from urllib.request import urlopen

import psycopg
import redis
from flask import Flask, jsonify
from flask.typing import ResponseReturnValue

from .config import BackendSettings, load_backend_settings
from .lifecycle_api import LifecycleDependencies, build_lifecycle_dependencies, register_lifecycle_api


def create_app(
    settings: BackendSettings | None = None,
    *,
    lifecycle_dependencies: LifecycleDependencies | None = None,
) -> Flask:
    resolved = settings or load_backend_settings()
    app = Flask(__name__)
    app.config["KNOTIC_SETTINGS"] = resolved
    dependencies = lifecycle_dependencies or build_lifecycle_dependencies(
        database_url=resolved.database_url.get_secret_value(),
        redis_url=resolved.redis_url.get_secret_value(),
        environment=resolved.environment.value,
        security_key=resolved.session_security_key.get_secret_value().encode(),
        allowed_origins=resolved.allowed_origins,
    )
    register_lifecycle_api(app, dependencies)

    @app.get("/api/v1/health/live")
    def live() -> ResponseReturnValue:
        return jsonify(status="ok")

    @app.get("/api/v1/health/ready")
    def ready() -> ResponseReturnValue:
        checks: dict[str, str] = {}
        try:
            with psycopg.connect(resolved.database_url.get_secret_value(), connect_timeout=2) as connection:
                connection.execute("select 1")
            checks["postgres"] = "ok"
            redis.Redis.from_url(resolved.redis_url.get_secret_value(), socket_connect_timeout=2).ping()
            checks["redis"] = "ok"
            with urlopen(f"{resolved.mcp_base_url}/health/ready", timeout=2) as response:  # noqa: S310 - configured/validated service URL
                if response.status != 200:
                    raise RuntimeError("MCP is not ready")
            checks["mcp"] = "ok"
        except Exception:
            return jsonify(status="not_ready", checks=checks), 503
        return jsonify(status="ok", checks=checks)

    return app
