from __future__ import annotations

import logging

import pytest

from knotic_api.privacy import RetentionPolicy
from knotic_api.privacy_logging import SensitiveDataFilter


def test_sensitive_log_filter_redacts_secrets_identifiers_and_structured_extras() -> None:
    record = logging.LogRecord(
        name="knotic",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer abc.def.ghi email=ada@example.com postgresql://user:pass@db/service",
        args=(),
        exc_info=None,
    )
    record.payload = {"cookie": "opaque-cookie", "safe_code": "DEPENDENCY_UNAVAILABLE"}

    assert SensitiveDataFilter().filter(record)
    rendered = record.getMessage()
    assert "abc.def.ghi" not in rendered
    assert "ada@example.com" not in rendered
    assert "user:pass" not in rendered
    assert record.payload == "[REDACTED]"
    assert rendered.count("[REDACTED]") >= 3


def test_retention_policy_rejects_shorter_audit_or_business_windows() -> None:
    with pytest.raises(ValueError, match="cannot be shorter"):
        RetentionPolicy(
            policy_version="test-v1",
            session_content_days=365,
            business_record_days=300,
            event_days=400,
        )
