import unittest

from pydantic import SecretStr

from knotic_api.app import create_app
from knotic_api.config import BackendSettings


class BackendHealthTests(unittest.TestCase):
    def test_liveness_does_not_depend_on_downstream_services(self) -> None:
        settings = BackendSettings(
            database_url=SecretStr("postgresql://user:password@db.internal/app"),
            redis_url=SecretStr("redis://:password@redis.internal/0"),
            mcp_auth_token=SecretStr("a" * 40),
            agora_app_certificate=SecretStr("b" * 32),
            KNOTIC_MCP_BASE_URL="http://mcp.internal:8090",
            KNOTIC_AGORA_APP_ID="0123456789abcdef0123456789abcdef",
        )
        client = create_app(settings).test_client()

        response = client.get("/api/v1/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
