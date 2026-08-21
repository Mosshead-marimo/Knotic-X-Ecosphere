import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from knotic_api.__main__ import main
from knotic_api.config import load_backend_settings
from knotic_config import MappingSecretProvider, SecretResolutionError, configuration_error_summary

VALID_SECRETS = {
    "KNOTIC_DATABASE_URL": "postgresql://knotic:secure-password@db.internal:5432/knotic",
    "KNOTIC_REDIS_URL": "rediss://:secure-redis-password@redis.internal:6379/0",
    "KNOTIC_MCP_AUTH_TOKEN": "a" * 40,
    "KNOTIC_AGORA_APP_CERTIFICATE": "b" * 32,
    "KNOTIC_SESSION_SECURITY_KEY": "c" * 32,
}
VALID_ENVIRONMENT = {
    "KNOTIC_ENV": "development",
    "KNOTIC_LOG_LEVEL": "INFO",
    "KNOTIC_DEBUG": "false",
    "KNOTIC_MCP_BASE_URL": "http://mcp.internal:8090",
    "KNOTIC_AGORA_APP_ID": "0123456789abcdef0123456789abcdef",
    "KNOTIC_ALLOWED_ORIGINS": '["http://localhost:3000"]',
}


class BackendSettingsTests(unittest.TestCase):
    def test_loads_valid_settings_and_masks_secrets(self) -> None:
        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True):
            settings = load_backend_settings(secret_provider=MappingSecretProvider(VALID_SECRETS))

        self.assertEqual(settings.port, 8080)
        self.assertEqual(settings.mcp_base_url, "http://mcp.internal:8090")
        self.assertNotIn(VALID_SECRETS["KNOTIC_MCP_AUTH_TOKEN"], repr(settings))

    def test_missing_secrets_fail_before_model_loading(self) -> None:
        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True):
            with self.assertRaises(SecretResolutionError) as context:
                load_backend_settings(secret_provider=MappingSecretProvider({}))

        self.assertIn("KNOTIC_DATABASE_URL", context.exception.missing_names)

    def test_environment_file_is_loaded_only_when_explicitly_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "development.env"
            env_file.write_text(
                "\n".join(f"{name}={value}" for name, value in VALID_ENVIRONMENT.items()),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                settings = load_backend_settings(
                    secret_provider=MappingSecretProvider(VALID_SECRETS),
                    env_file=env_file,
                )

        self.assertEqual(settings.environment.value, "development")
        self.assertEqual(settings.mcp_base_url, "http://mcp.internal:8090")

    def test_malformed_secret_error_does_not_echo_value(self) -> None:
        bad_secret = "mysql://user:do-not-print@database/app"
        secrets = {**VALID_SECRETS, "KNOTIC_DATABASE_URL": bad_secret}

        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True):
            with self.assertRaises(ValidationError) as context:
                load_backend_settings(secret_provider=MappingSecretProvider(secrets))

        self.assertNotIn(bad_secret, configuration_error_summary(context.exception))

    def test_production_rejects_debug_and_local_origins(self) -> None:
        environment = {
            **VALID_ENVIRONMENT,
            "KNOTIC_ENV": "production",
            "KNOTIC_DEBUG": "true",
            "KNOTIC_ALLOWED_ORIGINS": '["http://localhost:3000"]',
        }

        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ValidationError):
                load_backend_settings(secret_provider=MappingSecretProvider(VALID_SECRETS))

    def test_process_entrypoint_fails_fast_with_safe_error(self) -> None:
        stderr = io.StringIO()
        with patch.dict(os.environ, VALID_ENVIRONMENT, clear=True), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as context:
                main()

        self.assertEqual(context.exception.code, 78)
        self.assertIn("KNOTIC_DATABASE_URL", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
