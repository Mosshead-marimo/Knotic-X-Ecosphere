from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import SecretStr

from knotic_mcp.app import create_app
from knotic_mcp.cache import CacheEntry, InMemoryToolResultCache, ToolResultCache, cache_key
from knotic_mcp.config import McpSettings
from knotic_mcp.contracts import ToolEnvelope, ToolStatus
from knotic_mcp.knowledge import ApprovedSource, KnowledgeIngestionService, KnowledgeStore
from knotic_mcp.registry import ToolRegistry
from knotic_mcp.sales import PricingCatalog

IDENTIFIERS = {
    "tenant_id": "0193a2d7-1000-7000-8000-000000000001",
    "actor_id": "0193a2d7-1000-7000-8000-000000000002",
    "correlation_id": "0193a2d7-1000-7000-8000-000000000003",
}


async def _request(
    app: object, *, path: str, headers: dict[str, str], body: dict[str, object]
) -> tuple[int, dict[str, object]]:
    messages: list[dict[str, object]] = []
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        },
        receive,
        send,
    )  # type: ignore[misc]
    return int(messages[0]["status"]), json.loads(messages[1]["body"])


async def _request_with_headers(
    app: object, *, path: str, headers: dict[str, str], body: dict[str, object]
) -> tuple[int, dict[str, object], dict[str, str]]:
    messages: list[dict[str, object]] = []
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(key.encode(), value.encode()) for key, value in headers.items()],
        },
        receive,
        send,
    )  # type: ignore[misc]
    response_headers = {key.decode().lower(): value.decode() for key, value in messages[0]["headers"]}
    return int(messages[0]["status"]), json.loads(messages[1]["body"]), response_headers


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_rejects_unknown_tool_and_invalid_arguments(self) -> None:
        token = "a" * 40
        app = create_app(
            McpSettings(
                database_url=SecretStr("postgresql://user:password@db.internal/app"),
                redis_url=SecretStr("redis://:password@redis.internal/0"),
                auth_token=SecretStr(token),
            )
        )
        context = {
            **IDENTIFIERS,
            "scopes": ["knowledge:read"],
            "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        headers = {"authorization": f"Bearer {token}", "x-knotic-trusted-context": json.dumps(context)}
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000004",
            "tool": "knowledge.search",
            "version": 1,
            "arguments": {"query": "SSO"},
        }
        status, payload = await _request(app, path="/v1/tools/knowledge.search", headers=headers, body=body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
        body["tool"] = "unknown.tool"
        status, payload = await _request(app, path="/v1/tools/unknown.tool", headers=headers, body=body)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    async def _authorized_app(
        self,
        *,
        scopes: list[str],
        knowledge_store: KnowledgeStore | None = None,
        pricing_catalog: PricingCatalog | None = None,
        tool_cache: ToolResultCache | None = None,
        registry: ToolRegistry | None = None,
    ) -> tuple[object, dict[str, str]]:
        token = "a" * 40
        app = create_app(
            McpSettings(
                database_url=SecretStr("postgresql://user:password@db.internal/app"),
                redis_url=SecretStr("redis://:password@redis.internal/0"),
                auth_token=SecretStr(token),
            ),
            knowledge_store=knowledge_store,
            pricing_catalog=pricing_catalog,
            tool_cache=tool_cache,
            registry=registry,
        )
        context = {
            **IDENTIFIERS,
            "scopes": scopes,
            "deadline_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        headers = {"authorization": f"Bearer {token}", "x-knotic-trusted-context": json.dumps(context)}
        return app, headers

    async def test_knowledge_tools_reject_invalid_arguments(self) -> None:
        app, headers = await self._authorized_app(scopes=["knowledge:read"])
        for tool, arguments in (
            ("product.search", {"query": ""}),
            ("product.get_feature", {}),
            ("product.get_integration", {"integration": ""}),
            ("competitor.compare", {"competitor": "Acme", "dimensions": []}),
            ("security.get_information", {"topic": "SOC 2", "extra_field": "nope"}),
        ):
            body = {
                "tool_call_id": "0193a2d7-1000-7000-8000-000000000004",
                "tool": tool,
                "version": 1,
                "arguments": arguments,
            }
            status, payload = await _request(app, path=f"/v1/tools/{tool}", headers=headers, body=body)
            self.assertEqual(status, 400, tool)
            self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT", tool)

    async def test_knowledge_tools_return_explicit_miss_without_evidence(self) -> None:
        app, headers = await self._authorized_app(scopes=["knowledge:read"])
        for tool, arguments in (
            ("product.search", {"query": "nonexistent", "limit": 5}),
            ("product.get_feature", {"feature": "nonexistent"}),
            ("product.get_integration", {"integration": "nonexistent"}),
            ("competitor.compare", {"competitor": "nonexistent", "dimensions": ["pricing"]}),
            ("security.get_information", {"topic": "nonexistent"}),
        ):
            body = {
                "tool_call_id": "0193a2d7-1000-7000-8000-000000000005",
                "tool": tool,
                "version": 1,
                "arguments": arguments,
            }
            status, payload = await _request(app, path=f"/v1/tools/{tool}", headers=headers, body=body)
            self.assertEqual(status, 200, tool)
            self.assertEqual(payload["error"]["code"], "NO_GROUNDED_RESULT", tool)

    async def test_product_search_succeeds_with_grounded_citations(self) -> None:
        tenant_id = UUID(IDENTIFIERS["tenant_id"])
        store = KnowledgeStore()
        ingest = KnowledgeIngestionService(store)
        ingest.register_source(ApprovedSource("https://docs.example/product", tenant_id, frozenset({"PRODUCT"})))
        ingest.ingest(
            tenant_id=tenant_id,
            source_uri="https://docs.example/product",
            title="Knotic Workspace",
            domain="PRODUCT",
            content_type="text/markdown",
            body=b"Knotic Workspace supports single sign-on and SCIM provisioning.",
        )
        app, headers = await self._authorized_app(scopes=["knowledge:read"], knowledge_store=store)
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000006",
            "tool": "product.search",
            "version": 1,
            "arguments": {"query": "single sign-on", "limit": 5},
        }
        status, payload = await _request(app, path="/v1/tools/product.search", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertTrue(payload["data"]["products"])
        self.assertTrue(payload["data"]["citations"])

    async def test_security_get_information_denies_confidential_without_clearance(self) -> None:
        tenant_id = UUID(IDENTIFIERS["tenant_id"])
        store = KnowledgeStore()
        ingest = KnowledgeIngestionService(store)
        ingest.register_source(
            ApprovedSource(
                "https://docs.example/pentest",
                tenant_id,
                frozenset({"SECURITY"}),
                classification="CUSTOMER_CONFIDENTIAL",
            )
        )
        ingest.ingest(
            tenant_id=tenant_id,
            source_uri="https://docs.example/pentest",
            title="Pentest summary",
            domain="SECURITY",
            content_type="text/plain",
            body=b"The pentest summary covers network segmentation controls in detail.",
        )
        app, headers = await self._authorized_app(scopes=["knowledge:read"], knowledge_store=store)
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000007",
            "tool": "security.get_information",
            "version": 1,
            "arguments": {"topic": "pentest summary"},
        }
        status, payload = await _request(app, path="/v1/tools/security.get_information", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], "PERMISSION_DENIED")

    @staticmethod
    def _test_catalog() -> PricingCatalog:
        return PricingCatalog.from_payload(
            {
                "source_version": "test-1",
                "plans": [
                    {
                        "plan_id": "PRO",
                        "display_name": "Professional",
                        "currency": "USD",
                        "monthly_price_per_user": "49.00",
                        "annual_price_per_user": "490.00",
                        "minimum_users": 5,
                        "region": "GLOBAL",
                        "effective_at": "2026-01-01T00:00:00+00:00",
                        "expires_at": "2027-01-01T00:00:00+00:00",
                        "source": "pricing-catalog-v1",
                        "features": [],
                    },
                    {
                        "plan_id": "EXPIRED",
                        "display_name": "Legacy",
                        "currency": "USD",
                        "monthly_price_per_user": "9.00",
                        "annual_price_per_user": "90.00",
                        "minimum_users": 1,
                        "region": "GLOBAL",
                        "effective_at": "2020-01-01T00:00:00+00:00",
                        "expires_at": "2021-01-01T00:00:00+00:00",
                        "source": "pricing-catalog-v1",
                        "features": [],
                    },
                ],
            }
        )

    async def test_pricing_get_quote_succeeds_and_rejects_stale_plan(self) -> None:
        app, headers = await self._authorized_app(scopes=["sales:read"], pricing_catalog=self._test_catalog())
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000008",
            "tool": "pricing.get_quote",
            "version": 1,
            "arguments": {"users": 10, "billing_period": "MONTHLY", "plan": "PRO"},
        }
        status, payload = await _request(app, path="/v1/tools/pricing.get_quote", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertEqual(payload["data"]["total"], "490.00")

        body["arguments"]["plan"] = "EXPIRED"
        status, payload = await _request(app, path="/v1/tools/pricing.get_quote", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], "PRICE_UNAVAILABLE")

    async def test_pricing_get_quote_rejects_invalid_arguments(self) -> None:
        app, headers = await self._authorized_app(scopes=["sales:read"], pricing_catalog=self._test_catalog())
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000009",
            "tool": "pricing.get_quote",
            "version": 1,
            "arguments": {"users": 0, "billing_period": "MONTHLY"},
        }
        status, payload = await _request(app, path="/v1/tools/pricing.get_quote", headers=headers, body=body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")

    async def test_pricing_compare_plans_rejects_when_any_plan_is_stale(self) -> None:
        app, headers = await self._authorized_app(scopes=["sales:read"], pricing_catalog=self._test_catalog())
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000010",
            "tool": "pricing.compare_plans",
            "version": 1,
            "arguments": {"plan_ids": ["PRO", "EXPIRED"], "users": 10},
        }
        status, payload = await _request(app, path="/v1/tools/pricing.compare_plans", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], "PRICE_UNAVAILABLE")

    async def test_lead_qualify_succeeds_deterministically(self) -> None:
        app, headers = await self._authorized_app(scopes=["sales:read"])
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000011",
            "tool": "lead.qualify",
            "version": 1,
            "arguments": {
                "need": 25,
                "product_fit": 20,
                "deployment_fit": 15,
                "timeline": 15,
                "authority": 10,
                "budget": 5,
                "purchase_intent": 10,
            },
        }
        status, payload = await _request(app, path="/v1/tools/lead.qualify", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["total"], 100)
        self.assertEqual(payload["data"]["stage"], "HIGH_INTENT")

    async def test_lead_qualify_rejects_out_of_bounds_component(self) -> None:
        app, headers = await self._authorized_app(scopes=["sales:read"])
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000012",
            "tool": "lead.qualify",
            "version": 1,
            "arguments": {
                "need": 999,
                "product_fit": 20,
                "deployment_fit": 15,
                "timeline": 15,
                "authority": 10,
                "budget": 5,
                "purchase_intent": 10,
            },
        }
        status, payload = await _request(app, path="/v1/tools/lead.qualify", headers=headers, body=body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")

    async def test_lead_next_action_applies_explicit_override(self) -> None:
        app, headers = await self._authorized_app(scopes=["sales:read"])
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000013",
            "tool": "lead.next_action",
            "version": 1,
            "arguments": {"intent": "DISCOVERY", "stage": "SALES_QUALIFIED", "explicit_request": "book a demo call"},
        }
        status, payload = await _request(app, path="/v1/tools/lead.next_action", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["action"], "BOOK_DEMO")

    async def test_fresh_cache_hit_is_served_without_calling_the_provider_again(self) -> None:
        tenant_id = UUID(IDENTIFIERS["tenant_id"])
        store = KnowledgeStore()
        ingest = KnowledgeIngestionService(store)
        ingest.register_source(ApprovedSource("https://docs.example/product", tenant_id, frozenset({"PRODUCT"})))
        ingest.ingest(
            tenant_id=tenant_id,
            source_uri="https://docs.example/product",
            title="Knotic Workspace",
            domain="PRODUCT",
            content_type="text/markdown",
            body=b"Knotic Workspace supports single sign-on and SCIM provisioning.",
        )
        app, headers = await self._authorized_app(scopes=["knowledge:read"], knowledge_store=store)
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000014",
            "tool": "product.search",
            "version": 1,
            "arguments": {"query": "single sign-on", "limit": 5},
        }
        status, payload, headers_first = await _request_with_headers(
            app, path="/v1/tools/product.search", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers_first["x-knotic-cache"], "MISS")
        self.assertTrue(payload["data"]["products"])

        # Remove the underlying evidence entirely: a fresh (uncached) lookup would now miss.
        store.chunks.clear()
        status, payload, headers_second = await _request_with_headers(
            app, path="/v1/tools/product.search", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers_second["x-knotic-cache"], "HIT")
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertTrue(payload["data"]["products"])
        self.assertNotIn("x-knotic-degraded", headers_second)

    async def test_stale_if_safe_serves_a_recent_answer_when_the_provider_fails(self) -> None:
        tenant_id = UUID(IDENTIFIERS["tenant_id"])
        now = datetime.now(UTC)
        tool_call_id = UUID("0193a2d7-1000-7000-8000-000000000015")
        good_envelope = ToolEnvelope(
            tool_call_id=tool_call_id,
            tool="product.search",
            version=1,
            status=ToolStatus.SUCCEEDED,
            data={
                "products": [{"name": "Knotic Workspace", "summary": "Supports SSO.", "score": 0.9}],
                "citations": [],
            },
            started_at=now,
            completed_at=now,
        )
        cache = InMemoryToolResultCache()
        key = cache_key(tenant_id=tenant_id, tool="product.search", version=1, arguments={"query": "sso", "limit": 5})
        cache.set(
            key,
            CacheEntry(
                envelope=good_envelope,
                cached_at=now - timedelta(seconds=120),
                fresh_until=now - timedelta(seconds=1),
                stale_until=now + timedelta(seconds=300),
            ),
        )

        def failing_handler(context: object, invocation: object) -> ToolEnvelope:
            raise ValueError("simulated provider outage")

        registry = ToolRegistry(handlers={"product.search": failing_handler})
        app, headers = await self._authorized_app(scopes=["knowledge:read"], tool_cache=cache, registry=registry)
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000016",
            "tool": "product.search",
            "version": 1,
            "arguments": {"query": "sso", "limit": 5},
        }
        status, payload, response_headers = await _request_with_headers(
            app, path="/v1/tools/product.search", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "SUCCEEDED")
        self.assertEqual(response_headers["x-knotic-cache"], "STALE")
        self.assertEqual(response_headers["x-knotic-degraded"], "true")
        self.assertTrue(payload["data"]["products"])

    async def test_pricing_provider_failure_never_serves_a_stale_price(self) -> None:
        tenant_id = UUID(IDENTIFIERS["tenant_id"])
        now = datetime.now(UTC)
        good_envelope = ToolEnvelope(
            tool_call_id=UUID("0193a2d7-1000-7000-8000-000000000017"),
            tool="pricing.get_quote",
            version=1,
            status=ToolStatus.SUCCEEDED,
            data={
                "quote_id": "0193a2d7-1000-7000-8000-000000000018",
                "currency": "USD",
                "total": "490.00",
                "valid_until": (now + timedelta(days=10)).isoformat(),
                "source": "pricing-catalog-v1@test-1",
            },
            started_at=now,
            completed_at=now,
        )
        cache = InMemoryToolResultCache()
        key = cache_key(
            tenant_id=tenant_id,
            tool="pricing.get_quote",
            version=1,
            arguments={"users": 10, "billing_period": "MONTHLY", "plan": "PRO"},
        )
        # Even a very recent, still-fresh-looking cache entry must never be served once its own
        # freshness window has lapsed for pricing: stale_grace_seconds is 0 for pricing tools.
        cache.set(
            key,
            CacheEntry(
                envelope=good_envelope,
                cached_at=now - timedelta(seconds=5),
                fresh_until=now - timedelta(seconds=1),
                stale_until=now - timedelta(seconds=1),
            ),
        )

        def failing_handler(context: object, invocation: object) -> ToolEnvelope:
            raise ValueError("simulated pricing provider outage")

        registry = ToolRegistry(handlers={"pricing.get_quote": failing_handler})
        app, headers = await self._authorized_app(scopes=["sales:read"], tool_cache=cache, registry=registry)
        body = {
            "tool_call_id": "0193a2d7-1000-7000-8000-000000000019",
            "tool": "pricing.get_quote",
            "version": 1,
            "arguments": {"users": 10, "billing_period": "MONTHLY", "plan": "PRO"},
        }
        status, payload, response_headers = await _request_with_headers(
            app, path="/v1/tools/pricing.get_quote", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], "DEPENDENCY_UNAVAILABLE")
        self.assertEqual(response_headers["x-knotic-cache"], "MISS")
        self.assertNotIn("x-knotic-degraded", response_headers)
