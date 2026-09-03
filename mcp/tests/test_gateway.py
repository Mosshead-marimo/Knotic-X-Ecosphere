from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr

from knotic_mcp.app import create_app
from knotic_mcp.config import McpSettings

IDENTIFIERS = {
    "tenant_id": "0193a2d7-1000-7000-8000-000000000001",
    "actor_id": "0193a2d7-1000-7000-8000-000000000002",
    "correlation_id": "0193a2d7-1000-7000-8000-000000000003",
}


async def _request(app: object, *, path: str, headers: dict[str, str], body: dict[str, object]) -> tuple[int, dict[str, object]]:
    messages: list[dict[str, object]] = []
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app({"type": "http", "method": "POST", "path": path, "headers": [(key.encode(), value.encode()) for key, value in headers.items()]}, receive, send)  # type: ignore[misc]
    return int(messages[0]["status"]), json.loads(messages[1]["body"])


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_rejects_unknown_tool_and_invalid_arguments(self) -> None:
        token = "a" * 40
        app = create_app(
            McpSettings(
                database_url=SecretStr("postgresql://user:password@db.internal/app"),
                redis_url=SecretStr("redis://:password@redis.internal/0"),
                auth_token=SecretStr(token),
            )
        )
        context = {
            **IDENTIFIERS,
            "scopes": ["knowledge:read"],
            "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        headers = {"authorization": f"Bearer {token}", "x-knotic-trusted-context": json.dumps(context)}
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000004",
            "tool": "knowledge.search",
            "version": 1,
            "arguments": {"query": "SSO"},
        }
        status, payload = await _request(app, path="/v1/tools/knowledge.search", headers=headers, body=body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
        body["tool"] = "unknown.tool"
        status, payload = await _request(app, path="/v1/tools/unknown.tool", headers=headers, body=body)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")
