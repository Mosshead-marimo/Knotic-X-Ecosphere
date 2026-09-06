"""Development-only browser-session bootstrap.

This codebase has no production identity provider wired in yet -- the call page's own comment
and Phase 7's docs both flag the browser-session/CSRF bootstrap (login) flow as a pre-existing
gap outside their scope. Every other authenticated endpoint (`lifecycle_api`, `voice_api`)
already correctly *requires* a `knotic_session` cookie; nothing in the codebase issues one.

This module fills that gap ONLY for local development so the already-implemented
session/Agora/CRM stack can be exercised end-to-end without a real identity provider: it mints
a browser-session cookie for a single, fixed local "demo" tenant (idempotently ensuring the
matching `tenants` row exists -- `sales_sessions.tenant_id` has a foreign key to it) and returns
the matching CSRF token. `create_app` wires this in only when the resolved environment is
DEVELOPMENT or TEST; never staging or production. Replace this with real sign-in before any
managed deployment.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from sqlalchemy.engine import Engine

from knotic_api.security import AuthenticatedActor, RedisBrowserSessionStore, SecurityDependencyUnavailable

_COOKIE_NAME = "knotic_session"
_SESSION_TTL = timedelta(hours=8)

# Fixed identifiers for the single shared local-development demo tenant/actor, so repeated
# bootstrap calls (one per browser tab) reuse the same tenant row instead of accumulating one
# per call. Not secrets -- these are only ever used against a local development database.
DEMO_TENANT_ID = UUID("018f2f9a-0000-7000-8000-000000000001")
DEMO_ACTOR_ID = UUID("018f2f9a-0000-7000-8000-000000000002")


@dataclass(frozen=True, slots=True)
class DevAuthDependencies:
    engine: Engine
    browser_sessions: RedisBrowserSessionStore
    allowed_origins: frozenset[str]
    cookie_secure: bool


class DevAuthApi:
    def __init__(self, app: Flask, dependencies: DevAuthDependencies) -> None:
        self.app = app
        self.dependencies = dependencies

    def register(self) -> None:
        self.app.add_url_rule("/api/v1/auth/dev-session", view_func=self.create_dev_session, methods=["POST"])

    def create_dev_session(self) -> ResponseReturnValue:
        origin = request.headers.get("Origin")
        if origin is None or origin not in self.dependencies.allowed_origins:
            response = jsonify(error={"code": "ORIGIN_DENIED", "message": "Request origin is not allowed."})
            response.status_code = 403
            return response

        try:
            self._ensure_demo_tenant()
        except Exception:
            self.app.logger.exception("dev-session bootstrap could not ensure the demo tenant")
            response = jsonify(
                error={"code": "DEPENDENCY_UNAVAILABLE", "message": "A required dependency is unavailable."}
            )
            response.status_code = 503
            return response

        cookie = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        actor = AuthenticatedActor(
            tenant_id=DEMO_TENANT_ID,
            actor_id=DEMO_ACTOR_ID,
            actor_type="HUMAN_AGENT",
            roles=("ADMIN",),
            display_name="Local operator",
        )
        expires_at = datetime.now(UTC) + _SESSION_TTL

        try:
            self.dependencies.browser_sessions.put(
                cookie=cookie, csrf_token=csrf_token, actor=actor, expires_at=expires_at
            )
        except SecurityDependencyUnavailable:
            response = jsonify(
                error={"code": "DEPENDENCY_UNAVAILABLE", "message": "A required dependency is unavailable."}
            )
            response.status_code = 503
            return response

        response = jsonify(
            authenticated=True,
            csrf_token=csrf_token,
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
            actor={"actor_id": str(DEMO_ACTOR_ID), "display_name": "Local operator", "roles": ["ADMIN"]},
            tenant={"tenant_id": str(DEMO_TENANT_ID), "slug": "local-demo"},
        )
        response.set_cookie(
            _COOKIE_NAME,
            cookie,
            max_age=int(_SESSION_TTL.total_seconds()),
            httponly=True,
            secure=self.dependencies.cookie_secure,
            samesite="Lax",
            path="/",
        )
        return response

    def _ensure_demo_tenant(self) -> None:
        with self.dependencies.engine.begin() as connection:
            connection.execute(
                sa.text(
                    "insert into tenants (id, slug, status) values (:id, 'local-demo', 'ACTIVE') "
                    "on conflict (id) do nothing"
                ),
                {"id": DEMO_TENANT_ID},
            )
            connection.execute(
                sa.text(
                    "insert into actors (id,tenant_id,actor_type,status) values (:id,:tenant_id,'USER','ACTIVE') "
                    "on conflict (id) do nothing"
                ),
                {"id": DEMO_ACTOR_ID, "tenant_id": DEMO_TENANT_ID},
            )
            connection.execute(
                sa.text(
                    "insert into operator_role_assignments (id,tenant_id,actor_id,role) "
                    "values (:id,:tenant_id,:actor_id,'ADMIN') on conflict (tenant_id,actor_id,role) do nothing"
                ),
                {"id": DEMO_ACTOR_ID, "tenant_id": DEMO_TENANT_ID, "actor_id": DEMO_ACTOR_ID},
            )


def register_dev_auth_api(app: Flask, dependencies: DevAuthDependencies) -> None:
    DevAuthApi(app, dependencies).register()
