"""Logging redaction for credentials and security-sensitive fields."""

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import SecretStr

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|secret|token|password|passwd|certificate|private[_-]?key|api[_-]?key|database[_-]?url|redis[_-]?url)",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_BASIC_VALUE = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]+")
_URL_PASSWORD = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/\s]+):(?P<password>[^@\s]+)@", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"(?i)(?P<key>(?:secret|token|password|passwd|certificate|private[_-]?key|api[_-]?key))(?P<separator>\s*[:=]\s*)(?P<value>[^\s,;]+)"
)


def redact_text(value: str, exact_secrets: Iterable[str] = ()) -> str:
    """Redact common credential forms and exact resolved secret values."""

    redacted = value
    for secret in exact_secrets:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    redacted = _BEARER_VALUE.sub(f"Bearer {REDACTED}", redacted)
    redacted = _BASIC_VALUE.sub(f"Basic {REDACTED}", redacted)
    redacted = _URL_PASSWORD.sub(lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED}@", redacted)
    return _KEY_VALUE.sub(lambda match: f"{match.group('key')}{match.group('separator')}{REDACTED}", redacted)


def redact_value(value: Any, exact_secrets: Iterable[str] = (), *, key: str | None = None) -> Any:
    """Recursively redact structured logging values without changing shape."""

    secrets = tuple(exact_secrets)
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, Mapping):
        return {item_key: redact_value(item_value, secrets, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_value(item, secrets) for item in value)
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    return value


class RedactingFilter(logging.Filter):
    """Redact log records before a handler formats or emits them."""

    def __init__(self, exact_secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._exact_secrets = tuple(secret for secret in exact_secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_value(record.msg, self._exact_secrets)
        record.args = redact_value(record.args, self._exact_secrets)
        return True


def install_redaction(logger: logging.Logger, exact_secrets: Iterable[str] = ()) -> RedactingFilter:
    """Install one redaction filter on a logger and all existing handlers."""

    redaction_filter = RedactingFilter(exact_secrets)
    logger.addFilter(redaction_filter)
    for handler in logger.handlers:
        handler.addFilter(redaction_filter)
    return redaction_filter

