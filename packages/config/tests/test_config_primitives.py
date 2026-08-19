import logging
import unittest

from knotic_config import (
    REDACTED,
    MappingSecretProvider,
    RedactingFilter,
    SecretResolutionError,
    redact_text,
    redact_value,
)
from knotic_config.secrets import resolve_secrets


class SecretProviderTests(unittest.TestCase):
    def test_resolves_required_and_optional_secrets(self) -> None:
        provider = MappingSecretProvider({"REQUIRED": "secret-value", "OPTIONAL": "old-value"})

        resolved = resolve_secrets(provider, {"current": "REQUIRED"}, {"previous": "OPTIONAL"})

        self.assertEqual(resolved["current"].get_secret_value(), "secret-value")
        self.assertEqual(resolved["previous"].get_secret_value(), "old-value")

    def test_missing_secret_error_contains_names_only(self) -> None:
        with self.assertRaises(SecretResolutionError) as context:
            resolve_secrets(MappingSecretProvider({}), {"token": "REQUIRED_TOKEN"})

        self.assertEqual(context.exception.missing_names, ("REQUIRED_TOKEN",))
        self.assertNotIn("token-value", str(context.exception))


class RedactionTests(unittest.TestCase):
    def test_redacts_exact_bearer_and_url_credentials(self) -> None:
        value = "token=exact-secret Bearer abc.def postgresql://user:db-password@db.internal/app"

        redacted = redact_text(value, ("exact-secret",))

        self.assertNotIn("exact-secret", redacted)
        self.assertNotIn("abc.def", redacted)
        self.assertNotIn("db-password", redacted)
        self.assertIn(REDACTED, redacted)

    def test_recursively_redacts_sensitive_keys(self) -> None:
        value = {"customer": "safe", "authorization": "Bearer private", "nested": {"api_key": "key"}}

        redacted = redact_value(value)

        self.assertEqual(redacted["customer"], "safe")
        self.assertEqual(redacted["authorization"], REDACTED)
        self.assertEqual(redacted["nested"]["api_key"], REDACTED)

    def test_logging_filter_redacts_message_and_arguments(self) -> None:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "credential=%s", ("exact-secret",), None)

        self.assertTrue(RedactingFilter(("exact-secret",)).filter(record))

        self.assertNotIn("exact-secret", record.getMessage())


if __name__ == "__main__":
    unittest.main()
