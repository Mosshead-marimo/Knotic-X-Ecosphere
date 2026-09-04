"""Append-only, redacted MCP audit records and deterministic approval checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .contracts import ToolEnvelope, ToolInvocation, TrustedContext

_SENSITIVE = re.compile(r"(?:authorization|cookie|secret|token|password|credential|api[_-]?key)", re.I)


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SENSITIVE.search(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value[:4096] + "…[TRUNCATED]" if isinstance(value, str) and len(value) > 4096 else value


@dataclass(frozen=True, slots=True)
class AuditRecord:
    occurred_at: datetime
    tool_call_id: str
    tenant_id: str
    actor_id: str
    session_id: str | None
    correlation_id: str
    tool: str
    version: int
    request_hash: str
    idempotency_key_hmac: str | None
    approval_decision: str
    latency_ms: int | None
    status: str
    error_code: str | None
    metadata: dict[str, object]
    cache_status: str = "MISS"


class AuditSink(Protocol):
    def append(self, record: AuditRecord) -> None: ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)


class ApprovalPolicy:
    def __init__(self, *, hmac_key: bytes) -> None:
        self._hmac_key = hmac_key

    def decide(self, *, level: str, context: TrustedContext, invocation: ToolInvocation) -> str:
        if level == "NONE":
            return "NOT_REQUIRED"
        if not context.approval_reference:
            return "REQUIRED"
        expected = canonical_hash({"tool": invocation.tool, "arguments": invocation.arguments})[:24]
        return "APPROVED" if hmac.compare_digest(context.approval_reference, expected) else "DENIED"

    def idempotency_hmac(self, key: str | None) -> str | None:
        return hmac.new(self._hmac_key, key.encode(), hashlib.sha256).hexdigest() if key else None


def audit_record(
    *,
    context: TrustedContext,
    invocation: ToolInvocation,
    envelope: ToolEnvelope | None,
    approval_decision: str,
    latency_ms: int | None,
    idempotency_key_hmac: str | None,
    cache_status: str = "MISS",
) -> AuditRecord:
    return AuditRecord(
        occurred_at=datetime.now(UTC),
        tool_call_id=str(invocation.tool_call_id),
        tenant_id=str(context.tenant_id),
        actor_id=str(context.actor_id),
        session_id=str(context.session_id) if context.session_id else None,
        correlation_id=str(context.correlation_id),
        tool=invocation.tool,
        version=invocation.version,
        request_hash=canonical_hash(invocation.arguments),
        idempotency_key_hmac=idempotency_key_hmac,
        approval_decision=approval_decision,
        latency_ms=latency_ms,
        status=envelope.status.value if envelope else "REJECTED",
        error_code=envelope.error.code if envelope and envelope.error else None,
        metadata=redact({"arguments": invocation.arguments, "data": envelope.data if envelope else None}),  # type: ignore[arg-type]
        cache_status=cache_status,
    )
