"""Scheduled reconciliation for orphaned Agora managed-agent instances.

The browser's own "Hang Up" button already stops the agent via ``voice_api.stop_agent`` (a tab
crash or lost connectivity never runs that code, though), and Agora's own ``idle_timeout`` stops
an agent once every subscribed customer UID leaves the RTC channel -- but neither covers a call
whose *session* was independently ended (support workflow, privacy erasure, an operator action)
while the agent is still running, or an agent stuck past a configured maximum call duration. This
module is the safety net for both: run it on a schedule (cron, a Kubernetes CronJob, or any
periodic-task runner) via ``python -m knotic_api.voice.agent_cleanup`` or the ``knotic-agent-
cleanup`` console script.

Kept dependency-light and side-effect-explicit (a single ``reconcile_once`` function) so it is
easy to schedule with whatever job runner a given deployment already uses, rather than this
codebase inventing its own scheduler.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import redis
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from ..config import load_backend_settings
from .managed_agent import ManagedAgentConfiguration, ManagedAgentService, ManagedAgentUnavailable

_MAX_CALL_DURATION = timedelta(hours=2)
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 2.0

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    checked: int
    stopped_ended_session: int
    stopped_timed_out: int
    stop_failures: int


def _session_has_ended(engine: Engine, *, tenant_id: UUID, session_id: UUID) -> bool:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("select status from sales_sessions where tenant_id = :tenant_id and id = :session_id"),
            {"tenant_id": tenant_id, "session_id": session_id},
        ).first()
    if row is None:
        # The session row is gone entirely (hard-deleted by an erasure workflow); treat that the
        # same as ended rather than leaving an agent attached to nothing.
        return True
    return str(row[0]) in {"ENDED", "ABANDONED"}


def _stop_with_retries(service: ManagedAgentService, *, tenant_id: UUID, session_id: UUID, agent_id: str) -> bool:
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            service.stop(tenant_id=tenant_id, session_id=session_id)
            return True
        except ManagedAgentUnavailable:
            if attempt == _RETRY_ATTEMPTS:
                _logger.error(
                    "agora_agent_cleanup_could_not_stop_agent",
                    extra={
                        "agora_tenant_id": str(tenant_id),
                        "agora_session_id": str(session_id),
                        "agora_agent_id": agent_id,
                    },
                )
                return False
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    return False


def reconcile_once(
    *,
    engine: Engine,
    service: ManagedAgentService,
    now: datetime | None = None,
) -> ReconciliationResult:
    now = now or datetime.now(UTC)
    handles = service.list_active()
    stopped_ended_session = 0
    stopped_timed_out = 0
    stop_failures = 0
    for handle in handles:
        try:
            tenant_id = UUID(str(handle["tenant_id"]))
            session_id = UUID(str(handle["session_id"]))
            agent_id = str(handle.get("agent_id", ""))
            started_at = datetime.fromisoformat(str(handle["started_at"]))
        except (KeyError, ValueError):
            continue
        ended = _session_has_ended(engine, tenant_id=tenant_id, session_id=session_id)
        timed_out = now - started_at > _MAX_CALL_DURATION
        if not ended and not timed_out:
            continue
        if _stop_with_retries(service, tenant_id=tenant_id, session_id=session_id, agent_id=agent_id):
            if ended:
                stopped_ended_session += 1
            else:
                stopped_timed_out += 1
        else:
            stop_failures += 1
    return ReconciliationResult(
        checked=len(handles),
        stopped_ended_session=stopped_ended_session,
        stopped_timed_out=stopped_timed_out,
        stop_failures=stop_failures,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_backend_settings()
    if not settings.managed_agent_configured:
        _logger.info("agora_agent_cleanup_skipped_not_configured")
        return

    engine = sa.create_engine(
        settings.database_url.get_secret_value().replace("postgresql://", "postgresql+psycopg://", 1),
        pool_pre_ping=True,
    )
    redis_client = redis.Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=False)
    assert settings.agora_customer_id is not None  # noqa: S101 - narrows Optional after the configured-check above
    assert settings.agora_customer_secret is not None  # noqa: S101
    assert settings.agora_llm_url is not None  # noqa: S101
    assert settings.agora_llm_api_key is not None  # noqa: S101
    service = ManagedAgentService(
        ManagedAgentConfiguration(
            app_id=settings.agora_app_id,
            app_certificate=settings.agora_app_certificate.get_secret_value(),
            customer_id=settings.agora_customer_id.get_secret_value(),
            customer_secret=settings.agora_customer_secret.get_secret_value(),
            openai_api_key=settings.openai_api_key.get_secret_value() if settings.openai_api_key else None,
            llm_url=settings.agora_llm_url,
            llm_api_key=settings.agora_llm_api_key.get_secret_value(),
            session_security_key=settings.session_security_key.get_secret_value().encode(),
            api_base_url=settings.agora_agent_api_url,
        ),
        redis_client,
        environment=settings.environment.value,
    )
    result = reconcile_once(engine=engine, service=service)
    _logger.info(
        "agora_agent_cleanup_completed",
        extra={
            "checked": result.checked,
            "stopped_ended_session": result.stopped_ended_session,
            "stopped_timed_out": result.stopped_timed_out,
            "stop_failures": result.stop_failures,
        },
    )
    if result.stop_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
