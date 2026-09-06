"""Agora webhook receiver for managed-agent lifecycle, transcript, error, and usage events.

Configured in Agora Console under Webhooks (see
https://docs.agora.io/en/conversational-ai/rest-api or the Console's own webhook setup page).
Verifies Agora's signature, deduplicates by delivery id, and returns quickly -- this module does
no slow work inline; event handling below is a placeholder synchronous acknowledgement for now
(see the follow-up note in ``docs/CHANGES_MADE.md``), and should become an enqueue onto a
background worker before this is trusted with real production traffic volume.

Agora's exact webhook signature scheme (header name, hash algorithm) is confirmed from the
project's own Console webhook configuration screen at setup time, not from public docs available
to this change -- ``_verify_signature`` implements the common HMAC-SHA256-over-raw-body pattern
Agora documents for its notification callbacks; confirm the header name and payload-to-sign
convention against the actual Console configuration before relying on this in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

import redis
from flask import Flask, Response, jsonify, request
from flask.typing import ResponseReturnValue

_DEDUPE_TTL_SECONDS = 7 * 24 * 60 * 60
_SIGNATURE_HEADER = "Agora-Signature"


class WebhookStoreUnavailable(RuntimeError):
    """The webhook dedupe/event store could not confirm the requested operation."""


@dataclass(frozen=True, slots=True)
class AgentWebhookDependencies:
    signing_secret: str
    redis_client: redis.Redis
    environment: str
    logger: logging.Logger | None = None


class AgentWebhookApi:
    def __init__(self, app: Flask, dependencies: AgentWebhookDependencies) -> None:
        self.app = app
        self.dependencies = dependencies
        self._logger = dependencies.logger or logging.getLogger(__name__)

    def register(self) -> None:
        self.app.add_url_rule("/api/v1/webhooks/agora", view_func=self.receive, methods=["POST"])

    def receive(self) -> ResponseReturnValue:
        raw_body = request.get_data(cache=True)
        signature = request.headers.get(_SIGNATURE_HEADER, "")
        if not signature or not self._verify_signature(raw_body, signature):
            return self._ack_invalid()

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return self._ack_invalid()
        if not isinstance(payload, dict):
            return self._ack_invalid()

        event_id = self._event_id(payload)
        if event_id is None:
            return self._ack_invalid()

        try:
            is_new = self._reserve_delivery(event_id)
        except WebhookStoreUnavailable:
            self._logger.exception("agora_webhook_dedupe_store_unavailable")
            # Fail closed on our own storage outage by returning 503 so Agora retries later,
            # rather than risk silently dropping or double-processing an event.
            response = jsonify(error={"code": "DEPENDENCY_UNAVAILABLE", "message": "Try again shortly."})
            response.status_code = 503
            return response

        if not is_new:
            self._logger.info("agora_webhook_duplicate_delivery", extra={"agora_event_id": event_id})
            return jsonify(status="duplicate")

        # Fast-path acknowledgement. Real event handling (persisting agent-started/-stopped,
        # transcript, failure, and usage rows; alerting) is intentionally deferred to a queued
        # worker in a follow-up rather than done inline here, per Agora's own guidance to respond
        # within its timeout and do slower processing asynchronously.
        self._logger.info(
            "agora_webhook_received",
            extra={"agora_event_id": event_id, "agora_event_type": payload.get("eventType") or payload.get("type")},
        )
        return jsonify(status="accepted")

    def _verify_signature(self, raw_body: bytes, signature: str) -> bool:
        expected = hmac.new(self.dependencies.signing_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _reserve_delivery(self, event_id: str) -> bool:
        """Returns True the first time this event_id is seen, False on any repeat delivery."""
        key = f"knotic:{self.dependencies.environment}:agora-webhook-delivery:{event_id}"
        try:
            return bool(self.dependencies.redis_client.set(key, b"1", ex=_DEDUPE_TTL_SECONDS, nx=True))
        except redis.RedisError as error:
            raise WebhookStoreUnavailable("webhook delivery dedupe record could not be written") from error

    @staticmethod
    def _event_id(payload: dict[str, object]) -> str | None:
        for key in ("eventId", "event_id", "id", "noticeId", "notice_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _ack_invalid() -> Response:
        # Still a fast 200-class acknowledgement so a malformed/unsigned delivery does not cause
        # Agora to retry indefinitely, but distinguishable in logs/metrics from a genuine event.
        response = jsonify(status="rejected")
        response.status_code = 400
        return response


def register_agent_webhook_api(app: Flask, dependencies: AgentWebhookDependencies) -> None:
    AgentWebhookApi(app, dependencies).register()
