"""Fail-safe redaction filter for operational logs."""

from __future__ import annotations

import logging
import re
from typing import Any

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(r"authorization|cookie|token|secret|password|transcript|prompt|payload", re.IGNORECASE)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_CONNECTION_CREDENTIAL = re.compile(r"(?i)\b(postgresql|redis|https?)://[^\s/@:]+:[^\s/@]+@")
_KEY_VALUE = re.compile(r"(?i)\b(authorization|cookie|token|secret|password)\b\s*[:=]\s*([^\s,;]+|\"[^\"]*\")")


class SensitiveDataFilter(logging.Filter):
    """Redact common secrets and identifiers before any handler emits a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _sanitize(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_sanitize(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _sanitize(value) for key, value in record.args.items()}
        for key, value in tuple(record.__dict__.items()):
            if _SENSITIVE_KEY.search(key):
                record.__dict__[key] = _REDACTED
            elif key not in _LOG_RECORD_FIELDS:
                record.__dict__[key] = _sanitize(value)
        return True


def install_sensitive_data_filter(logger: logging.Logger) -> None:
    """Install one redaction filter on the logger and every attached handler."""

    if any(isinstance(item, SensitiveDataFilter) for item in logger.filters):
        return
    redactor = SensitiveDataFilter()
    logger.addFilter(redactor)
    for handler in logger.handlers:
        handler.addFilter(redactor)


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        value = _BEARER.sub(lambda match: f"{match.group(1)} {_REDACTED}", value)
        value = _KEY_VALUE.sub(lambda match: f"{match.group(1)}={_REDACTED}", value)
        value = _CONNECTION_CREDENTIAL.sub(lambda match: f"{match.group(1)}://{_REDACTED}@", value)
        return _EMAIL.sub(_REDACTED, value)
    if isinstance(value, dict):
        return {key: _REDACTED if _SENSITIVE_KEY.search(str(key)) else _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    return value


_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
