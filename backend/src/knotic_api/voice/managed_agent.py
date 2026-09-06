"""Server-only Agora Conversational AI lifecycle adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

import redis

from .agora_token import build_rtc_token
from .session_service import channel_name_for


class ManagedAgentUnavailable(RuntimeError):
    """The provider could not confirm an agent lifecycle operation."""


@dataclass(frozen=True, slots=True)
class ManagedAgentConfiguration:
    app_id: str
    app_certificate: str
    customer_id: str | None
    customer_secret: str | None
    openai_api_key: str | None
    llm_url: str | None
    llm_api_key: str | None
    api_base_url: str = "https://api.agora.io/api/conversational-ai-agent/v2"


class ManagedAgentService:
    def __init__(self, configuration: ManagedAgentConfiguration, store: redis.Redis, *, environment: str) -> None:
        self.configuration = configuration
        self.store = store
        self.environment = environment

    def start(self, *, tenant_id: UUID, session_id: UUID, customer_uid: int) -> dict[str, object]:
        cached = self._load(tenant_id, session_id)
        if cached:
            return {**cached, "duplicate": True}
        configuration = self.configuration
        if not all(
            (
                configuration.customer_id,
                configuration.customer_secret,
                configuration.openai_api_key,
                configuration.llm_url,
                configuration.llm_api_key,
            )
        ):
            raise ManagedAgentUnavailable("Managed voice is not configured for this environment.")
        channel = channel_name_for(tenant_id=tenant_id, session_id=session_id)
        agent_uid = self._agent_uid(session_id)
        now = int(time.time())
        token = build_rtc_token(
            app_id=configuration.app_id,
            app_certificate=configuration.app_certificate,
            channel_name=channel,
            uid=agent_uid,
            role="PUBLISHER",
            issue_ts=now,
            salt=secrets.randbits(32),
            token_expire_seconds=3600,
            privilege_expire_seconds=3600,
        )
        payload = {
            "name": f"voxsales-{session_id}",
            "properties": {
                "channel": channel,
                "token": token,
                "agent_rtc_uid": str(agent_uid),
                "remote_rtc_uids": [str(customer_uid)],
                "enable_string_uid": False,
                "advanced_features": {"enable_aivad": True, "enable_rtm": True},
                "asr": {"vendor": "ares", "language": "en-US"},
                "tts": {
                    "vendor": "openai",
                    "params": {"api_key": configuration.openai_api_key, "model": "tts-1", "voice": "alloy"},
                },
                "llm": {
                    "url": configuration.llm_url,
                    "api_key": configuration.llm_api_key,
                    "params": {"model": "knotic-langgraph-v1"},
                    "system_messages": [{"role": "system", "content": "Use the server-owned sales workflow."}],
                },
                "vad": {"mode": "interrupt"},
                "parameters": {"transcript": {"enable": True, "protocol_version": "v2"}},
            },
        }
        response = self._request("POST", f"projects/{configuration.app_id}/join", payload)
        agent_id = response.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            raise ManagedAgentUnavailable("Agora did not confirm an agent identifier.")
        result: dict[str, object] = {
            "agent_id": agent_id,
            "agent_uid": agent_uid,
            "channel": channel,
            "status": "confirmed",
            "duplicate": False,
        }
        try:
            self.store.set(self._key(tenant_id, session_id), json.dumps(result).encode(), ex=3900)
        except redis.RedisError as error:
            self._request("POST", f"projects/{configuration.app_id}/agents/{agent_id}/leave", {})
            raise ManagedAgentUnavailable("Agent state could not be stored safely.") from error
        return result

    def stop(self, *, tenant_id: UUID, session_id: UUID) -> dict[str, object]:
        active = self._load(tenant_id, session_id)
        if not active:
            return {"status": "confirmed", "already_stopped": True}
        agent_id = str(active["agent_id"])
        self._request("POST", f"projects/{self.configuration.app_id}/agents/{agent_id}/leave", {})
        try:
            self.store.delete(self._key(tenant_id, session_id))
        except redis.RedisError as error:
            raise ManagedAgentUnavailable("Stopped agent state could not be cleared.") from error
        return {"agent_id": agent_id, "status": "confirmed", "already_stopped": False}

    def _request(self, method: str, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        configuration = self.configuration
        credentials = base64.b64encode(f"{configuration.customer_id}:{configuration.customer_secret}".encode()).decode()
        request = Request(  # noqa: S310 - configuration validation restricts this to HTTP(S)
            f"{configuration.api_base_url.rstrip('/')}/{path}",
            data=json.dumps(payload).encode(),
            method=method,
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=8) as response:  # noqa: S310
                document = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ManagedAgentUnavailable("Agora could not confirm the agent operation.") from error
        return document if isinstance(document, dict) else {}

    def _load(self, tenant_id: UUID, session_id: UUID) -> dict[str, object] | None:
        try:
            raw = self.store.get(self._key(tenant_id, session_id))
        except redis.RedisError as error:
            raise ManagedAgentUnavailable("Agent state is unavailable.") from error
        if not isinstance(raw, bytes):
            return None
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ManagedAgentUnavailable("Agent state is invalid.") from error
        return document if isinstance(document, dict) else None

    def _key(self, tenant_id: UUID, session_id: UUID) -> str:
        digest = hashlib.sha256(f"{tenant_id}:{session_id}".encode()).hexdigest()
        return f"knotic:{self.environment}:managed-agent:{digest}"

    @staticmethod
    def _agent_uid(session_id: UUID) -> int:
        digest = hashlib.sha256(session_id.bytes + b":managed-agent").digest()
        return int.from_bytes(digest[:4], "big") % (2**32 - 1) + 1
