from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from statistics import quantiles
from time import perf_counter

import pytest
import redis

from knotic_api.domain import SalesState, SessionStatus, new_uuid7
from knotic_api.persistence.active_state import CacheReadStatus, RedisSalesStateRepository


@pytest.mark.integration
def test_expected_load_cache_read_p95_is_within_75_milliseconds() -> None:
    redis_url = os.getenv("KNOTIC_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("KNOTIC_TEST_REDIS_URL is required for the state performance baseline")
    client = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1, decode_responses=False)
    client.flushdb()
    repository = RedisSalesStateRepository(client, environment="performance", ttl_seconds=60)
    now = datetime.now(UTC)
    states = [
        SalesState(
            session_id=new_uuid7(),
            tenant_id=new_uuid7(),
            status=SessionStatus.CREATED,
            version=1,
            created_at=now,
            updated_at=now,
        )
        for _ in range(25)
    ]
    for state in states:
        repository.create(state, event_watermark=1, fencing_token=1)

    def measured_read(index: int) -> float:
        state = states[index % len(states)]
        started = perf_counter()
        result = repository.load(state.tenant_id, state.session_id)
        elapsed = perf_counter() - started
        assert result.status == CacheReadStatus.HIT
        return elapsed

    with ThreadPoolExecutor(max_workers=25) as executor:
        durations = list(executor.map(measured_read, range(500)))

    p95 = quantiles(durations, n=100, method="inclusive")[94]
    assert p95 <= 0.075, f"cache state-load p95 was {p95 * 1000:.2f} ms"
