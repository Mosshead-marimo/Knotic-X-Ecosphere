"""Replay-stable workflow identifiers derived from trusted UUIDv7 inputs."""

import hashlib
from uuid import UUID


def derived_uuid7(source: UUID, purpose: str) -> UUID:
    timestamp = source.int >> 80
    random_bits = int.from_bytes(hashlib.sha256(source.bytes + purpose.encode()).digest(), "big") & ((1 << 74) - 1)
    value = timestamp << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
