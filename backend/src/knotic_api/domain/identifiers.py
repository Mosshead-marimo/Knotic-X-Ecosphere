"""Opaque RFC 9562 UUIDv7 identifiers."""

from __future__ import annotations

import secrets
import time
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator


def _require_uuid7(value: UUID) -> UUID:
    if value.version != 7 or value.variant != "specified in RFC 4122":
        raise ValueError("identifier must be an RFC 9562 UUIDv7")
    return value


UUID7 = Annotated[UUID, AfterValidator(_require_uuid7)]


def new_uuid7(*, timestamp_ms: int | None = None) -> UUID:
    """Generate an RFC 9562 UUIDv7 without relying on a database extension."""

    timestamp = int(time.time_ns() // 1_000_000) if timestamp_ms is None else timestamp_ms
    if not 0 <= timestamp < 2**48:
        raise ValueError("timestamp_ms must fit in the UUIDv7 48-bit timestamp")
    random_bits = secrets.randbits(74)
    value = timestamp << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
