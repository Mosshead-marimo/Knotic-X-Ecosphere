from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from knotic_mcp.cache import (
    TOOL_CACHE_POLICIES,
    CacheEntry,
    CachePolicy,
    InMemoryToolResultCache,
    ProviderHealthTracker,
    cache_key,
)
from knotic_mcp.contracts import ToolEnvelope, ToolStatus

TENANT = UUID("0193a2d7-1000-7000-8000-000000000001")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _envelope() -> ToolEnvelope:
    return ToolEnvelope(
        tool_call_id=UUID("0193a2d7-1000-7000-8000-000000000099"),
        tool="knowledge.search",
        version=1,
        status=ToolStatus.SUCCEEDED,
        data={"matches": [], "index_version": "v1"},
        started_at=NOW,
        completed_at=NOW,
    )


def test_cache_key_is_stable_regardless_of_argument_order() -> None:
    a = cache_key(tenant_id=TENANT, tool="knowledge.search", version=1, arguments={"query": "sso", "limit": 5})
    b = cache_key(tenant_id=TENANT, tool="knowledge.search", version=1, arguments={"limit": 5, "query": "sso"})
    assert a == b


def test_cache_key_differs_by_tenant_tool_version_or_arguments() -> None:
    base = cache_key(tenant_id=TENANT, tool="knowledge.search", version=1, arguments={"query": "sso", "limit": 5})
    other_tenant = cache_key(
        tenant_id=UUID("0193a2d7-1000-7000-8000-000000000002"),
        tool="knowledge.search",
        version=1,
        arguments={"query": "sso", "limit": 5},
    )
    other_tool = cache_key(tenant_id=TENANT, tool="product.search", version=1, arguments={"query": "sso", "limit": 5})
    other_args = cache_key(tenant_id=TENANT, tool="knowledge.search", version=1, arguments={"query": "scim", "limit": 5})
    assert len({base, other_tenant, other_tool, other_args}) == 4


def test_cache_policy_allows_stale_only_when_cacheable_and_grace_positive() -> None:
    assert not CachePolicy(cacheable=False, ttl_seconds=60, stale_grace_seconds=300).allows_stale
    assert not CachePolicy(cacheable=True, ttl_seconds=60, stale_grace_seconds=0).allows_stale
    assert CachePolicy(cacheable=True, ttl_seconds=60, stale_grace_seconds=300).allows_stale


def test_pricing_tools_never_allow_stale_serving() -> None:
    assert TOOL_CACHE_POLICIES["pricing.get_quote"].allows_stale is False
    assert TOOL_CACHE_POLICIES["pricing.compare_plans"].allows_stale is False


def test_side_effect_and_deterministic_tools_are_not_in_the_cache_policy_table() -> None:
    # lead.qualify / lead.next_action are cheap pure functions; followup.create is a side effect.
    # None should ever be cached.
    for tool in ("lead.qualify", "lead.next_action", "followup.create"):
        assert TOOL_CACHE_POLICIES.get(tool, CachePolicy(cacheable=False)).cacheable is False


def test_cache_entry_freshness_and_stale_grace_windows() -> None:
    entry = CacheEntry(
        envelope=_envelope(),
        cached_at=NOW,
        fresh_until=NOW + timedelta(seconds=60),
        stale_until=NOW + timedelta(seconds=360),
    )
    assert entry.is_fresh(at=NOW + timedelta(seconds=30))
    assert not entry.is_fresh(at=NOW + timedelta(seconds=61))
    assert not entry.is_within_stale_grace(at=NOW + timedelta(seconds=30))
    assert entry.is_within_stale_grace(at=NOW + timedelta(seconds=120))
    assert not entry.is_within_stale_grace(at=NOW + timedelta(seconds=400))


def test_in_memory_cache_get_set_and_invalidate() -> None:
    cache = InMemoryToolResultCache()
    key = cache_key(tenant_id=TENANT, tool="knowledge.search", version=1, arguments={"query": "sso", "limit": 5})
    assert cache.get(key) is None
    entry = CacheEntry(
        envelope=_envelope(), cached_at=NOW, fresh_until=NOW + timedelta(seconds=60), stale_until=NOW + timedelta(seconds=360)
    )
    cache.set(key, entry)
    assert cache.get(key) == entry
    cache.invalidate(key)
    assert cache.get(key) is None


def test_in_memory_cache_invalidate_prefix_clears_matching_entries_only() -> None:
    cache = InMemoryToolResultCache()
    entry = CacheEntry(
        envelope=_envelope(), cached_at=NOW, fresh_until=NOW + timedelta(seconds=60), stale_until=NOW + timedelta(seconds=360)
    )
    matching_key = f"mcp:cache:{TENANT}:knowledge.search:1:aaa"
    other_key = f"mcp:cache:{TENANT}:pricing.get_quote:1:bbb"
    cache.set(matching_key, entry)
    cache.set(other_key, entry)
    cache.invalidate_prefix(f"mcp:cache:{TENANT}:knowledge.search:")
    assert cache.get(matching_key) is None
    assert cache.get(other_key) == entry


def test_provider_health_opens_after_threshold_and_resets_on_success() -> None:
    tracker = ProviderHealthTracker(failure_threshold=3, open_seconds=60)
    assert not tracker.is_open("knowledge_retrieval")
    tracker.record_failure("knowledge_retrieval")
    tracker.record_failure("knowledge_retrieval")
    assert not tracker.is_open("knowledge_retrieval")
    tracker.record_failure("knowledge_retrieval")
    assert tracker.is_open("knowledge_retrieval")
    tracker.record_success("knowledge_retrieval")
    assert not tracker.is_open("knowledge_retrieval")


def test_provider_health_status_reports_each_tracked_provider() -> None:
    tracker = ProviderHealthTracker(failure_threshold=1, open_seconds=60)
    tracker.record_failure("pricing_catalog")
    tracker.record_success("knowledge_retrieval")
    status = tracker.status()
    assert status["pricing_catalog"] == "degraded"
    assert status["knowledge_retrieval"] == "ok"
