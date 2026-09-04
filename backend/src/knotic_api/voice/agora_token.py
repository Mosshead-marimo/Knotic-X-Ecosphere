"""Agora AccessToken2 ("007") RTC token wire format (P4-T001).

Ports the packing, signing, and parsing rules Agora publishes at
https://github.com/AgoraIO/Tools (``DynamicKey/AgoraDynamicKey``) into a small,
typed, dependency-free module so tokens built here are accepted by the real
Agora RTC service. ``build_rtc_token`` is a pure function of its arguments
(the caller supplies ``issue_ts`` and ``salt`` rather than the module sampling
them) so token construction is deterministic and independently testable;
:func:`decode_rtc_token` is the inverse, used for tests and introspection.

Only the RTC service (channel join and publish privileges) is implemented;
Knotic never issues RTM, Chat, Streaming, or APaaS tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import zlib
from base64 import b64decode, b64encode
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

_TOKEN_VERSION = "007"
_VERSION_LENGTH = len(_TOKEN_VERSION)
_RTC_SERVICE_TYPE = 1
_PRIVILEGE_JOIN_CHANNEL = 1
_PRIVILEGE_PUBLISH_AUDIO_STREAM = 2
_PRIVILEGE_PUBLISH_VIDEO_STREAM = 3
_PRIVILEGE_PUBLISH_DATA_STREAM = 4

Role = Literal["PUBLISHER", "SUBSCRIBER"]


class InvalidAgoraToken(ValueError):
    """A token or its inputs do not match the AccessToken2 wire format."""


def _pack_uint16(value: int) -> bytes:
    return struct.pack("<H", value)


def _unpack_uint16(buffer: bytes) -> tuple[int, bytes]:
    (value,) = struct.unpack_from("<H", buffer)
    return int(value), buffer[2:]


def _pack_uint32(value: int) -> bytes:
    return struct.pack("<I", value)


def _unpack_uint32(buffer: bytes) -> tuple[int, bytes]:
    (value,) = struct.unpack_from("<I", buffer)
    return int(value), buffer[4:]


def _pack_string(value: bytes) -> bytes:
    return _pack_uint16(len(value)) + value


def _unpack_string(buffer: bytes) -> tuple[bytes, bytes]:
    length, buffer = _unpack_uint16(buffer)
    return buffer[:length], buffer[length:]


def _pack_privileges(privileges: dict[int, int]) -> bytes:
    ordered = OrderedDict(sorted(privileges.items()))
    body = b"".join(_pack_uint16(privilege) + _pack_uint32(expire) for privilege, expire in ordered.items())
    return _pack_uint16(len(ordered)) + body


def _unpack_privileges(buffer: bytes) -> tuple[dict[int, int], bytes]:
    count, buffer = _unpack_uint16(buffer)
    privileges: dict[int, int] = {}
    for _ in range(count):
        privilege, buffer = _unpack_uint16(buffer)
        expire, buffer = _unpack_uint32(buffer)
        privileges[privilege] = expire
    return privileges, buffer


def _is_hex32(value: str) -> bool:
    if len(value) != 32:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _role_privileges(role: Role, privilege_expire_seconds: int) -> dict[int, int]:
    privileges = {_PRIVILEGE_JOIN_CHANNEL: privilege_expire_seconds}
    if role == "PUBLISHER":
        privileges[_PRIVILEGE_PUBLISH_AUDIO_STREAM] = privilege_expire_seconds
        privileges[_PRIVILEGE_PUBLISH_VIDEO_STREAM] = privilege_expire_seconds
        privileges[_PRIVILEGE_PUBLISH_DATA_STREAM] = privilege_expire_seconds
    return privileges


def build_rtc_token(
    *,
    app_id: str,
    app_certificate: str,
    channel_name: str,
    uid: int,
    role: Role,
    issue_ts: int,
    salt: int,
    token_expire_seconds: int,
    privilege_expire_seconds: int,
) -> str:
    """Build a version-007 Agora RTC token for one channel and numeric UID.

    ``token_expire_seconds`` and ``privilege_expire_seconds`` are durations in
    seconds from ``issue_ts`` (matching Agora's published builder signature),
    not absolute timestamps.
    """
    if not _is_hex32(app_id):
        raise InvalidAgoraToken("app_id must be 32 hexadecimal characters")
    if not _is_hex32(app_certificate):
        raise InvalidAgoraToken("app_certificate must be 32 hexadecimal characters")
    if not 1 <= len(channel_name.encode("utf-8")) <= 64:
        raise InvalidAgoraToken("channel_name must be 1 to 64 bytes")
    if not 1 <= uid <= 2**32 - 1:
        raise InvalidAgoraToken("uid must fit an unsigned 32-bit integer")
    if not 1 <= token_expire_seconds <= 86_400:
        raise InvalidAgoraToken("token_expire_seconds must be between 1 and 86400")
    if not 1 <= privilege_expire_seconds <= 86_400:
        raise InvalidAgoraToken("privilege_expire_seconds must be between 1 and 86400")
    if not 0 <= issue_ts <= 2**32 - 1 or not 0 <= salt <= 2**32 - 1:
        raise InvalidAgoraToken("issue_ts and salt must fit an unsigned 32-bit integer")

    service = (
        _pack_uint16(_RTC_SERVICE_TYPE)
        + _pack_privileges(_role_privileges(role, privilege_expire_seconds))
        + _pack_string(channel_name.encode("utf-8"))
        + _pack_string(str(uid).encode("utf-8"))
    )
    signing_info = (
        _pack_string(app_id.encode("utf-8"))
        + _pack_uint32(issue_ts)
        + _pack_uint32(token_expire_seconds)
        + _pack_uint32(salt)
        + _pack_uint16(1)
        + service
    )
    signing_key = hmac.new(_pack_uint32(issue_ts), app_certificate.encode("utf-8"), hashlib.sha256).digest()
    signing_key = hmac.new(_pack_uint32(salt), signing_key, hashlib.sha256).digest()
    signature = hmac.new(signing_key, signing_info, hashlib.sha256).digest()
    return _TOKEN_VERSION + b64encode(zlib.compress(_pack_string(signature) + signing_info)).decode("ascii")


@dataclass(frozen=True, slots=True)
class DecodedRtcToken:
    app_id: str
    channel_name: str
    uid: str
    issue_ts: int
    token_expire_seconds: int
    salt: int
    privileges: dict[int, int]
    signature_valid: bool


def decode_rtc_token(token: str, *, app_certificate: str) -> DecodedRtcToken:
    """Parse a version-007 RTC token built by :func:`build_rtc_token` and verify its signature.

    Used by tests and operational introspection; the gateway does not need to decode tokens it
    just issued, since it already knows their contents.
    """
    if token[:_VERSION_LENGTH] != _TOKEN_VERSION:
        raise InvalidAgoraToken("unsupported token version")
    try:
        raw = zlib.decompress(b64decode(token[_VERSION_LENGTH:]))
    except (zlib.error, ValueError) as error:
        raise InvalidAgoraToken("token payload is not a valid compressed AccessToken2 body") from error
    signature, signing_info = _unpack_string(raw)
    remaining = signing_info
    app_id_bytes, remaining = _unpack_string(remaining)
    issue_ts, remaining = _unpack_uint32(remaining)
    token_expire_seconds, remaining = _unpack_uint32(remaining)
    salt, remaining = _unpack_uint32(remaining)
    service_count, remaining = _unpack_uint16(remaining)
    if service_count != 1:
        raise InvalidAgoraToken("expected exactly one packed service")
    service_type, remaining = _unpack_uint16(remaining)
    if service_type != _RTC_SERVICE_TYPE:
        raise InvalidAgoraToken("expected an RTC service")
    privileges, remaining = _unpack_privileges(remaining)
    channel_name_bytes, remaining = _unpack_string(remaining)
    uid_bytes, remaining = _unpack_string(remaining)
    if remaining:
        raise InvalidAgoraToken("token payload has trailing bytes")

    signing_key = hmac.new(_pack_uint32(issue_ts), app_certificate.encode("utf-8"), hashlib.sha256).digest()
    signing_key = hmac.new(_pack_uint32(salt), signing_key, hashlib.sha256).digest()
    expected_signature = hmac.new(signing_key, signing_info, hashlib.sha256).digest()

    return DecodedRtcToken(
        app_id=app_id_bytes.decode("utf-8"),
        channel_name=channel_name_bytes.decode("utf-8"),
        uid=uid_bytes.decode("utf-8"),
        issue_ts=issue_ts,
        token_expire_seconds=token_expire_seconds,
        salt=salt,
        privileges=privileges,
        signature_valid=hmac.compare_digest(signature, expected_signature),
    )
