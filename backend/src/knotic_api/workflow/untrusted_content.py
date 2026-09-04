"""Fail-closed defenses applied to every piece of text sourced from MCP tools.

Retrieved knowledge chunks and other tool payloads are customer- or vendor-controlled data,
never instructions. A poisoned document or tool result must never be able to redirect model
policy, exfiltrate data across tenants, or cause a side-effect tool call. This module is the
single place that decides whether a piece of untrusted, tool-sourced text is safe to carry
forward into graph state (as a ``GroundedFact``) or into a generated response.

Text that fails this check is dropped, not repaired: a document containing an injection
attempt is treated the same as a missing document, because a partially-sanitized quote from a
compromised source is not trustworthy evidence either.
"""

from __future__ import annotations

import re

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"ignore\s+(?:all|any|the)?\s*(?:previous|prior|above|earlier)\s+instructions",
        r"disregard\s+(?:all|any|the)?\s*(?:previous|prior|above|earlier)\s+instructions",
        r"forget\s+(?:all|any|the)?\s*(?:previous|prior|above|earlier)\s+instructions",
        r"you\s+are\s+now\b",
        r"\bnew\s+instructions?\s*[:.]",
        r"\bsystem\s*[:=]",
        r"\bassistant\s*[:=]",
        r"reveal\s+(?:the\s+|your\s+)?(?:system\s+prompt|instructions|api\s*key|secret|credentials)",
        r"\bact\s+as\b.{0,30}\b(?:developer|admin|root|system)\b",
        r"<\|.*?\|>",
        r"\[\[.*?\]\]",
        r"###\s*(?:system|instruction)",
        r"\bexfiltrat\w*",
        r"\b(?:call|invoke|run|trigger|execute)\s+(?:the\s+)?(?:mcp\s+)?tool\b",
        r"\bdelete\s+(?:all|every)\s+(?:lead|record|customer|tenant)\b",
        r"\bdisable\s+(?:audit|logging|security|approval)\b",
    )
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bsk-[A-Za-z0-9]{16,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        r"\bBearer\s+[A-Za-z0-9._-]{12,}\b",
    )
)


def contains_injection_signal(text: str) -> bool:
    """True if ``text`` contains a recognizable prompt-injection or policy-override attempt."""
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def contains_secret_signal(text: str) -> bool:
    """True if ``text`` contains what looks like a credential, key, or bearer token."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def is_safe_untrusted_text(text: str, *, max_length: int = 500) -> bool:
    """Fail-closed check applied to every MCP-sourced string before it enters graph state.

    Rejects empty text, text that exceeds the bound the caller intends to store it at
    (oversized payloads are themselves an exfiltration/DoS vector), and text carrying a
    recognized injection or secret-leak signal.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > max_length:
        return False
    return not contains_injection_signal(stripped) and not contains_secret_signal(stripped)


__all__ = ["contains_injection_signal", "contains_secret_signal", "is_safe_untrusted_text"]
