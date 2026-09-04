from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knotic_api.domain import new_uuid7
from knotic_api.workflow import ALLOWED_TOOLS, TOOL_SCOPES, HttpMcpClient, McpCallContext, least_privilege_scopes


def test_allowed_tools_and_tool_scopes_cover_the_same_canonical_set() -> None:
    assert set(ALLOWED_TOOLS) == set(TOOL_SCOPES)
    assert len(ALLOWED_TOOLS) == 20


def test_client_rejects_a_tool_outside_the_allowlist_without_a_network_call() -> None:
    client = HttpMcpClient(base_url="https://mcp.internal:8090", auth_token="a" * 40)
    context = McpCallContext(
        tenant_id=new_uuid7(), actor_id=new_uuid7(), correlation_id=new_uuid7(), scopes=frozenset({"knowledge:read"})
    )
    result = client.call(
        context=context,
        tool_call_id=new_uuid7(),
        tool="sql.execute_raw",
        arguments={},
        deadline=datetime.now(UTC) + timedelta(seconds=5),
    )
    assert result.status == "FAILED"
    assert result.error is not None
    assert result.error.code == "POLICY_DENIED"
    assert result.error.retryable is False


def test_least_privilege_scopes_grants_only_the_named_tools() -> None:
    scopes = least_privilege_scopes(["knowledge.search", "pricing.get_quote"])
    assert scopes == frozenset({"knowledge:read", "sales:read"})
    assert "crm:write" not in scopes


def test_least_privilege_scopes_rejects_a_tool_outside_the_allowlist() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        least_privilege_scopes(["knowledge.search", "sql.execute_raw"])


def test_least_privilege_scopes_never_grants_more_than_the_full_allowlist_union() -> None:
    assert least_privilege_scopes(ALLOWED_TOOLS) == frozenset(TOOL_SCOPES.values())
