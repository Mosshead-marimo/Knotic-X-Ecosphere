from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import redis

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.domain.models import SalesState
from knotic_api.domain.types import SessionStatus
from knotic_api.persistence.active_state import (
    ActiveStateUnavailable,
    CacheReadStatus,
    ConcurrentStateUpdate,
    RedisSalesStateRepository,
    StaleFencingToken,
)


def _state(*, version: int = 1, intent: str | None = None) -> SalesState:
    now = datetime.now(UTC)
    return SalesState(
        session_id=new_uuid7(),
        tenant_id=new_uuid7(),
        status=SessionStatus.CREATED,
        version=version,
        current_intent=intent,
        created_at=now,
        updated_at=now,
    )


def _next(state: SalesState, intent: str) -> SalesState:
    return state.model_copy(
        update={
            "version": state.version + 1,
            "current_intent": intent,
            "updated_at": state.updated_at + timedelta(milliseconds=1),
        }
    )


def test_key_uses_environment_and_cluster_hash_tag() -> None:
    state = _state()
    repository = RedisSalesStateRepository(redis.Redis(), environment="test")
    assert repository.key(state.tenant_id, state.session_id) == (
        f"knotic:test:{{{state.tenant_id}:{state.session_id}}}:state:v1"
    )
    with pytest.raises(ValueError, match="lowercase"):
        RedisSalesStateRepository(redis.Redis(), environment="Production West")
    with pytest.raises(ValueError, match="UUIDv7"):
        repository.key(uuid4(), state.session_id)


def _integration_repository() -> tuple[redis.Redis, RedisSalesStateRepository]:
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("KNOTIC_TEST_REDIS_URL is required for Redis integration tests")
    client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=1,
        socket_timeout=1,
        decode_responses=False,
    )
    client.flushdb()
    return client, RedisSalesStateRepository(client, environment="test", ttl_seconds=60)


@pytest.mark.integration
def test_round_trip_sliding_ttl_and_corruption_recovery() -> None:
    client, repository = _integration_repository()
    state = _state()
    envelope = repository.create(state, event_watermark=7, fencing_token=11)
    assert envelope.state == state

    result = repository.load(state.tenant_id, state.session_id)
    assert result.status == CacheReadStatus.HIT
    assert result.envelope == envelope
    assert 0 < client.ttl(repository.key(state.tenant_id, state.session_id)) <= 60

    client.set(repository.key(state.tenant_id, state.session_id), b'{"schema_version":999}', ex=60)
    corrupt = repository.load(state.tenant_id, state.session_id)
    assert corrupt.status == CacheReadStatus.CORRUPT
    assert client.exists(repository.key(state.tenant_id, state.session_id)) == 0
    assert repository.load(state.tenant_id, state.session_id).status == CacheReadStatus.MISS


@pytest.mark.integration
def test_configured_ttl_expires_active_state() -> None:
    client, _ = _integration_repository()
    repository = RedisSalesStateRepository(client, environment="test", ttl_seconds=1)
    state = _state()
    repository.create(state, event_watermark=0, fencing_token=0)
    assert repository.load(state.tenant_id, state.session_id).status == CacheReadStatus.HIT
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline and client.exists(repository.key(state.tenant_id, state.session_id)):
        # Poll the underlying key because repository reads intentionally slide
        # the expiry window.
        time.sleep(0.05)
    assert client.exists(repository.key(state.tenant_id, state.session_id)) == 0


@pytest.mark.integration
def test_compare_and_set_prevents_lost_updates_and_stale_writers() -> None:
    _, repository = _integration_repository()
    state = _state()
    repository.create(state, event_watermark=1, fencing_token=4)

    proposals = [_next(state, "security"), _next(state, "integrations")]

    def update(proposal: SalesState) -> str:
        try:
            repository.compare_and_set(
                proposal,
                expected_version=1,
                event_watermark=2,
                fencing_token=4,
            )
            return "saved"
        except ConcurrentStateUpdate:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update, proposals))
    assert sorted(outcomes) == ["conflict", "saved"]

    current = repository.load(state.tenant_id, state.session_id)
    assert current.envelope is not None
    version_two = current.envelope.state
    with pytest.raises(StaleFencingToken):
        repository.compare_and_set(
            _next(version_two, "pricing"),
            expected_version=2,
            event_watermark=3,
            fencing_token=3,
        )


@pytest.mark.integration
def test_duplicate_create_and_redis_outage_fail_safely() -> None:
    _, repository = _integration_repository()
    state = _state()
    repository.create(state, event_watermark=0, fencing_token=0)
    with pytest.raises(ConcurrentStateUpdate):
        repository.create(state, event_watermark=0, fencing_token=0)

    unavailable = RedisSalesStateRepository(
        redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.05, socket_timeout=0.05),
        environment="test",
    )
    with pytest.raises(ActiveStateUnavailable):
        unavailable.load(state.tenant_id, state.session_id)
