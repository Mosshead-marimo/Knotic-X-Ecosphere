import io
import os
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from knotic_config import MappingSecretProvider, SecretResolutionError, configuration_error_summary
from pydantic import ValidationError

from knotic_mcp.__main__ import main
from knotic_mcp.config import load_mcp_settings

VALID_SECRETS = {
    "KNOTIC_DATABASE_URL": "postgresql://knotic:secure-password@db.internal:5432/knotic",
    "KNOTIC_REDIS_URL": "rediss://:secure-redis-password@redis.internal:6379/0",
    "KNOTIC_MCP_AUTH_TOKEN": "a" * 40,
}
VALID_ENVIRONMENT = {
    "KNOTIC_ENV": "development",
    "KNOTIC_LOG_LEVEL": "INFO",
    "KNOTIC_DEBUG": "false",
    "KNOTIC_MCP_ALLOWED_HOSTS": '["127.0.0.1"]',
}


class McpSettingsTests(unittest.TestCase):
    def test_loads_valid_settings_and_masks_secrets(self) -> None:
        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True):
            settings = load_mcp_settings(secret_provider=MappingSecretProvider(VALID_SECRETS))

        self.assertEqual(settings.port, 8090)
        self.assertNotIn(VALID_SECRETS["KNOTIC_MCP_AUTH_TOKEN"], repr(settings))

    def test_missing_secrets_fail_before_model_loading(self) -> None:
        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True):
            with self.assertRaises(SecretResolutionError) as context:
                load_mcp_settings(secret_provider=MappingSecretProvider({}))

        self.assertIn("KNOTIC_MCP_AUTH_TOKEN", context.exception.missing_names)

    def test_malformed_secret_error_does_not_echo_value(self) -> None:
        bad_secret = "redis://short"
        secrets = {**VALID_SECRETS, "KNOTIC_REDIS_URL": bad_secret}

        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True):
            with self.assertRaises(ValidationError) as context:
                load_mcp_settings(secret_provider=MappingSecretProvider(secrets))

        self.assertNotIn(bad_secret, configuration_error_summary(context.exception))

    def test_production_requires_explicit_non_local_hosts(self) -> None:
        environment = {**VALID_ENVIRONMENT, "KNOTIC_ENV": "production"}

        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ValidationError):
                load_mcp_settings(secret_provider=MappingSecretProvider(VALID_SECRETS))

    def test_process_entrypoint_fails_fast_with_safe_error(self) -> None:
        stderr = io.StringIO()
        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as context:
                main()

        self.assertEqual(context.exception.code, 78)
        self.assertIn("KNOTIC_DATABASE_URL", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

