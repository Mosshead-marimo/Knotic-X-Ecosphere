"""Authenticated version-1 sales-session lifecycle endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import redis
import sqlalchemy as sa
from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.domain.models import DomainEvent, Outcome, SalesState
from knotic_api.domain.types import EventType, OutcomeType, SessionStatus
from knotic_api.persistence.active_state import ActiveStateError, RedisSalesStateRepository
from knotic_api.persistence.repositories import IdempotencyReservation, SessionCreate, SessionRecord
from knotic_api.persistence.unit_of_work import UnitOfWork
from knotic_api.security import (
    AuthenticatedActor,
    BrowserSessionValue,
    RateLimitDecision,
    RedisBrowserSessionStore,
    RedisRateLimiter,
    ReplayCipher,
    SecurityDependencyUnavailable,
)

_IDEMPOTENCY_KEY = re.compile(r"^[!-~]{16,128}$")


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str = Field(min_length=2, max_length=35)
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("locale")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value):
            raise ValueError("locale must be a valid language tag")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA timezone") from error
        return value


class EndSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["USER_REQUEST", "COMPLETED", "DISCONNECTED"]
    expected_session_version: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class LifecycleDependencies:
    engine: Engine
    browser_sessions: RedisBrowserSessionStore
    rate_limiter: RedisRateLimiter
    active_states: RedisSalesStateRepository
    replay_cipher: ReplayCipher
    idempotency_hmac_key: bytes
    allowed_origins: frozenset[str]


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    request_id: UUID
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class RequestSecurity:
    actor: AuthenticatedActor
    browser_session: BrowserSessionValue
    identity: RequestIdentity
    rate_limit: RateLimitDecision


class ApiProblem(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        category: str,
        message: str,
        retryable: bool = False,
        details: list[dict[str, str]] | None = None,
        rate_limit: RateLimitDecision | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.category = category
        self.message = message
        self.retryable = retryable
        self.details = details or []
        self.rate_limit = rate_limit


class LifecycleApi:
    def __init__(self, app: Flask, dependencies: LifecycleDependencies) -> None:
        self.app = app
        self.dependencies = dependencies

    def register(self) -> None:
        self.app.add_url_rule("/api/v1/sessions", view_func=self.create_session, methods=["POST"])
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>",
            view_func=self.get_session,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/end",
            view_func=self.end_session,
            methods=["POST"],
        )

    def create_session(self) -> ResponseReturnValue:
        identity: RequestIdentity | None = None
        try:
            identity = self._request_identity()
            security = self._secure(identity, action="create-session", limit=30, mutation=True)
            payload = self._json_body(SessionCreateRequest)
            idempotency_key = self._idempotency_key()
            canonical_request = self._canonical_request(payload)
            reservation = self._reservation(
                security.actor,
                idempotency_key=idempotency_key,
                request_hash=hashlib.sha256(canonical_request).digest(),
                path="/api/v1/sessions",
            )
            session_id = new_uuid7()
            now = datetime.now(UTC)
            with UnitOfWork(
                self.dependencies.engine,
                tenant_id=security.actor.tenant_id,
                actor_id=security.actor.actor_id,
            ) as work:
                replay = self._reserve_or_replay(work, reservation, identity)
                if replay is not None:
                    return self._replay_response(replay, identity, security.rate_limit)
                session = work.sessions.create(
                    SessionCreate(
                        session_id=session_id,
                        locale=payload.locale,
                        timezone=payload.timezone,
                    )
                )
                event = DomainEvent(
                    event_id=new_uuid7(),
                    event_type=EventType.SESSION_CREATED,
                    occurred_at=now,
                    tenant_id=security.actor.tenant_id,
                    session_id=session_id,
                    sequence=1,
                    correlation_id=identity.correlation_id,
                    actor_type=security.actor.actor_type,
                    actor_id=security.actor.actor_id,
                    payload={"session_version": session.version, "status": session.status},
                )
                work.events.append(event)
                body = self._session_resource(session)
                response_bytes = self._response_bytes(body)
                self._complete_idempotency(
                    work,
                    reservation,
                    status=201,
                    body=response_bytes,
                    headers={"Location": f"/api/v1/sessions/{session_id}", "ETag": f'"{session.version}"'},
                )
            state = SalesState(
                session_id=session_id,
                tenant_id=security.actor.tenant_id,
                status=SessionStatus.CREATED,
                version=session.version,
                created_at=session.created_at,
                updated_at=session.updated_at,
            )
            try:
                self.dependencies.active_states.create(state, event_watermark=1, fencing_token=0)
            except ActiveStateError:
                self.app.logger.warning("active state cache unavailable after durable session creation")
            return self._success(
                body,
                status=201,
                identity=identity,
                rate_limit=security.rate_limit,
                headers={"Location": f"/api/v1/sessions/{session_id}", "ETag": f'"{session.version}"'},
            )
        except ApiProblem as problem:
            return self._problem(problem, identity)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(self._dependency_problem(), identity)
        except Exception:
            self.app.logger.exception("unhandled create-session failure")
            return self._problem(self._internal_problem(), identity)

    def get_session(self, session_id: str) -> ResponseReturnValue:
        identity: RequestIdentity | None = None
        try:
            identity = self._request_identity()
            security = self._secure(identity, action="get-session", limit=120, mutation=False)
            parsed_session_id = self._session_id(session_id)
            with UnitOfWork(
                self.dependencies.engine,
                tenant_id=security.actor.tenant_id,
                actor_id=security.actor.actor_id,
            ) as work:
                session = work.sessions.get(parsed_session_id)
            if session is None:
                raise ApiProblem(
                    status=404,
                    code="RESOURCE_NOT_FOUND",
                    category="NOT_FOUND",
                    message="Session was not found.",
                )
            body = self._session_resource(session)
            return self._success(
                body,
                status=200,
                identity=identity,
                rate_limit=security.rate_limit,
                headers={"ETag": f'"{session.version}"'},
            )
        except ApiProblem as problem:
            return self._problem(problem, identity)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(self._dependency_problem(), identity)
        except Exception:
            self.app.logger.exception("unhandled get-session failure")
            return self._problem(self._internal_problem(), identity)

    def end_session(self, session_id: str) -> ResponseReturnValue:
        identity: RequestIdentity | None = None
        try:
            identity = self._request_identity()
            security = self._secure(identity, action="end-session", limit=30, mutation=True)
            parsed_session_id = self._session_id(session_id)
            payload = self._json_body(EndSessionRequest)
            idempotency_key = self._idempotency_key()
            request_hash = hashlib.sha256(self._canonical_request(payload)).digest()
            reservation = self._reservation(
                security.actor,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                path=f"/api/v1/sessions/{parsed_session_id}/end",
            )
            now = datetime.now(UTC)
            with UnitOfWork(
                self.dependencies.engine,
                tenant_id=security.actor.tenant_id,
                actor_id=security.actor.actor_id,
            ) as work:
                replay = self._reserve_or_replay(work, reservation, identity)
                if replay is not None:
                    return self._replay_response(replay, identity, security.rate_limit)
                session = work.sessions.get(parsed_session_id, for_update=True)
                if session is None:
                    raise ApiProblem(
                        status=404,
                        code="RESOURCE_NOT_FOUND",
                        category="NOT_FOUND",
                        message="Session was not found.",
                    )
                if session.version != payload.expected_session_version:
                    raise self._version_conflict(session.version)
                current_status = SessionStatus(session.status)
                if current_status == SessionStatus.ENDED:
                    ended = session
                elif current_status == SessionStatus.FAILED:
                    raise ApiProblem(
                        status=409,
                        code="SESSION_NOT_ACCEPTING_TURNS",
                        category="CONFLICT",
                        message="Failed sessions cannot be ended.",
                    )
                else:
                    ending = session
                    if current_status != SessionStatus.ENDING:
                        ending_candidate = work.sessions.transition(
                            parsed_session_id,
                            expected_version=session.version,
                            expected_status=current_status,
                            target_status=SessionStatus.ENDING,
                            at=now,
                        )
                        if ending_candidate is None:
                            raise self._version_conflict(session.version)
                        ending = ending_candidate
                    ended_candidate = work.sessions.transition(
                        parsed_session_id,
                        expected_version=ending.version,
                        expected_status=SessionStatus.ENDING,
                        target_status=SessionStatus.ENDED,
                        at=now,
                        outcome=OutcomeType.CLOSED_NO_ACTION.value,
                    )
                    if ended_candidate is None:
                        raise self._version_conflict(ending.version)
                    ended = ended_candidate
                    work.outcomes.assign(
                        Outcome(
                            outcome_id=new_uuid7(),
                            tenant_id=security.actor.tenant_id,
                            session_id=parsed_session_id,
                            outcome=OutcomeType.CLOSED_NO_ACTION,
                            source="SYSTEM",
                            assigned_at=now,
                        )
                    )
                    sequence = work.events.next_sequence(parsed_session_id)
                    work.events.append(
                        DomainEvent(
                            event_id=new_uuid7(),
                            event_type=EventType.SESSION_ENDED,
                            occurred_at=now,
                            tenant_id=security.actor.tenant_id,
                            session_id=parsed_session_id,
                            sequence=sequence,
                            correlation_id=identity.correlation_id,
                            actor_type=security.actor.actor_type,
                            actor_id=security.actor.actor_id,
                            payload={
                                "outcome": "CLOSED_NO_ACTION",
                                "reason": payload.reason,
                                "session_version": ended.version,
                            },
                        )
                    )
                body = self._session_resource(ended)
                response_bytes = self._response_bytes(body)
                self._complete_idempotency(
                    work,
                    reservation,
                    status=200,
                    body=response_bytes,
                    headers={"ETag": f'"{ended.version}"'},
                )
            try:
                self.dependencies.active_states.delete(security.actor.tenant_id, parsed_session_id)
            except ActiveStateError:
                self.app.logger.warning("active state cache unavailable after durable session end")
            return self._success(
                body,
                status=200,
                identity=identity,
                rate_limit=security.rate_limit,
                headers={"ETag": f'"{ended.version}"'},
            )
        except ApiProblem as problem:
            return self._problem(problem, identity)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(self._dependency_problem(), identity)
        except Exception:
            self.app.logger.exception("unhandled end-session failure")
            return self._problem(self._internal_problem(), identity)

    def _request_identity(self) -> RequestIdentity:
        try:
            request_id = UUID(request.headers["X-Request-ID"]) if "X-Request-ID" in request.headers else new_uuid7()
            correlation_id = (
                UUID(request.headers["X-Correlation-ID"]) if "X-Correlation-ID" in request.headers else request_id
            )
        except ValueError as error:
            raise ApiProblem(
                status=400,
                code="INVALID_HEADER",
                category="VALIDATION",
                message="Request identifier headers must contain UUIDs.",
            ) from error
        return RequestIdentity(request_id=request_id, correlation_id=correlation_id)

    def _secure(self, identity: RequestIdentity, *, action: str, limit: int, mutation: bool) -> RequestSecurity:
        authenticated = self.dependencies.browser_sessions.authenticate(request.cookies.get("knotic_session"))
        if authenticated is None:
            raise ApiProblem(
                status=401,
                code="AUTHENTICATION_REQUIRED",
                category="AUTHENTICATION",
                message="Authentication is required.",
            )
        actor, browser_session = authenticated
        if mutation:
            origin = request.headers.get("Origin")
            if origin is None or origin not in self.dependencies.allowed_origins:
                raise ApiProblem(
                    status=403,
                    code="ORIGIN_DENIED",
                    category="AUTHORIZATION",
                    message="Request origin is not allowed.",
                )
            if not self.dependencies.browser_sessions.verify_csrf(browser_session, request.headers.get("X-CSRF-Token")):
                raise ApiProblem(
                    status=403,
                    code="CSRF_FAILED",
                    category="AUTHORIZATION",
                    message="CSRF validation failed.",
                )
        rate_limit = self.dependencies.rate_limiter.check(actor, action=action, limit=limit)
        if not rate_limit.allowed:
            raise ApiProblem(
                status=429,
                code="RATE_LIMITED",
                category="RATE_LIMIT",
                message="Rate limit exceeded.",
                retryable=True,
                rate_limit=rate_limit,
            )
        return RequestSecurity(actor, browser_session, identity, rate_limit)

    def _json_body(self, model: type[BaseModel]) -> Any:
        if request.mimetype != "application/json":
            raise ApiProblem(
                status=415,
                code="UNSUPPORTED_MEDIA_TYPE",
                category="VALIDATION",
                message="Content-Type must be application/json.",
            )
        try:
            raw = request.get_json(force=False, silent=False)
        except Exception as error:
            raise ApiProblem(
                status=400,
                code="MALFORMED_JSON",
                category="VALIDATION",
                message="Request body is not valid JSON.",
            ) from error
        try:
            return model.model_validate(raw)
        except ValidationError as error:
            details = [
                {"field": ".".join(str(part) for part in item["loc"]), "issue": item["type"]}
                for item in error.errors()[:50]
            ]
            raise ApiProblem(
                status=422,
                code="VALIDATION_FAILED",
                category="VALIDATION",
                message="Request validation failed.",
                details=details,
            ) from error

    @staticmethod
    def _canonical_request(payload: BaseModel) -> bytes:
        return json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()

    def _idempotency_key(self) -> str:
        value = request.headers.get("Idempotency-Key")
        if value is None or not _IDEMPOTENCY_KEY.fullmatch(value):
            raise ApiProblem(
                status=400,
                code="INVALID_HEADER",
                category="VALIDATION",
                message="Idempotency-Key is required and must contain 16 to 128 visible ASCII characters.",
            )
        return value

    def _reservation(
        self,
        actor: AuthenticatedActor,
        *,
        idempotency_key: str,
        request_hash: bytes,
        path: str,
    ) -> IdempotencyReservation:
        key_hmac = hmac.new(self.dependencies.idempotency_hmac_key, idempotency_key.encode(), hashlib.sha256).digest()
        return IdempotencyReservation(
            record_id=new_uuid7(),
            actor_id=actor.actor_id,
            workload="browser-session-api",
            method=request.method,
            path=path,
            key_hmac=key_hmac,
            request_hash=request_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

    def _reserve_or_replay(
        self,
        work: UnitOfWork,
        reservation: IdempotencyReservation,
        identity: RequestIdentity,
    ) -> tuple[int, bytes, dict[str, str]] | None:
        del identity
        if work.idempotency.reserve(reservation):
            return None
        existing = work.idempotency.get(reservation, for_update=True)
        if existing is None:
            raise self._dependency_problem()
        if existing["request_hash"] != reservation.request_hash:
            raise ApiProblem(
                status=409,
                code="IDEMPOTENCY_CONFLICT",
                category="CONFLICT",
                message="Idempotency key was already used for a different request.",
            )
        if existing["status"] != "COMPLETED" or existing["response_body_ciphertext"] is None:
            raise ApiProblem(
                status=409,
                code="IDEMPOTENCY_CONFLICT",
                category="CONFLICT",
                message="An equivalent request is still processing.",
                retryable=True,
            )
        body = self.dependencies.replay_cipher.decrypt(
            existing["response_body_ciphertext"],
            associated_data=self._replay_aad(reservation),
        )
        headers = {str(key): str(value) for key, value in (existing["response_headers"] or {}).items()}
        return int(existing["response_status"]), body, headers

    def _complete_idempotency(
        self,
        work: UnitOfWork,
        reservation: IdempotencyReservation,
        *,
        status: int,
        body: bytes,
        headers: dict[str, str],
    ) -> None:
        encrypted = self.dependencies.replay_cipher.encrypt(body, associated_data=self._replay_aad(reservation))
        if not work.idempotency.complete(
            reservation.record_id,
            response_status=status,
            response_body_ciphertext=encrypted,
            response_headers=headers,
        ):
            raise RuntimeError("idempotency completion lost its reservation")

    @staticmethod
    def _replay_aad(reservation: IdempotencyReservation) -> bytes:
        return b"|".join(
            (
                str(reservation.actor_id).encode(),
                reservation.workload.encode(),
                reservation.method.encode(),
                reservation.path.encode(),
                reservation.key_hmac.hex().encode(),
            )
        )

    @staticmethod
    def _session_id(value: str) -> UUID:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ApiProblem(
                status=404,
                code="RESOURCE_NOT_FOUND",
                category="NOT_FOUND",
                message="Session was not found.",
            ) from error
        if parsed.version != 7:
            raise ApiProblem(
                status=404,
                code="RESOURCE_NOT_FOUND",
                category="NOT_FOUND",
                message="Session was not found.",
            )
        return parsed

    @staticmethod
    def _session_resource(session: SessionRecord) -> dict[str, Any]:
        if session.summary_ciphertext is not None or session.latest_request_ciphertext is not None:
            raise SecurityDependencyUnavailable("encrypted session projection requires authorized hydration")
        state: dict[str, Any] = {
            "current_intent": session.current_intent,
            "buying_stage": session.buying_stage or "NURTURE",
            "qualification_score": session.qualification_score or 0,
            "next_best_action": session.next_best_action or "ASK_DISCOVERY",
            "conversation_summary": "",
            "latest_request": None,
            "outcome": session.outcome,
        }
        if session.current_topic is not None:
            state["current_topic"] = session.current_topic
        return {
            "session_id": str(session.session_id),
            "version": session.version,
            "status": session.status,
            "created_at": LifecycleApi._timestamp(session.created_at),
            "updated_at": LifecycleApi._timestamp(session.updated_at),
            "ended_at": None if session.ended_at is None else LifecycleApi._timestamp(session.ended_at),
            "state": state,
        }

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _response_bytes(body: dict[str, Any]) -> bytes:
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def _replay_response(
        self,
        replay: tuple[int, bytes, dict[str, str]],
        identity: RequestIdentity,
        rate_limit: RateLimitDecision,
    ) -> Response:
        status, body, headers = replay
        response = Response(body, status=status, content_type="application/json")
        self._response_headers(response, identity, rate_limit, headers)
        return response

    def _success(
        self,
        body: dict[str, Any],
        *,
        status: int,
        identity: RequestIdentity,
        rate_limit: RateLimitDecision,
        headers: dict[str, str],
    ) -> Response:
        response = jsonify(body)
        response.status_code = status
        self._response_headers(response, identity, rate_limit, headers)
        return response

    @staticmethod
    def _response_headers(
        response: Response,
        identity: RequestIdentity,
        rate_limit: RateLimitDecision,
        headers: dict[str, str],
    ) -> None:
        response.headers["X-Request-ID"] = str(identity.request_id)
        response.headers["X-Correlation-ID"] = str(identity.correlation_id)
        response.headers["RateLimit-Limit"] = str(rate_limit.limit)
        response.headers["RateLimit-Remaining"] = str(rate_limit.remaining)
        response.headers["RateLimit-Reset"] = str(rate_limit.reset_epoch)
        for name, value in headers.items():
            response.headers[name] = value

    def _problem(self, problem: ApiProblem, identity: RequestIdentity | None = None) -> Response:
        resolved = identity or RequestIdentity(new_uuid7(), new_uuid7())
        response = jsonify(
            error={
                "code": problem.code,
                "category": problem.category,
                "message": problem.message,
                "retryable": problem.retryable,
                "request_id": str(resolved.request_id),
                "correlation_id": str(resolved.correlation_id),
                "details": problem.details,
            }
        )
        response.status_code = problem.status
        response.headers["X-Request-ID"] = str(resolved.request_id)
        response.headers["X-Correlation-ID"] = str(resolved.correlation_id)
        if problem.rate_limit is not None:
            response.headers["RateLimit-Limit"] = str(problem.rate_limit.limit)
            response.headers["RateLimit-Remaining"] = str(problem.rate_limit.remaining)
            response.headers["RateLimit-Reset"] = str(problem.rate_limit.reset_epoch)
            response.headers["Retry-After"] = str(
                max(1, problem.rate_limit.reset_epoch - int(datetime.now(UTC).timestamp()))
            )
        return response

    @staticmethod
    def _version_conflict(observed_version: int) -> ApiProblem:
        return ApiProblem(
            status=409,
            code="VERSION_CONFLICT",
            category="CONFLICT",
            message="Session version does not match the expected version.",
            details=[{"field": "expected_session_version", "issue": f"current version is {observed_version}"}],
        )

    @staticmethod
    def _dependency_problem() -> ApiProblem:
        return ApiProblem(
            status=503,
            code="DEPENDENCY_UNAVAILABLE",
            category="DEPENDENCY",
            message="A required dependency is unavailable.",
            retryable=True,
        )

    @staticmethod
    def _internal_problem() -> ApiProblem:
        return ApiProblem(
            status=500,
            code="INTERNAL_ERROR",
            category="INTERNAL",
            message="The request could not be completed.",
        )


def build_lifecycle_dependencies(
    *,
    database_url: str,
    redis_url: str,
    environment: str,
    security_key: bytes,
    allowed_origins: list[str],
) -> LifecycleDependencies:
    sqlalchemy_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = sa.create_engine(sqlalchemy_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    redis_client = redis.Redis.from_url(
        redis_url,
        decode_responses=False,
        socket_connect_timeout=1,
        socket_timeout=1,
        health_check_interval=30,
    )
    from knotic_api.security import derive_key

    return LifecycleDependencies(
        engine=engine,
        browser_sessions=RedisBrowserSessionStore(redis_client, environment=environment, master_key=security_key),
        rate_limiter=RedisRateLimiter(redis_client, environment=environment, master_key=security_key),
        active_states=RedisSalesStateRepository(redis_client, environment=environment),
        replay_cipher=ReplayCipher(security_key),
        idempotency_hmac_key=derive_key(security_key, b"idempotency-key-hmac"),
        allowed_origins=frozenset(allowed_origins),
    )


def register_lifecycle_api(app: Flask, dependencies: LifecycleDependencies) -> None:
    LifecycleApi(app, dependencies).register()
