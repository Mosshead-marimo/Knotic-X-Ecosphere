"""Provider-neutral OIDC browser authentication with PKCE and server-side sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import jwt
import redis
import sqlalchemy as sa
from flask import Flask, jsonify, redirect, request
from flask.typing import ResponseReturnValue
from jwt import PyJWKClient
from sqlalchemy.engine import Engine

from knotic_api.config import BackendSettings
from knotic_api.domain.identifiers import new_uuid7
from knotic_api.security import AuthenticatedActor, RedisBrowserSessionStore, SecurityDependencyUnavailable, derive_key
from knotic_config import RuntimeEnvironment

_COOKIE_NAME = "knotic_session"
_FLOW_TTL_SECONDS = 600
_SESSION_TTL = timedelta(hours=8)
_TENANT_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ALLOWED_ROLES = frozenset({"ADMIN", "SUPERVISOR", "SALES_REP", "CUSTOMER"})


@dataclass(frozen=True, slots=True)
class AuthDependencies:
    settings: BackendSettings
    engine: Engine
    redis_client: redis.Redis
    browser_sessions: RedisBrowserSessionStore


class OidcApi:
    def __init__(self, app: Flask, dependencies: AuthDependencies) -> None:
        self.app = app
        self.dependencies = dependencies
        self._identity_hmac_key = derive_key(
            dependencies.settings.session_security_key.get_secret_value().encode(), b"oidc-subject"
        )

    def register(self) -> None:
        self.app.add_url_rule("/api/v1/auth/login", view_func=self.login, methods=["GET"])
        self.app.add_url_rule("/api/v1/auth/callback", view_func=self.callback, methods=["GET"])
        self.app.add_url_rule("/api/v1/auth/session", view_func=self.session, methods=["GET"])
        self.app.add_url_rule("/api/v1/auth/logout", view_func=self.logout, methods=["POST"])

    def login(self) -> ResponseReturnValue:
        if not self._configured():
            return self._problem(503, "OIDC_NOT_CONFIGURED", "Identity provider sign-in is not configured.")
        return_to = request.args.get("return_to", "/console/live")
        if not return_to.startswith("/") or return_to.startswith("//"):
            return_to = "/console/live"
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        flow = json.dumps({"nonce": nonce, "verifier": verifier, "return_to": return_to})
        try:
            self.dependencies.redis_client.set(self._flow_key(state), flow.encode(), ex=_FLOW_TTL_SECONDS, nx=True)
            discovery = self._discovery()
        except Exception:
            self.app.logger.exception("OIDC login initialization failed")
            return self._problem(503, "IDENTITY_UNAVAILABLE", "Identity provider is unavailable.")
        query = urlencode(
            {
                "client_id": self.dependencies.settings.oidc_client_id,
                "redirect_uri": self.dependencies.settings.oidc_redirect_uri,
                "response_type": "code",
                "scope": "openid profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return redirect(f"{discovery['authorization_endpoint']}?{query}", code=302)

    def callback(self) -> ResponseReturnValue:
        state, code = request.args.get("state"), request.args.get("code")
        if not state or not code or request.args.get("error"):
            return self._problem(400, "OIDC_CALLBACK_INVALID", "Sign-in response was invalid.")
        key = self._flow_key(state)
        try:
            pipeline = self.dependencies.redis_client.pipeline(transaction=True)
            pipeline.get(key)
            pipeline.delete(key)
            raw, _ = pipeline.execute()
        except redis.RedisError:
            return self._problem(503, "IDENTITY_UNAVAILABLE", "Identity session could not be verified.")
        if not isinstance(raw, bytes):
            return self._problem(400, "OIDC_STATE_INVALID", "Sign-in state expired or was already used.")
        flow = json.loads(raw)
        try:
            discovery = self._discovery()
            token = self._exchange_code(discovery, code, str(flow["verifier"]))
            claims = self._validate_id_token(discovery, str(token["id_token"]), str(flow["nonce"]))
            actor, tenant_slug = self._resolve_actor(claims)
        except Exception:
            self.app.logger.exception("OIDC callback validation failed")
            return self._problem(401, "OIDC_TOKEN_INVALID", "Identity token could not be validated.")
        cookie, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + _SESSION_TTL
        try:
            self.dependencies.browser_sessions.put(
                cookie=cookie, csrf_token=csrf_token, actor=actor, expires_at=expires_at
            )
        except SecurityDependencyUnavailable:
            return self._problem(503, "IDENTITY_UNAVAILABLE", "Browser session could not be created.")
        response = redirect(str(flow.get("return_to", "/console/live")), code=303)
        response.set_cookie(
            _COOKIE_NAME,
            cookie,
            max_age=int(_SESSION_TTL.total_seconds()),
            httponly=True,
            secure=self._cookie_secure(),
            samesite="Lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Knotic-Tenant"] = tenant_slug
        return response

    def session(self) -> ResponseReturnValue:
        try:
            authenticated = self.dependencies.browser_sessions.authenticate(request.cookies.get(_COOKIE_NAME))
        except SecurityDependencyUnavailable:
            return self._problem(503, "IDENTITY_UNAVAILABLE", "Browser session could not be verified.")
        if authenticated is None:
            return self._problem(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        actor, value = authenticated
        try:
            csrf_token = self.dependencies.browser_sessions.csrf_token(request.cookies.get(_COOKIE_NAME), value)
        except SecurityDependencyUnavailable:
            return self._problem(503, "IDENTITY_UNAVAILABLE", "Browser session could not be read.")
        response = jsonify(
            authenticated=True,
            actor={"actor_id": str(actor.actor_id), "display_name": actor.display_name, "roles": list(actor.roles)},
            tenant={"tenant_id": str(actor.tenant_id)},
            csrf_token=csrf_token,
            expires_at=value.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def logout(self) -> ResponseReturnValue:
        authenticated = self.dependencies.browser_sessions.authenticate(request.cookies.get(_COOKIE_NAME))
        if authenticated is None:
            response = jsonify(status="signed_out")
        else:
            _, value = authenticated
            submitted = request.headers.get("X-CSRF-Token")
            if not self.dependencies.browser_sessions.verify_csrf(value, submitted):
                return self._problem(403, "CSRF_FAILED", "CSRF validation failed.")
            self.dependencies.browser_sessions.revoke(request.cookies.get(_COOKIE_NAME))
            response = jsonify(status="signed_out")
        response.delete_cookie(_COOKIE_NAME, path="/", secure=self._cookie_secure(), httponly=True, samesite="Lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    def _resolve_actor(self, claims: dict[str, Any]) -> tuple[AuthenticatedActor, str]:
        tenant_claim = claims.get(self.dependencies.settings.oidc_tenant_claim)
        subject = claims.get("sub")
        if not isinstance(tenant_claim, str) or not isinstance(subject, str):
            raise ValueError("required OIDC identity claims are absent")
        tenant_slug = tenant_claim.strip().casefold()
        if not _TENANT_SLUG.fullmatch(tenant_slug):
            raise ValueError("tenant claim is invalid")
        raw_roles = claims.get(self.dependencies.settings.oidc_roles_claim, [])
        if isinstance(raw_roles, str):
            raw_roles = raw_roles.split()
        roles = tuple(sorted({str(role).upper() for role in raw_roles} & _ALLOWED_ROLES))
        if not roles:
            raise ValueError("identity has no supported role")
        display_name = str(claims.get(self.dependencies.settings.oidc_name_claim) or "Operator")[:120]
        subject_hmac = hmac.new(self._identity_hmac_key, subject.encode(), hashlib.sha256).digest()
        issuer = str(claims["iss"])
        tenant_id, actor_id = new_uuid7(), new_uuid7()
        with self.dependencies.engine.begin() as connection:
            tenant = connection.execute(
                sa.text("select id from tenants where slug = :slug and status = 'ACTIVE'"), {"slug": tenant_slug}
            ).scalar_one_or_none()
            if tenant is None:
                connection.execute(
                    sa.text("insert into tenants (id, slug, status) values (:id, :slug, 'ACTIVE')"),
                    {"id": tenant_id, "slug": tenant_slug},
                )
            else:
                tenant_id = tenant
            connection.execute(
                sa.text("select set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
            )
            existing = connection.execute(
                sa.text(
                    "select actor_id from actor_identities where tenant_id=:tenant_id and issuer=:issuer "
                    "and subject_hmac=:subject_hmac"
                ),
                {"tenant_id": tenant_id, "issuer": issuer, "subject_hmac": subject_hmac},
            ).scalar_one_or_none()
            if existing is None:
                connection.execute(
                    sa.text(
                        "insert into actors (id, tenant_id, actor_type, status) values (:id,:tenant_id,'USER','ACTIVE')"
                    ),
                    {"id": actor_id, "tenant_id": tenant_id},
                )
                connection.execute(
                    sa.text(
                        "insert into actor_identities (id,tenant_id,actor_id,issuer,subject_ciphertext,subject_hmac) "
                        "values (:id,:tenant_id,:actor_id,:issuer,:subject,:subject_hmac)"
                    ),
                    {
                        "id": new_uuid7(),
                        "tenant_id": tenant_id,
                        "actor_id": actor_id,
                        "issuer": issuer,
                        "subject": b"redacted",
                        "subject_hmac": subject_hmac,
                    },
                )
            else:
                actor_id = existing
            connection.execute(
                sa.text("delete from operator_role_assignments where tenant_id=:tenant_id and actor_id=:actor_id"),
                {"tenant_id": tenant_id, "actor_id": actor_id},
            )
            for role in roles:
                connection.execute(
                    sa.text(
                        "insert into operator_role_assignments (id,tenant_id,actor_id,role) "
                        "values (:id,:tenant_id,:actor_id,:role)"
                    ),
                    {"id": new_uuid7(), "tenant_id": tenant_id, "actor_id": actor_id, "role": role},
                )
        actor_type = "CUSTOMER" if roles == ("CUSTOMER",) else "HUMAN_AGENT"
        return AuthenticatedActor(tenant_id, actor_id, actor_type, roles, display_name), tenant_slug

    def _validate_id_token(self, discovery: dict[str, Any], raw: str, nonce: str) -> dict[str, Any]:
        key = PyJWKClient(str(discovery["jwks_uri"]), cache_jwk_set=True, lifespan=300).get_signing_key_from_jwt(raw)
        claims = jwt.decode(
            raw,
            key.key,
            algorithms=["RS256", "ES256"],
            audience=self.dependencies.settings.oidc_client_id,
            issuer=self.dependencies.settings.oidc_issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud", "nonce"]},
        )
        if not hmac.compare_digest(str(claims["nonce"]), nonce):
            raise ValueError("OIDC nonce mismatch")
        return claims

    def _exchange_code(self, discovery: dict[str, Any], code: str, verifier: str) -> dict[str, Any]:
        secret = self.dependencies.settings.oidc_client_secret
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.dependencies.settings.oidc_redirect_uri,
                "client_id": self.dependencies.settings.oidc_client_id,
                "client_secret": "" if secret is None else secret.get_secret_value(),
                "code_verifier": verifier,
            }
        ).encode()
        req = UrlRequest(
            str(discovery["token_endpoint"]), body, {"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
        )
        with urlopen(req, timeout=5) as response:  # noqa: S310 - validated provider metadata
            return json.load(response)

    def _discovery(self) -> dict[str, Any]:
        issuer = self.dependencies.settings.oidc_issuer
        if issuer is None:
            raise ValueError("OIDC is not configured")
        with urlopen(f"{issuer}/.well-known/openid-configuration", timeout=5) as response:  # noqa: S310
            document = json.load(response)
        if document.get("issuer") != issuer:
            raise ValueError("OIDC discovery issuer mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = document.get(field)
            if not isinstance(value, str) or urlparse(value).scheme != "https":
                raise ValueError("OIDC discovery endpoint is invalid")
        return document

    def _flow_key(self, state: str) -> str:
        digest = hmac.new(self._identity_hmac_key, state.encode(), hashlib.sha256).hexdigest()
        return f"knotic:{self.dependencies.settings.environment.value}:oidc-flow:{digest}"

    def _configured(self) -> bool:
        settings = self.dependencies.settings
        return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_redirect_uri)

    def _cookie_secure(self) -> bool:
        return self.dependencies.settings.environment not in {RuntimeEnvironment.DEVELOPMENT, RuntimeEnvironment.TEST}

    @staticmethod
    def _problem(status: int, code: str, message: str) -> tuple[Any, int]:
        return jsonify(error={"code": code, "message": message}), status


def register_oidc_api(app: Flask, dependencies: AuthDependencies) -> None:
    OidcApi(app, dependencies).register()
