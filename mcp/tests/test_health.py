import unittest

from pydantic import SecretStr

from knotic_mcp.app import create_app
from knotic_mcp.config import McpSettings


class McpHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_liveness_does_not_depend_on_downstream_services(self) -> None:
        settings = McpSettings(
            database_url=SecretStr("postgresql://user:password@db.internal/app"),
            redis_url=SecretStr("redis://:password@redis.internal/0"),
            mcp_auth_token=SecretStr("a" * 40),
        )
        app = create_app(settings)
        messages: list[dict[str, object]] = []

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        await app({"type": "http", "path": "/health/live"}, None, send)

        self.assertEqual(messages[0]["status"], 200)
        self.assertIn(b'"status":"ok"', messages[1]["body"])


if __name__ == "__main__":
    unittest.main()
