from __future__ import annotations

from typing import Any

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.managed_agent import ManagedAgentConfiguration, ManagedAgentService


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes, *, ex: int) -> bool:
        assert 3600 <= ex <= 4000
        self.values[key] = value
        return True

    def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


class RecordingManagedAgent(ManagedAgentService):
    def __init__(self, configuration: ManagedAgentConfiguration, store: Any) -> None:
        super().__init__(configuration, store, environment="test")
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    def _request(self, method: str, path: str, payload: Any) -> dict[str, object]:
        self.requests.append((method, path, dict(payload)))
        return {"agent_id": "agent-confirmed-1"}


def test_managed_agent_uses_agora_asr_openai_tts_and_private_llm() -> None:
    store = MemoryRedis()
    service = RecordingManagedAgent(
        ManagedAgentConfiguration(
            app_id="0123456789abcdef0123456789abcdef",
            app_certificate="b" * 32,
            customer_id="customer-id",
            customer_secret="customer-secret",
            openai_api_key="openai-secret-value",
            llm_url="https://api.example.test/api/v1/internal/voice/chat/completions",
            llm_api_key="internal-agent-secret",
        ),
        store,
    )
    tenant_id, session_id = new_uuid7(), new_uuid7()

    started = service.start(tenant_id=tenant_id, session_id=session_id, customer_uid=12)
    duplicate = service.start(tenant_id=tenant_id, session_id=session_id, customer_uid=12)

    assert started["status"] == "confirmed"
    assert duplicate["duplicate"] is True
    assert len(service.requests) == 1
    properties = service.requests[0][2]["properties"]
    assert isinstance(properties, dict)
    assert properties["asr"] == {"vendor": "ares", "language": "en-US"}
    assert properties["tts"] == {
        "vendor": "openai",
        "params": {"api_key": "openai-secret-value", "model": "tts-1", "voice": "alloy"},
    }
    assert properties["llm"] == {
        "url": "https://api.example.test/api/v1/internal/voice/chat/completions",
        "api_key": "internal-agent-secret",
        "params": {"model": "knotic-langgraph-v1"},
        "system_messages": [{"role": "system", "content": "Use the server-owned sales workflow."}],
    }

    stopped = service.stop(tenant_id=tenant_id, session_id=session_id)
    assert stopped == {"agent_id": "agent-confirmed-1", "status": "confirmed", "already_stopped": False}
    assert service.stop(tenant_id=tenant_id, session_id=session_id)["already_stopped"] is True
