"""Tenant-isolated operator console projections and governed actions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from urllib.request import urlopen
from uuid import UUID

import redis
import sqlalchemy as sa
from flask import Flask, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.exc import DBAPIError, IntegrityError

from knotic_api.config import BackendSettings
from knotic_api.domain.identifiers import new_uuid7
from knotic_api.persistence.hydration import StateFieldCipher, StateRecoveryError
from knotic_api.security import (
    AuthenticatedActor,
    BrowserSessionValue,
    RedisBrowserSessionStore,
    RedisRateLimiter,
    SecurityDependencyUnavailable,
    derive_key,
)

_OPERATOR_ROLES = frozenset({"ADMIN", "SUPERVISOR", "SALES_REP"})
_ADMIN_ROLES = frozenset({"ADMIN", "SUPERVISOR"})


class HandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=500)
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    expected_session_version: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class ConsoleDependencies:
    settings: BackendSettings
    engine: Engine
    redis_client: redis.Redis
    browser_sessions: RedisBrowserSessionStore
    rate_limiter: RedisRateLimiter
    allowed_origins: frozenset[str]


class ConsoleProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ConsoleApi:
    def __init__(self, app: Flask, dependencies: ConsoleDependencies) -> None:
        self.app = app
        self.dependencies = dependencies
        master_key = dependencies.settings.session_security_key.get_secret_value().encode()
        self._field_cipher = StateFieldCipher(master_key)
        self._idempotency_key = derive_key(master_key, b"console-idempotency")

    def register(self) -> None:
        app = self.app
        app.add_url_rule(
            "/api/v1/console/sessions", endpoint="console_sessions", view_func=self.sessions, methods=["GET"]
        )
        app.add_url_rule(
            "/api/v1/console/sessions/<session_id>", endpoint="console_session", view_func=self.session, methods=["GET"]
        )
        app.add_url_rule(
            "/api/v1/console/sessions/<session_id>/handoff",
            endpoint="console_handoff",
            view_func=self.handoff,
            methods=["POST"],
        )
        app.add_url_rule(
            "/api/v1/console/analytics", endpoint="console_analytics", view_func=self.analytics, methods=["GET"]
        )
        app.add_url_rule(
            "/api/v1/console/integrations",
            endpoint="console_integrations",
            view_func=self.integrations,
            methods=["GET"],
        )
        app.add_url_rule("/api/v1/console/system", endpoint="console_system", view_func=self.system, methods=["GET"])
        app.add_url_rule(
            "/api/v1/console/pending-work",
            endpoint="console_pending_work",
            view_func=self.pending_work,
            methods=["GET"],
        )
        app.add_url_rule(
            "/api/v1/console/pending-work/<work_id>/retry",
            endpoint="console_retry_work",
            view_func=self.retry_work,
            methods=["POST"],
        )

    def sessions(self) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-sessions-read")
            limit = self._integer_arg("limit", 25, 1, 100)
            status = request.args.get("status")
            if status and status not in {"CREATED", "ACTIVE", "ENDING", "ENDED", "FAILED"}:
                raise ConsoleProblem(422, "VALIDATION_FAILED", "Session status filter is invalid.")
            cursor = self._decode_cursor(request.args.get("cursor"))
            parameters: dict[str, Any] = {
                "tenant_id": actor.tenant_id,
                "limit": limit + 1,
                "status": status,
                "cursor_at": cursor[0] if cursor else None,
                "cursor_id": cursor[1] if cursor else None,
            }
            statement = sa.text(
                "select s.*, l.name_ciphertext, l.company_ciphertext from sales_sessions s "
                "left join leads l on l.tenant_id=s.tenant_id and l.id=s.lead_id "
                "where s.tenant_id=:tenant_id and "
                "(cast(:status as text) is null or s.status=cast(:status as text)) "
                "and (cast(:cursor_at as timestamptz) is null or (s.updated_at,s.id) < "
                "(cast(:cursor_at as timestamptz),cast(:cursor_id as uuid))) "
                "order by s.updated_at desc,s.id desc limit :limit"
            )
            with self._connection(actor) as connection:
                rows = list(connection.execute(statement, parameters).mappings())
            has_more = len(rows) > limit
            page = rows[:limit]
            return jsonify(
                items=[self._session_summary(row) for row in page],
                next_cursor=self._encode_cursor(page[-1]) if has_more else None,
                has_more=has_more,
            )
        except ConsoleProblem as problem:
            return self._problem(problem)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(ConsoleProblem(503, "DEPENDENCY_UNAVAILABLE", "Session data is unavailable."))

    def session(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-session-read")
            parsed = self._uuid7(session_id)
            with self._connection(actor) as connection:
                row = (
                    connection.execute(
                        sa.text(
                            "select s.*,l.name_ciphertext,l.company_ciphertext from sales_sessions s "
                            "left join leads l on l.tenant_id=s.tenant_id and l.id=s.lead_id "
                            "where s.tenant_id=:tenant_id and s.id=:session_id"
                        ),
                        {"tenant_id": actor.tenant_id, "session_id": parsed},
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ConsoleProblem(404, "RESOURCE_NOT_FOUND", "Session was not found.")
                messages = list(
                    connection.execute(
                        sa.text(
                            "select id,speaker,source,content_ciphertext,interrupted,created_at from messages "
                            "where tenant_id=:tenant_id and session_id=:session_id order by sequence,id limit 500"
                        ),
                        {"tenant_id": actor.tenant_id, "session_id": parsed},
                    ).mappings()
                )
                requirements = list(
                    connection.execute(
                        sa.text(
                            "select field,value_integer,value_text,value_text_array,value_numeric,"
                            "currency,confidence,version from requirements_current "
                            "where tenant_id=:tenant_id and session_id=:session_id order by field"
                        ),
                        {"tenant_id": actor.tenant_id, "session_id": parsed},
                    ).mappings()
                )
                objections = list(
                    connection.execute(
                        sa.text(
                            "select id,category,status,detail_ciphertext,version from objections "
                            "where tenant_id=:tenant_id and session_id=:session_id order by created_at,id"
                        ),
                        {"tenant_id": actor.tenant_id, "session_id": parsed},
                    ).mappings()
                )
                operations = self._session_operations(connection, actor.tenant_id, parsed)
            body = self._session_summary(row)
            body.update(
                transcript=[self._message(actor.tenant_id, message) for message in messages],
                requirements=[self._requirement(item) for item in requirements],
                objections=[self._objection(actor.tenant_id, item) for item in objections],
                operations=operations,
            )
            response = jsonify(body)
            response.headers["ETag"] = f'"{row["version"]}"'
            return response
        except ConsoleProblem as problem:
            return self._problem(problem)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(ConsoleProblem(503, "DEPENDENCY_UNAVAILABLE", "Session data is unavailable."))

    def handoff(self, session_id: str) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-handoff", mutation=True)
            parsed = self._uuid7(session_id)
            payload = self._json(HandoffRequest)
            key = self._idempotency_header()
            key_hmac = hmac.new(self._idempotency_key, key.encode(), hashlib.sha256).digest()
            handoff_id, now = new_uuid7(), datetime.now(UTC)
            context = json.dumps(
                {"session_id": session_id, "reason": payload.reason, "priority": payload.priority},
                sort_keys=True,
            )
            encrypted = self._field_cipher.encrypt(
                context, tenant_id=actor.tenant_id, aggregate_id=handoff_id, field="handoff_context"
            )
            try:
                with self._connection(actor) as connection:
                    session = (
                        connection.execute(
                            sa.text(
                                "select version,status from sales_sessions "
                                "where tenant_id=:tenant_id and id=:id for update"
                            ),
                            {"tenant_id": actor.tenant_id, "id": parsed},
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if session is None:
                        raise ConsoleProblem(404, "RESOURCE_NOT_FOUND", "Session was not found.")
                    if session["version"] != payload.expected_session_version:
                        raise ConsoleProblem(409, "VERSION_CONFLICT", "Session version changed; refresh and try again.")
                    existing = (
                        connection.execute(
                            sa.text(
                                "select id,status,priority,created_at from handoffs "
                                "where tenant_id=:tenant_id and request_key_hmac=:key"
                            ),
                            {"tenant_id": actor.tenant_id, "key": key_hmac},
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if existing is not None:
                        return jsonify(self._handoff_resource(existing)), 200
                    connection.execute(
                        sa.text(
                            "insert into handoffs "
                            "(id,tenant_id,session_id,reason,status,context_ciphertext,priority,"
                            "requested_by,request_key_hmac) "
                            "values (:id,:tenant_id,:session_id,:reason,'REQUESTED',:context,:priority,:actor_id,:key)"
                        ),
                        {
                            "id": handoff_id,
                            "tenant_id": actor.tenant_id,
                            "session_id": parsed,
                            "reason": payload.reason,
                            "context": encrypted,
                            "priority": payload.priority,
                            "actor_id": actor.actor_id,
                            "key": key_hmac,
                        },
                    )
                    connection.execute(
                        sa.text(
                            "insert into domain_events "
                            "(id,tenant_id,session_id,event_id,event_type,event_version,sequence,occurred_at,"
                            "correlation_id,actor_id,actor_type,payload,payload_schema_version) "
                            "select :event_id,:tenant_id,:session_id,:event_id,'operation.updated',1,"
                            "coalesce(max(sequence),0)+1,:at,"
                            ":event_id,:actor_id,'HUMAN_AGENT',cast(:payload as jsonb),1 from domain_events "
                            "where tenant_id=:tenant_id and session_id=:session_id"
                        ),
                        {
                            "event_id": new_uuid7(),
                            "tenant_id": actor.tenant_id,
                            "session_id": parsed,
                            "at": now,
                            "actor_id": actor.actor_id,
                            "payload": json.dumps(
                                {"kind": "HANDOFF", "status": "REQUESTED", "handoff_id": str(handoff_id)}
                            ),
                        },
                    )
            except IntegrityError:
                return self._problem(
                    ConsoleProblem(409, "IDEMPOTENCY_CONFLICT", "Handoff request is already being processed.")
                )
            return jsonify(
                id=str(handoff_id), status="requested", priority=payload.priority, created_at=self._timestamp(now)
            ), 202
        except ConsoleProblem as problem:
            return self._problem(problem)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(ConsoleProblem(503, "DEPENDENCY_UNAVAILABLE", "Handoff could not be requested."))

    def analytics(self) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-analytics-read")
            days = self._integer_arg("days", 30, 1, 366)
            since = datetime.now(UTC) - timedelta(days=days)
            with self._connection(actor) as connection:
                summary = (
                    connection.execute(
                        sa.text(
                            "select count(*) total,count(*) filter(where status='ACTIVE') active,"
                            "count(*) filter(where outcome is not null) completed,"
                            "coalesce(avg(qualification_score),0) average_score,"
                            "coalesce(avg(extract(epoch from "
                            "(coalesce(ended_at,timezone('utc',now()))-started_at))),0) "
                            "average_duration_seconds "
                            "from sales_sessions where tenant_id=:tenant_id and created_at>=:since"
                        ),
                        {"tenant_id": actor.tenant_id, "since": since},
                    )
                    .mappings()
                    .one()
                )
                outcomes = list(
                    connection.execute(
                        sa.text(
                            "select coalesce(outcome,'UNASSIGNED') outcome,count(*) count from sales_sessions "
                            "where tenant_id=:tenant_id and created_at>=:since group by outcome order by count desc"
                        ),
                        {"tenant_id": actor.tenant_id, "since": since},
                    ).mappings()
                )
                series = list(
                    connection.execute(
                        sa.text(
                            "select date_trunc('day',created_at) bucket,count(*) total,"
                            "count(*) filter(where outcome in ('LEAD_QUALIFIED','ENTERPRISE_DEMO_BOOKED')) converted "
                            "from sales_sessions where tenant_id=:tenant_id and created_at>=:since "
                            "group by bucket order by bucket"
                        ),
                        {"tenant_id": actor.tenant_id, "since": since},
                    ).mappings()
                )
            total = int(summary["total"])
            return jsonify(
                range={"days": days, "from": self._timestamp(since), "to": self._timestamp(datetime.now(UTC))},
                totals={
                    "sessions": total,
                    "active": int(summary["active"]),
                    "completed": int(summary["completed"]),
                    "average_qualification": round(float(summary["average_score"]), 1),
                    "average_duration_seconds": round(float(summary["average_duration_seconds"]), 1),
                },
                conversion_rate=round(
                    sum(
                        int(row["count"])
                        for row in outcomes
                        if row["outcome"] in {"LEAD_QUALIFIED", "ENTERPRISE_DEMO_BOOKED"}
                    )
                    / total
                    * 100,
                    1,
                )
                if total
                else 0,
                outcomes=[dict(row) for row in outcomes],
                series=[
                    {"at": self._timestamp(row["bucket"]), "sessions": row["total"], "converted": row["converted"]}
                    for row in series
                ],
            )
        except ConsoleProblem as problem:
            return self._problem(problem)
        except (DBAPIError, SecurityDependencyUnavailable):
            return self._problem(ConsoleProblem(503, "DEPENDENCY_UNAVAILABLE", "Analytics are unavailable."))

    def integrations(self) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-integrations-read")
            with self._connection(actor) as connection:
                rows = list(
                    connection.execute(
                        sa.text(
                            "select provider,status,count(*) count,max(updated_at) last_activity_at "
                            "from pending_provider_updates "
                            "where tenant_id=:tenant_id group by provider,status order by provider,status"
                        ),
                        {"tenant_id": actor.tenant_id},
                    ).mappings()
                )
            mcp_status = self._mcp_status()
            providers: dict[str, dict[str, Any]] = {}
            for row in rows:
                item = providers.setdefault(
                    str(row["provider"]), {"provider": row["provider"], "counts": {}, "last_activity_at": None}
                )
                item["counts"][str(row["status"]).lower()] = row["count"]
                item["last_activity_at"] = self._timestamp(row["last_activity_at"])
            return jsonify(mcp={"status": mcp_status}, providers=list(providers.values()))
        except ConsoleProblem as problem:
            return self._problem(problem)

    def system(self) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-system-read")
            checks: dict[str, str] = {}
            try:
                with self._connection(actor) as connection:
                    connection.execute(sa.text("select 1"))
                checks["postgres"] = "ok"
            except DBAPIError:
                checks["postgres"] = "unavailable"
            try:
                self.dependencies.redis_client.ping()
                checks["redis"] = "ok"
            except redis.RedisError:
                checks["redis"] = "unavailable"
            checks["mcp"] = self._mcp_status()
            status = "ok" if all(value in {"ok", "available"} for value in checks.values()) else "degraded"
            return jsonify(status=status, checks=checks, service="knotic-api", schema_revision="20260906_0013")
        except ConsoleProblem as problem:
            return self._problem(problem)

    def pending_work(self) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-pending-read")
            with self._connection(actor) as connection:
                rows = list(
                    connection.execute(
                        sa.text(
                            "select id,provider,action,aggregate_type,aggregate_id,status,attempt_count,"
                            "next_attempt_at,safe_error_code,updated_at from pending_provider_updates "
                            "where tenant_id=:tenant_id order by updated_at desc,id desc limit 100"
                        ),
                        {"tenant_id": actor.tenant_id},
                    ).mappings()
                )
            return jsonify(items=[self._json_row(row) for row in rows])
        except ConsoleProblem as problem:
            return self._problem(problem)

    def retry_work(self, work_id: str) -> ResponseReturnValue:
        try:
            actor, _ = self._authorize(action="console-pending-retry", mutation=True, roles=_ADMIN_ROLES)
            parsed = self._uuid7(work_id)
            self._idempotency_header()
            with self._connection(actor) as connection:
                row = (
                    connection.execute(
                        sa.text(
                            "update pending_provider_updates set "
                            "status='PENDING',next_attempt_at=timezone('utc',now()),"
                            "lease_owner=null,lease_expires_at=null,updated_at=timezone('utc',now()) "
                            "where tenant_id=:tenant_id and id=:id "
                            "and status in ('FAILED_RETRYABLE','DEAD_LETTER') "
                            "returning id,status,updated_at"
                        ),
                        {"tenant_id": actor.tenant_id, "id": parsed},
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                raise ConsoleProblem(409, "WORK_NOT_RETRYABLE", "Only failed retryable work can be retried.")
            return jsonify(id=str(row["id"]), status="queued", updated_at=self._timestamp(row["updated_at"])), 202
        except ConsoleProblem as problem:
            return self._problem(problem)

    def _authorize(
        self, *, action: str, mutation: bool = False, roles: frozenset[str] = _OPERATOR_ROLES
    ) -> tuple[AuthenticatedActor, BrowserSessionValue]:
        authenticated = self.dependencies.browser_sessions.authenticate(request.cookies.get("knotic_session"))
        if authenticated is None:
            raise ConsoleProblem(401, "AUTHENTICATION_REQUIRED", "Authentication is required.")
        actor, session = authenticated
        if not roles.intersection(actor.roles):
            raise ConsoleProblem(403, "PERMISSION_DENIED", "This operator role cannot access the resource.")
        if mutation:
            if request.headers.get("Origin") not in self.dependencies.allowed_origins:
                raise ConsoleProblem(403, "ORIGIN_DENIED", "Request origin is not allowed.")
            if not self.dependencies.browser_sessions.verify_csrf(session, request.headers.get("X-CSRF-Token")):
                raise ConsoleProblem(403, "CSRF_FAILED", "CSRF validation failed.")
        decision = self.dependencies.rate_limiter.check(actor, action=action, limit=120)
        if not decision.allowed:
            raise ConsoleProblem(429, "RATE_LIMITED", "Rate limit exceeded.")
        return actor, session

    @contextmanager
    def _connection(self, actor: AuthenticatedActor) -> Iterator[Connection]:
        with self.dependencies.engine.begin() as connection:
            connection.execute(
                sa.text("select set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(actor.tenant_id)},
            )
            connection.execute(
                sa.text("select set_config('app.actor_id',:actor,true)"),
                {"actor": str(actor.actor_id)},
            )
            yield connection

    def _session_summary(self, row: RowMapping) -> dict[str, Any]:
        name, company = None, None
        if row.get("lead_id"):
            name = self._decrypt(row.get("name_ciphertext"), row["tenant_id"], row["lead_id"], "name")
            company = self._decrypt(row.get("company_ciphertext"), row["tenant_id"], row["lead_id"], "company")
        return {
            "session_id": str(row["id"]),
            "version": row["version"],
            "status": row["status"],
            "customer": {"name": name or "Anonymous prospect", "company": company},
            "current_intent": row["current_intent"],
            "buying_stage": row["buying_stage"] or "NURTURE",
            "qualification_score": row["qualification_score"] or 0,
            "next_best_action": row["next_best_action"],
            "outcome": row["outcome"],
            "started_at": self._timestamp(row["started_at"]),
            "updated_at": self._timestamp(row["updated_at"]),
            "ended_at": self._timestamp(row["ended_at"]) if row["ended_at"] else None,
        }

    def _message(self, tenant_id: UUID, row: RowMapping) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "speaker": row["speaker"],
            "source": row["source"],
            "content": self._decrypt(row["content_ciphertext"], tenant_id, row["id"], "message_content")
            or "[Content unavailable]",
            "interrupted": row["interrupted"],
            "created_at": self._timestamp(row["created_at"]),
        }

    @staticmethod
    def _requirement(row: RowMapping) -> dict[str, Any]:
        value = next(
            (
                row[key]
                for key in ("value_integer", "value_text", "value_text_array", "value_numeric")
                if row[key] is not None
            ),
            None,
        )
        return {
            "field": row["field"],
            "value": value,
            "currency": row["currency"],
            "confidence": float(row["confidence"]),
            "version": row["version"],
        }

    def _objection(self, tenant_id: UUID, row: RowMapping) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "category": row["category"],
            "status": row["status"],
            "detail": self._decrypt(row["detail_ciphertext"], tenant_id, row["id"], "objection_detail")
            or "Details unavailable",
            "version": row["version"],
        }

    @staticmethod
    def _session_operations(connection: Any, tenant_id: UUID, session_id: UUID) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, query in {
            "meetings": (
                "select id,status,provider,scheduled_at,created_at from meetings "
                "where tenant_id=:tenant_id and session_id=:session_id order by created_at desc"
            ),
            "followups": (
                "select id,status,channel,scheduled_at,created_at from followups "
                "where tenant_id=:tenant_id and session_id=:session_id order by created_at desc"
            ),
            "handoffs": (
                "select id,status,reason,priority,assigned_agent_id,created_at from handoffs "
                "where tenant_id=:tenant_id and session_id=:session_id order by created_at desc"
            ),
        }.items():
            rows = connection.execute(sa.text(query), {"tenant_id": tenant_id, "session_id": session_id}).mappings()
            result[name] = [ConsoleApi._json_row(row) for row in rows]
        return result

    def _decrypt(self, value: bytes | None, tenant_id: UUID, aggregate_id: UUID, field: str) -> str | None:
        if value is None:
            return None
        try:
            return self._field_cipher.decrypt(value, tenant_id=tenant_id, aggregate_id=aggregate_id, field=field)
        except StateRecoveryError:
            self.app.logger.warning("console projection could not decrypt %s", field)
            return None

    def _mcp_status(self) -> str:
        try:
            with urlopen(f"{self.dependencies.settings.mcp_base_url}/health/ready", timeout=2) as response:  # noqa: S310
                return "available" if response.status == 200 else "degraded"
        except Exception:
            return "unavailable"

    @staticmethod
    def _json_row(row: RowMapping) -> dict[str, Any]:
        return {key: ConsoleApi._json_value(value) for key, value in row.items()}

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return ConsoleApi._timestamp(value)
        if isinstance(value, Decimal):
            return float(value)
        return value

    @staticmethod
    def _handoff_resource(row: RowMapping) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "status": str(row["status"]).lower(),
            "priority": row["priority"],
            "created_at": ConsoleApi._timestamp(row["created_at"]),
        }

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _uuid7(value: str) -> UUID:
        try:
            parsed = UUID(value)
        except ValueError as error:
            raise ConsoleProblem(404, "RESOURCE_NOT_FOUND", "Resource was not found.") from error
        if parsed.version != 7:
            raise ConsoleProblem(404, "RESOURCE_NOT_FOUND", "Resource was not found.")
        return parsed

    @staticmethod
    def _integer_arg(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(request.args.get(name, default))
        except ValueError as error:
            raise ConsoleProblem(422, "VALIDATION_FAILED", f"{name} must be an integer.") from error
        if not minimum <= value <= maximum:
            raise ConsoleProblem(422, "VALIDATION_FAILED", f"{name} is out of range.")
        return value

    @staticmethod
    def _json(model: type[BaseModel]) -> Any:
        try:
            return model.model_validate(request.get_json())
        except (ValidationError, TypeError) as error:
            raise ConsoleProblem(422, "VALIDATION_FAILED", "Request body is invalid.") from error

    @staticmethod
    def _idempotency_header() -> str:
        value = request.headers.get("Idempotency-Key", "")
        if not 16 <= len(value) <= 128 or not value.isascii():
            raise ConsoleProblem(400, "IDEMPOTENCY_KEY_REQUIRED", "A valid Idempotency-Key is required.")
        return value

    @staticmethod
    def _encode_cursor(row: RowMapping) -> str:
        body = json.dumps([ConsoleApi._timestamp(row["updated_at"]), str(row["id"])], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(body).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(value: str | None) -> tuple[datetime, UUID] | None:
        if not value:
            return None
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            at, identifier = json.loads(raw)
            return datetime.fromisoformat(at.replace("Z", "+00:00")), UUID(identifier)
        except Exception as error:
            raise ConsoleProblem(422, "VALIDATION_FAILED", "Cursor is invalid.") from error

    @staticmethod
    def _problem(problem: ConsoleProblem) -> tuple[Any, int]:
        return jsonify(error={"code": problem.code, "message": problem.message}), problem.status


def register_console_api(app: Flask, dependencies: ConsoleDependencies) -> None:
    ConsoleApi(app, dependencies).register()
