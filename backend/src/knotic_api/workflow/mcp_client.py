"""Typed, bounded MCP client used exclusively by workflow boundary nodes."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError


# The closed set of logical MCP tools this workload may ever invoke, mirroring
# docs/contracts/mcp-tools.v1.json. A tool name is never built from model or customer text, but
# this allowlist is enforced here too as an independent, defense-in-depth boundary: even a
# defect elsewhere that let untrusted data influence a tool name cannot reach the network.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "pricing.get_quote",
        "pricing.compare_plans",
        "lead.qualify",
        "lead.next_action",
        "followup.create",
        "knowledge.search",
        "product.search",
        "product.get_feature",
        "product.get_integration",
        "competitor.compare",
        "security.get_information",
        "crm.get_lead",
        "crm.create_lead",
        "crm.update_lead",
        "crm.add_note",
        "crm.add_call_summary",
        "calendar.get_slots",
        "calendar.book_meeting",
        "handoff.request_agent",
        "handoff.transfer_context",
    }
)


# Least-privilege scope for each allowlisted tool. A caller should request only the scopes
# covering the tools it actually intends to call this turn (see ``least_privilege_scopes``),
# never a blanket grant of every scope the workload identity is capable of holding.
TOOL_SCOPES: dict[str, str] = {
    "pricing.get_quote": "sales:read",
    "pricing.compare_plans": "sales:read",
    "lead.qualify": "sales:read",
    "lead.next_action": "sales:read",
    "followup.create": "followup:write",
    "knowledge.search": "knowledge:read",
    "product.search": "knowledge:read",
    "product.get_feature": "knowledge:read",
    "product.get_integration": "knowledge:read",
    "competitor.compare": "knowledge:read",
    "security.get_information": "knowledge:read",
    "crm.get_lead": "crm:read",
    "crm.create_lead": "crm:write",
    "crm.update_lead": "crm:write",
    "crm.add_note": "crm:write",
    "crm.add_call_summary": "crm:write",
    "calendar.get_slots": "calendar:read",
    "calendar.book_meeting": "calendar:write",
    "handoff.request_agent": "handoff:write",
    "handoff.transfer_context": "handoff:write",
}


def least_privilege_scopes(tools: Iterable[str]) -> frozenset[str]:
    """The minimal scope set covering exactly the named tools, and nothing else.

    Raises for any tool outside the allowlist so a typo or an unreviewed new tool name cannot
    silently grant a scope; callers should build every ``McpCallContext.scopes`` from this
    rather than a broad static set.
    """
    unknown = sorted(set(tools) - set(TOOL_SCOPES))
    if unknown:
        raise ValueError(f"cannot grant scope for tools outside the allowlist: {unknown}")
    return frozenset(TOOL_SCOPES[tool] for tool in tools)


class McpToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool = False


class McpToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: UUID
    tool: str
    version: int
    status: str
    data: dict[str, Any] | None = None
    error: McpToolError | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.status == "SUCCEEDED" and (self.data is None or self.error is not None):
            raise ValueError("successful MCP results require validated data")
        if self.status != "SUCCEEDED" and self.error is None:
            raise ValueError("non-success MCP results require a safe error")


@dataclass(frozen=True, slots=True)
class McpCallContext:
    tenant_id: UUID
    actor_id: UUID
    correlation_id: UUID
    scopes: frozenset[str]
    session_id: UUID | None = None
    turn_id: UUID | None = None

    def headers(self, deadline: datetime) -> str:
        return json.dumps(
            {
                "tenant_id": str(self.tenant_id),
                "actor_id": str(self.actor_id),
                "session_id": str(self.session_id) if self.session_id else None,
                "turn_id": str(self.turn_id) if self.turn_id else None,
                "correlation_id": str(self.correlation_id),
                "scopes": sorted(self.scopes),
                "deadline_at": deadline.isoformat(),
            },
            separators=(",", ":"),
        )


class McpClient(Protocol):
    def call(
        self,
        *,
        context: McpCallContext,
        tool_call_id: UUID,
        tool: str,
        arguments: dict[str, Any],
        deadline: datetime,
        idempotency_key: str | None = None,
    ) -> McpToolResult: ...


class HttpMcpClient:
    def __init__(self, *, base_url: str, auth_token: str, max_attempts: int = 2) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP base_url must be an absolute HTTP(S) URL")
        if not auth_token:
            raise ValueError("MCP auth_token is required")
        if not 1 <= max_attempts <= 2:
            raise ValueError("MCP max_attempts must be between 1 and 2")
        self._base_url, self._auth_token, self._max_attempts = base_url.rstrip("/"), auth_token, max_attempts
        self._failures, self._open_until = 0, 0.0

    def call(
        self,
        *,
        context: McpCallContext,
        tool_call_id: UUID,
        tool: str,
        arguments: dict[str, Any],
        deadline: datetime,
        idempotency_key: str | None = None,
    ) -> McpToolResult:
        if tool not in ALLOWED_TOOLS:
            return self._failure(tool_call_id, tool, "POLICY_DENIED", "This tool is not on the allowlist.", False)
        if deadline <= datetime.now(UTC):
            return self._failure(tool_call_id, tool, "TIMEOUT", "The tool deadline has expired.", False)
        if self._open_until > time.monotonic():
            return self._failure(tool_call_id, tool, "DEPENDENCY_UNAVAILABLE", "The MCP circuit is open.", True)
        payload = {
            "tool_call_id": str(tool_call_id),
            "tool": tool,
            "version": 1,
            "arguments": arguments,
            "idempotency_key": idempotency_key,
        }
        attempts = 1 if idempotency_key else self._max_attempts
        for attempt in range(attempts):
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            request = Request(  # noqa: S310 - base_url is validated as absolute HTTP(S) in __init__
                f"{self._base_url}/v1/tools/{tool}",
                data=json.dumps(payload, separators=(",", ":")).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._auth_token}",
                    "X-Knotic-Trusted-Context": context.headers(deadline),
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=min(remaining, 10)) as response:  # noqa: S310 - validated private base URL
                    result = McpToolResult.model_validate_json(response.read())
                self._failures = 0
                return result
            except (HTTPError, URLError, TimeoutError, ValidationError, ValueError):
                if attempt + 1 < attempts:
                    time.sleep(min(0.05 * (2**attempt), max(remaining, 0)))
        self._failures += 1
        if self._failures >= 3:
            self._open_until = time.monotonic() + 15
        return self._failure(tool_call_id, tool, "DEPENDENCY_UNAVAILABLE", "MCP did not return a valid result.", True)

    @staticmethod
    def _failure(tool_call_id: UUID, tool: str, code: str, message: str, retryable: bool) -> McpToolResult:
        return McpToolResult(
            tool_call_id=tool_call_id,
            tool=tool,
            version=1,
            status="FAILED",
            error=McpToolError(code=code, message=message, retryable=retryable),
        )


def bounded_deadline(seconds: float = 2.5) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)
