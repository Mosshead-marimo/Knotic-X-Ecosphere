"""Minimal private ASGI gateway shell with dependency health checks."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import redis

from .config import McpSettings, load_mcp_settings

AsgiSend = Callable[[dict[str, Any]], Awaitable[None]]


def create_app(settings: McpSettings | None = None):
    resolved = settings or load_mcp_settings()

    async def app(scope: dict[str, Any], receive: Any, send: AsgiSend) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path")
        status = 200
        payload: dict[str, Any] = {"status": "ok"}
        if path == "/health/ready":
            checks: dict[str, str] = {}
            try:
                with psycopg.connect(resolved.database_url.get_secret_value(), connect_timeout=2) as connection:
                    connection.execute("select 1")
                checks["postgres"] = "ok"
                redis.Redis.from_url(resolved.redis_url.get_secret_value(), socket_connect_timeout=2).ping()
                checks["redis"] = "ok"
                payload["checks"] = checks
            except Exception:
                status = 503
                payload = {"status": "not_ready", "checks": checks}
        elif path != "/health/live":
            status = 404
            payload = {"status": "not_found"}
        body = json.dumps(payload, separators=(",", ":")).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    return app
