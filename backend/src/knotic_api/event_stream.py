"""Durable domain-event relay and bounded tenant-scoped SSE delivery."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis
import sqlalchemy as sa
from flask import Flask, Response, jsonify, request, stream_with_context
from flask.typing import ResponseReturnValue
from sqlalchemy.engine import Engine

from knotic_api.security import RedisBrowserSessionStore, SecurityDependencyUnavailable

_STREAM_ID = re.compile(r"^(?:0|[1-9][0-9]*)-(?:0|[1-9][0-9]*)$")
_OPERATOR_ROLES = frozenset({"ADMIN", "SUPERVISOR", "SALES_REP"})


@dataclass(frozen=True, slots=True)
class EventStreamDependencies:
    engine: Engine
    redis_client: redis.Redis
    browser_sessions: RedisBrowserSessionStore
    environment: str


class DomainEventRelay:
    """Copies sanitized durable notifications to Redis Streams with a per-tenant watermark."""

    def __init__(self, dependencies: EventStreamDependencies) -> None:
        self.dependencies = dependencies

    def relay_once(self, *, batch_size: int = 500) -> int:
        published = 0
        with self.dependencies.engine.connect() as connection:
            tenants = list(connection.scalars(sa.text("select id from tenants where status='ACTIVE' order by id")))
        for tenant_id in tenants:
            watermark_key = self._watermark_key(tenant_id)
            raw = self.dependencies.redis_client.get(watermark_key)
            watermark = (
                raw.decode()
                if isinstance(raw, bytes)
                else "1970-01-01T00:00:00+00:00|00000000-0000-0000-0000-000000000000"
            )
            at_text, id_text = watermark.split("|", 1)
            with self.dependencies.engine.begin() as connection:
                connection.execute(
                    sa.text("select set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant_id)}
                )
                rows = list(
                    connection.execute(
                        sa.text(
                            "select id,session_id,event_type,occurred_at from domain_events where tenant_id=:tenant_id "
                            "and (occurred_at,id) > (:at,:id) order by occurred_at,id limit :limit"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "at": datetime.fromisoformat(at_text),
                            "id": UUID(id_text),
                            "limit": batch_size,
                        },
                    ).mappings()
                )
            for row in rows:
                payload = json.dumps(
                    {
                        "session_id": str(row["session_id"]),
                        "event_type": row["event_type"],
                        "occurred_at": row["occurred_at"].astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    },
                    separators=(",", ":"),
                )
                self.dependencies.redis_client.xadd(
                    self._stream_key(tenant_id),
                    {"event": b"session.changed", "data": payload.encode()},
                    maxlen=10_000,
                    approximate=True,
                )
                self.dependencies.redis_client.set(
                    watermark_key, f"{row['occurred_at'].isoformat()}|{row['id']}".encode()
                )
                published += 1
        return published

    def run_forever(self, *, interval_seconds: float = 0.5) -> None:
        while True:
            try:
                self.relay_once()
            except Exception:
                # Supervisors restart unhealthy processes; never skip the durable watermark.
                time.sleep(min(5.0, interval_seconds * 4))
            else:
                time.sleep(interval_seconds)

    def _stream_key(self, tenant_id: UUID) -> str:
        return f"knotic:{self.dependencies.environment}:console-events:{tenant_id}"

    def _watermark_key(self, tenant_id: UUID) -> str:
        return f"knotic:{self.dependencies.environment}:console-relay-watermark:{tenant_id}"


class EventStreamApi:
    def __init__(self, app: Flask, dependencies: EventStreamDependencies) -> None:
        self.app, self.dependencies = app, dependencies

    def register(self) -> None:
        self.app.add_url_rule(
            "/api/v1/console/stream", endpoint="console_stream", view_func=self.stream, methods=["GET"]
        )

    def stream(self) -> ResponseReturnValue:
        try:
            authenticated = self.dependencies.browser_sessions.authenticate(request.cookies.get("knotic_session"))
        except SecurityDependencyUnavailable:
            return jsonify(error={"code": "DEPENDENCY_UNAVAILABLE", "message": "Live updates are unavailable."}), 503
        if authenticated is None:
            return jsonify(error={"code": "AUTHENTICATION_REQUIRED", "message": "Authentication is required."}), 401
        actor, _ = authenticated
        if not _OPERATOR_ROLES.intersection(actor.roles):
            return jsonify(error={"code": "PERMISSION_DENIED", "message": "Operator access is required."}), 403
        cursor = request.headers.get("Last-Event-ID", "$")
        if cursor != "$" and not _STREAM_ID.fullmatch(cursor):
            return jsonify(error={"code": "INVALID_CURSOR", "message": "Last-Event-ID is invalid."}), 422
        stream_key = f"knotic:{self.dependencies.environment}:console-events:{actor.tenant_id}"

        def generate() -> Iterator[str]:
            yield 'event: ready\ndata: {"status":"connected"}\n\n'
            current, deadline = cursor, time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    result: Any = self.dependencies.redis_client.xread({stream_key: current}, count=100, block=10_000)
                except redis.RedisError:
                    yield 'event: degraded\ndata: {"reason":"redis_unavailable"}\n\n'
                    return
                if not result:
                    yield ": keepalive\n\n"
                    continue
                for _, entries in result:
                    for event_id, fields in entries:
                        current = event_id.decode()
                        event = fields.get(b"event", b"session.changed").decode()
                        data = fields.get(b"data", b"{}").decode()
                        yield f"id: {current}\nevent: {event}\ndata: {data}\n\n"

        response = Response(stream_with_context(generate()), content_type="text/event-stream")
        response.headers.update(
            {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
        )
        return response


def register_event_stream_api(app: Flask, dependencies: EventStreamDependencies) -> None:
    EventStreamApi(app, dependencies).register()
