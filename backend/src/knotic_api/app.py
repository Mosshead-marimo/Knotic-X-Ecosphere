"""Flask application factory and dependency-aware health checks."""

from __future__ import annotations

import hmac
from urllib.request import urlopen

import psycopg
import redis
from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue

from .config import BackendSettings, load_backend_settings
from .lifecycle_api import LifecycleDependencies, build_lifecycle_dependencies, register_lifecycle_api
from .observability import StateDataObservability
from .privacy_logging import install_sensitive_data_filter


def create_app(
    settings: BackendSettings | None = None,
    *,
    lifecycle_dependencies: LifecycleDependencies | None = None,
    observability: StateDataObservability | None = None,
) -> Flask:
    resolved = settings or load_backend_settings()
    app = Flask(__name__)
    install_sensitive_data_filter(app.logger)
    app.config["KNOTIC_SETTINGS"] = resolved
    telemetry = observability or StateDataObservability(
        service_name=resolved.service_name,
        environment=resolved.environment.value,
        otlp_endpoint=resolved.otel_exporter_otlp_endpoint,
    )
    app.extensions["knotic_observability"] = telemetry
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

    @app.get("/internal/metrics")
    def metrics() -> ResponseReturnValue:
        configured = resolved.metrics_auth_token
        if configured is None:
            return jsonify(error="not_found"), 404
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not supplied or not hmac.compare_digest(supplied, configured.get_secret_value()):
            return jsonify(error="unauthorized"), 401
        telemetry.sample_pool(dependencies.engine)
        payload, content_type = telemetry.render()
        return Response(payload, content_type=content_type)

    return app
