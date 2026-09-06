"""Authenticated Agora RTC token endpoints for an existing sales session (P4-T001).

Reuses the browser-session authentication, CSRF, origin, and rate-limit checks from
:mod:`knotic_api.lifecycle_api` rather than sharing a base class with it, matching this
codebase's existing per-endpoint-module security plumbing. A caller only ever supplies a
``session_id`` it already owns; the channel name and Agora UID are always derived server-side
(:mod:`knotic_api.voice.session_service`), so a request can never name another tenant's or
session's channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ConfigDict

from knotic_api.domain.types import SessionStatus
from knotic_api.persistence.active_state import CacheReadStatus, RedisSalesStateRepository
from knotic_api.security import (
    AuthenticatedActor,
    RateLimitDecision,
    RedisBrowserSessionStore,
    RedisRateLimiter,
    SecurityDependencyUnavailable,
)
from knotic_api.voice.agora_token import Role
from knotic_api.voice.event_sync import (
    VoiceControlEvent,
    VoiceEventSequenceConflict,
    VoiceEventStoreUnavailable,
    VoiceEventSynchronizer,
)
from knotic_api.voice.managed_agent import ManagedAgentService, ManagedAgentUnavailable
from knotic_api.voice.privacy import (
    VoiceConsentRequest,
    VoiceConsentRequired,
    VoicePrivacyService,
    VoicePrivacyStoreUnavailable,
)
from knotic_api.voice.session_service import (
    AgoraSessionDenied,
    AgoraSessionStoreUnavailable,
    AgoraSessionTokenService,
    IssuedAgoraToken,
)
from knotic_api.voice.telemetry import VoiceObservability, VoiceStage, VoiceTraceContext


class VoiceTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["PUBLISHER", "SUBSCRIBER"] = "PUBLISHER"


@dataclass(frozen=True, slots=True)
class VoiceDependencies:
    active_states: RedisSalesStateRepository
    browser_sessions: RedisBrowserSessionStore
    rate_limiter: RedisRateLimiter
    token_service: AgoraSessionTokenService
    agora_app_id: str
    allowed_origins: frozenset[str]
    event_synchronizer: VoiceEventSynchronizer
    privacy: VoicePrivacyService
    telemetry: VoiceObservability
    managed_agents: ManagedAgentService | None = None


class VoiceApiProblem(Exception):
    def __init__(self, *, status: int, code: str, message: str, rate_limit: RateLimitDecision | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.rate_limit = rate_limit


class VoiceApi:
    def __init__(self, app: Flask, dependencies: VoiceDependencies) -> None:
        self.app = app
        self.dependencies = dependencies

    def register(self) -> None:
        self.app.add_url_rule("/api/v1/sessions/<session_id>/voice/token", view_func=self.issue_token, methods=["POST"])
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/voice/token/renew", view_func=self.renew_token, methods=["POST"]
        )
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/voice/token", view_func=self.revoke_token, methods=["DELETE"]
        )
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/voice/events", view_func=self.accept_event, methods=["POST"]
        )
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/voice/events", view_func=self.reconcile_events, methods=["GET"]
        )
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/voice/consent", view_func=self.grant_consent, methods=["POST"]
        )
        self.app.add_url_rule("/api/v1/sessions/<session_id>/voice/agent", view_func=self.start_agent, methods=["POST"])
        self.app.add_url_rule(
            "/api/v1/sessions/<session_id>/voice/agent", view_func=self.stop_agent, methods=["DELETE"]
        )

    def start_agent(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, _, parsed_session_id, rate_limit = self._authorize(session_id, action="voice-agent-start")
            self._require_idempotency_key()
            service = self.dependencies.managed_agents
            if service is None:
                raise VoiceApiProblem(
                    status=503,
                    code="VOICE_AGENT_NOT_CONFIGURED",
                    message="The managed voice agent is not configured for this environment.",
                )
            issued = self.dependencies.token_service.issue(
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                session_id=parsed_session_id,
                role="PUBLISHER",
                now=datetime.now(UTC),
            )
            result = service.start(tenant_id=actor.tenant_id, session_id=parsed_session_id, customer_uid=issued.uid)
            response = jsonify(result)
            self._rate_limit_headers(response, rate_limit)
            return response
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except ManagedAgentUnavailable:
            return self._problem(
                VoiceApiProblem(
                    status=503,
                    code="VOICE_AGENT_UNAVAILABLE",
                    message="The managed voice agent could not be confirmed.",
                )
            )

    def stop_agent(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, parsed_session_id, rate_limit = self._authorize_control(
                session_id, action="voice-agent-stop", limit=20
            )
            self._require_idempotency_key()
            service = self.dependencies.managed_agents
            if service is None:
                raise VoiceApiProblem(
                    status=503,
                    code="VOICE_AGENT_NOT_CONFIGURED",
                    message="The managed voice agent is not configured for this environment.",
                )
            result = service.stop(tenant_id=actor.tenant_id, session_id=parsed_session_id)
            response = jsonify(result)
            self._rate_limit_headers(response, rate_limit)
            return response
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except ManagedAgentUnavailable:
            return self._problem(
                VoiceApiProblem(
                    status=503,
                    code="VOICE_AGENT_UNAVAILABLE",
                    message="The managed voice agent stop was not confirmed.",
                )
            )

    def grant_consent(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, parsed_session_id, rate_limit = self._authorize_control(session_id, action="voice-consent", limit=10)
            idempotency_key = request.headers.get("Idempotency-Key")
            if request.mimetype != "application/json" or not idempotency_key:
                raise VoiceApiProblem(
                    status=400, code="INVALID_CONSENT_REQUEST", message="A JSON consent request is required."
                )
            try:
                consent_request = VoiceConsentRequest.model_validate_json(request.get_data(cache=True))
            except Exception as error:
                raise VoiceApiProblem(
                    status=422, code="VALIDATION_FAILED", message="Voice consent validation failed."
                ) from error
            if idempotency_key != str(consent_request.consent_id):
                raise VoiceApiProblem(
                    status=409, code="IDEMPOTENCY_CONFLICT", message="Consent idempotency key does not match."
                )
            consent = self.dependencies.privacy.grant(
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                session_id=parsed_session_id,
                request=consent_request,
                now=datetime.now(UTC),
            )
            response = jsonify(
                consent_id=str(consent.consent_id),
                policy_version=consent.policy_version,
                media_region=consent.media_region,
                recording_allowed=False,
                expires_at=consent.expires_at.isoformat().replace("+00:00", "Z"),
            )
            self._rate_limit_headers(response, rate_limit)
            return response
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except ValueError:
            return self._problem(
                VoiceApiProblem(status=409, code="VOICE_POLICY_DENIED", message="Voice policy denied the request.")
            )
        except (SecurityDependencyUnavailable, VoicePrivacyStoreUnavailable):
            return self._problem(self._dependency_problem())

    def accept_event(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, parsed_session_id, rate_limit = self._authorize_control(session_id, action="voice-event-write")
            idempotency_key = request.headers.get("Idempotency-Key")
            if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
                raise VoiceApiProblem(
                    status=400, code="IDEMPOTENCY_KEY_REQUIRED", message="A valid Idempotency-Key is required."
                )
            if request.mimetype != "application/json":
                raise VoiceApiProblem(
                    status=415, code="UNSUPPORTED_MEDIA_TYPE", message="Content-Type must be application/json."
                )
            try:
                event = VoiceControlEvent.model_validate_json(request.get_data(cache=True))
            except Exception as error:
                raise VoiceApiProblem(
                    status=422, code="VALIDATION_FAILED", message="Voice event validation failed."
                ) from error
            if idempotency_key != str(event.event_id):
                raise VoiceApiProblem(
                    status=409,
                    code="IDEMPOTENCY_CONFLICT",
                    message="Idempotency-Key must identify the submitted voice event.",
                )
            with self.dependencies.telemetry.stage(
                VoiceStage.CAPTURE,
                VoiceTraceContext(correlation_id=event.event_id, session_id=parsed_session_id),
            ) as trace_result:
                acknowledged = self.dependencies.event_synchronizer.accept(
                    tenant_id=actor.tenant_id, session_id=parsed_session_id, event=event
                )
                trace_result["outcome"] = "success"
            response = jsonify(
                event_id=str(acknowledged.event_id),
                stream_id=str(acknowledged.stream_id),
                acknowledged_sequence=acknowledged.acknowledged_sequence,
                server_sequence=acknowledged.server_sequence,
                duplicate=acknowledged.duplicate,
            )
            self._rate_limit_headers(response, rate_limit)
            return response
        except VoiceEventSequenceConflict as conflict:
            response = jsonify(
                error={
                    "code": "VOICE_EVENT_SEQUENCE_CONFLICT",
                    "message": "The next voice event sequence does not match.",
                    "details": {"expected_sequence": conflict.expected_sequence},
                }
            )
            response.status_code = 409
            return response
        except LookupError:
            return self._problem(
                VoiceApiProblem(status=404, code="RESOURCE_NOT_FOUND", message="Session was not found.")
            )
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except (SecurityDependencyUnavailable, VoiceEventStoreUnavailable):
            return self._problem(self._dependency_problem())
        except Exception:
            self.app.logger.exception("unhandled voice event synchronization failure")
            return self._problem(self._internal_problem())

    def reconcile_events(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, parsed_session_id, rate_limit = self._authorize_control(session_id, action="voice-event-read")
            try:
                after = int(request.args.get("after", "0"))
                limit = int(request.args.get("limit", "100"))
                events = self.dependencies.event_synchronizer.reconcile(
                    tenant_id=actor.tenant_id, session_id=parsed_session_id, after=after, limit=limit
                )
            except ValueError as error:
                raise VoiceApiProblem(
                    status=422, code="VALIDATION_FAILED", message="Replay cursor validation failed."
                ) from error
            response = jsonify(
                events=[
                    {
                        "event_id": str(event.event_id),
                        "stream_id": str(event.stream_id),
                        "acknowledged_sequence": event.acknowledged_sequence,
                        "server_sequence": event.server_sequence,
                        "duplicate": True,
                    }
                    for event in events
                ],
                next_after=events[-1].server_sequence if events else after,
            )
            self._rate_limit_headers(response, rate_limit)
            return response
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except (SecurityDependencyUnavailable, VoiceEventStoreUnavailable):
            return self._problem(self._dependency_problem())
        except Exception:
            self.app.logger.exception("unhandled voice event reconciliation failure")
            return self._problem(self._internal_problem())

    def issue_token(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, role, parsed_session_id, rate_limit = self._authorize(session_id, action="voice-token-issue")
            issued = self.dependencies.token_service.issue(
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                session_id=parsed_session_id,
                role=role,
                now=datetime.now(UTC),
            )
            return self._success(issued, rate_limit)
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except VoiceConsentRequired:
            return self._problem(
                VoiceApiProblem(status=409, code="VOICE_CONSENT_REQUIRED", message="Voice consent is required.")
            )
        except AgoraSessionDenied as denied:
            return self._problem(VoiceApiProblem(status=409, code="VOICE_SESSION_DENIED", message=denied.reason))
        except (SecurityDependencyUnavailable, AgoraSessionStoreUnavailable, VoicePrivacyStoreUnavailable):
            return self._problem(self._dependency_problem())
        except Exception:
            self.app.logger.exception("unhandled voice token issuance failure")
            return self._problem(self._internal_problem())

    def renew_token(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, role, parsed_session_id, rate_limit = self._authorize(session_id, action="voice-token-renew")
            issued = self.dependencies.token_service.renew(
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                session_id=parsed_session_id,
                role=role,
                now=datetime.now(UTC),
            )
            return self._success(issued, rate_limit)
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except VoiceConsentRequired:
            return self._problem(
                VoiceApiProblem(status=409, code="VOICE_CONSENT_REQUIRED", message="Voice consent is required.")
            )
        except AgoraSessionDenied as denied:
            return self._problem(VoiceApiProblem(status=409, code="VOICE_SESSION_DENIED", message=denied.reason))
        except (SecurityDependencyUnavailable, AgoraSessionStoreUnavailable, VoicePrivacyStoreUnavailable):
            return self._problem(self._dependency_problem())
        except Exception:
            self.app.logger.exception("unhandled voice token renewal failure")
            return self._problem(self._internal_problem())

    def revoke_token(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, parsed_session_id, rate_limit = self._authorize_control(
                session_id, action="voice-token-revoke", limit=20
            )
            self.dependencies.token_service.revoke(
                tenant_id=actor.tenant_id, actor_id=actor.actor_id, session_id=parsed_session_id
            )
            self.dependencies.privacy.revoke(
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                session_id=parsed_session_id,
                now=datetime.now(UTC),
            )
            response = jsonify(status="revoked")
            self._rate_limit_headers(response, rate_limit)
            return response
        except VoiceApiProblem as problem:
            return self._problem(problem)
        except (SecurityDependencyUnavailable, AgoraSessionStoreUnavailable, VoicePrivacyStoreUnavailable):
            return self._problem(self._dependency_problem())
        except Exception:
            self.app.logger.exception("unhandled voice token revocation failure")
            return self._problem(self._internal_problem())

    def _authorize(self, session_id: str, *, action: str) -> tuple[AuthenticatedActor, Role, UUID, RateLimitDecision]:
        actor, parsed_session_id, rate_limit = self._authorize_control(session_id, action=action, limit=20)
        self.dependencies.privacy.require(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            session_id=parsed_session_id,
            now=datetime.now(UTC),
        )
        return actor, self._role(), parsed_session_id, rate_limit

    def _authorize_control(
        self, session_id: str, *, action: str, limit: int = 120
    ) -> tuple[AuthenticatedActor, UUID, RateLimitDecision]:
        authenticated = self.dependencies.browser_sessions.authenticate(request.cookies.get("knotic_session"))
        if authenticated is None:
            raise VoiceApiProblem(status=401, code="AUTHENTICATION_REQUIRED", message="Authentication is required.")
        actor, browser_session = authenticated
        origin = request.headers.get("Origin")
        if origin is None or origin not in self.dependencies.allowed_origins:
            raise VoiceApiProblem(status=403, code="ORIGIN_DENIED", message="Request origin is not allowed.")
        if not self.dependencies.browser_sessions.verify_csrf(browser_session, request.headers.get("X-CSRF-Token")):
            raise VoiceApiProblem(status=403, code="CSRF_FAILED", message="CSRF validation failed.")
        rate_limit = self.dependencies.rate_limiter.check(actor, action=action, limit=limit)
        if not rate_limit.allowed:
            raise VoiceApiProblem(
                status=429, code="RATE_LIMITED", message="Rate limit exceeded.", rate_limit=rate_limit
            )
        parsed_session_id = self._session_id(session_id)
        self._require_active_session(actor, parsed_session_id)
        return actor, parsed_session_id, rate_limit

    def _require_active_session(self, actor: AuthenticatedActor, session_id: UUID) -> None:
        read = self.dependencies.active_states.load(actor.tenant_id, session_id)
        if read.status != CacheReadStatus.HIT or read.envelope is None:
            raise VoiceApiProblem(status=404, code="RESOURCE_NOT_FOUND", message="Session was not found.")
        if read.envelope.state.status in {SessionStatus.ENDED, SessionStatus.FAILED}:
            raise VoiceApiProblem(
                status=409,
                code="SESSION_NOT_ACCEPTING_VOICE",
                message="A voice channel cannot be opened for an ended or failed session.",
            )

    @staticmethod
    def _role() -> Role:
        raw = request.get_data(cache=True)
        if not raw:
            return "PUBLISHER"
        if request.mimetype != "application/json":
            raise VoiceApiProblem(
                status=415, code="UNSUPPORTED_MEDIA_TYPE", message="Content-Type must be application/json."
            )
        try:
            payload = VoiceTokenRequest.model_validate_json(raw)
        except Exception as error:
            raise VoiceApiProblem(status=422, code="VALIDATION_FAILED", message="Request validation failed.") from error
        return payload.role

    @staticmethod
    def _session_id(value: str) -> UUID:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise VoiceApiProblem(status=404, code="RESOURCE_NOT_FOUND", message="Session was not found.") from error
        if parsed.version != 7:
            raise VoiceApiProblem(status=404, code="RESOURCE_NOT_FOUND", message="Session was not found.")
        return parsed

    @staticmethod
    def _require_idempotency_key() -> str:
        value = request.headers.get("Idempotency-Key", "")
        if not 16 <= len(value) <= 128 or not value.isascii():
            raise VoiceApiProblem(
                status=400, code="IDEMPOTENCY_KEY_REQUIRED", message="A valid Idempotency-Key is required."
            )
        return value

    def _success(self, issued: IssuedAgoraToken, rate_limit: RateLimitDecision) -> Response:
        response = jsonify(
            app_id=self.dependencies.agora_app_id,
            channel_name=issued.channel_name,
            uid=issued.uid,
            role=issued.role,
            token=issued.token,
            issued_at=issued.issued_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            expires_at=issued.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        )
        self._rate_limit_headers(response, rate_limit)
        return response

    @staticmethod
    def _rate_limit_headers(response: Response, rate_limit: RateLimitDecision) -> None:
        response.headers["RateLimit-Limit"] = str(rate_limit.limit)
        response.headers["RateLimit-Remaining"] = str(rate_limit.remaining)
        response.headers["RateLimit-Reset"] = str(rate_limit.reset_epoch)

    def _problem(self, problem: VoiceApiProblem) -> Response:
        response = jsonify(error={"code": problem.code, "message": problem.message})
        response.status_code = problem.status
        if problem.rate_limit is not None:
            self._rate_limit_headers(response, problem.rate_limit)
        return response

    @staticmethod
    def _dependency_problem() -> VoiceApiProblem:
        return VoiceApiProblem(
            status=503, code="DEPENDENCY_UNAVAILABLE", message="A required dependency is unavailable."
        )

    @staticmethod
    def _internal_problem() -> VoiceApiProblem:
        return VoiceApiProblem(status=500, code="INTERNAL_ERROR", message="The request could not be completed.")


def register_voice_api(app: Flask, dependencies: VoiceDependencies) -> None:
    VoiceApi(app, dependencies).register()
