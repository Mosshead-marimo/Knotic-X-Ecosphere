from __future__ import annotations

from uuid import UUID

import pytest

from knotic_mcp.knowledge import (
    ApprovedSource,
    KnowledgeIngestionService,
    KnowledgeQueryService,
    KnowledgeStore,
    PgvectorRetrievalService,
)

TENANT_A = UUID("0193a2d7-1000-7000-8000-000000000001")
TENANT_B = UUID("0193a2d7-1000-7000-8000-000000000002")


def test_ingestion_versions_tombstones_and_retrieval_isolation() -> None:
    store = KnowledgeStore()
    ingest = KnowledgeIngestionService(store, chunk_size=40)
    ingest.register_source(ApprovedSource("https://docs.example/product", TENANT_A, frozenset({"PRODUCT"})))
    ingest.register_source(ApprovedSource("https://docs.example/product", TENANT_B, frozenset({"PRODUCT"})))

    assert (
        ingest.ingest(
            tenant_id=TENANT_A,
            source_uri="https://docs.example/product",
            title="Product",
            domain="PRODUCT",
            content_type="text/markdown",
            body=b"Knotic supports secure SSO and detailed audit logs.",
        )
        == 1
    )
    assert (
        ingest.ingest(
            tenant_id=TENANT_B,
            source_uri="https://docs.example/product",
            title="Other",
            domain="PRODUCT",
            content_type="text/plain",
            body=b"Tenant B confidential pricing data.",
        )
        == 1
    )
    assert (
        ingest.ingest(
            tenant_id=TENANT_A,
            source_uri="https://docs.example/product",
            title="Product",
            domain="PRODUCT",
            content_type="text/markdown",
            body=b"Knotic supports SSO, audit logs, and SCIM.",
        )
        == 2
    )

    results = PgvectorRetrievalService(store).search(
        tenant_id=TENANT_A, query="Does Knotic support SSO?", domains={"PRODUCT"}, limit=5
    )
    assert results and all("Tenant B" not in result.text for result in results)
    assert all(
        chunk.tombstoned_at is not None
        for chunk in store.chunks
        if chunk.tenant_id == TENANT_A and chunk.document_version == 1
    )


def test_ingestion_rejects_unapproved_and_poison_documents() -> None:
    service = KnowledgeIngestionService(KnowledgeStore())
    with pytest.raises(ValueError, match="approved"):
        service.ingest(
            tenant_id=TENANT_A,
            source_uri="https://evil.example/doc",
            title="x",
            domain="PRODUCT",
            content_type="text/plain",
            body=b"instructions",
        )
    service.register_source(ApprovedSource("https://docs.example/product", TENANT_A, frozenset({"PRODUCT"})))
    with pytest.raises(ValueError, match="safety"):
        service.ingest(
            tenant_id=TENANT_A,
            source_uri="https://docs.example/product",
            title="x",
            domain="PRODUCT",
            content_type="text/plain",
            body=b"MZ malware",
        )


def _seeded_query_service() -> KnowledgeQueryService:
    store = KnowledgeStore()
    ingest = KnowledgeIngestionService(store, chunk_size=900)
    ingest.register_source(ApprovedSource("https://docs.example/product", TENANT_A, frozenset({"PRODUCT"})))
    ingest.register_source(
        ApprovedSource("https://docs.example/integrations", TENANT_A, frozenset({"INTEGRATION"}))
    )
    ingest.register_source(ApprovedSource("https://docs.example/competitors", TENANT_A, frozenset({"COMPETITOR"})))
    ingest.register_source(
        ApprovedSource(
            "https://docs.example/security-public", TENANT_A, frozenset({"SECURITY"}), classification="PUBLIC"
        )
    )
    ingest.register_source(
        ApprovedSource(
            "https://docs.example/security-confidential",
            TENANT_A,
            frozenset({"SECURITY"}),
            classification="CUSTOMER_CONFIDENTIAL",
        )
    )
    ingest.ingest(
        tenant_id=TENANT_A,
        source_uri="https://docs.example/product",
        title="Knotic Workspace",
        domain="PRODUCT",
        content_type="text/markdown",
        body=b"Knotic Workspace supports single sign-on, SCIM provisioning, and detailed audit logs.",
    )
    ingest.ingest(
        tenant_id=TENANT_A,
        source_uri="https://docs.example/integrations",
        title="Slack integration",
        domain="INTEGRATION",
        content_type="text/plain",
        body=b"Knotic integrates with Slack today; the Slack integration is fully supported for notifications.",
    )
    ingest.ingest(
        tenant_id=TENANT_A,
        source_uri="https://docs.example/competitors",
        title="Acme comparison",
        domain="COMPETITOR",
        content_type="text/plain",
        body=b"Acme pricing is higher per seat than Knotic. Acme lacks SSO on its base plan.",
    )
    ingest.ingest(
        tenant_id=TENANT_A,
        source_uri="https://docs.example/security-public",
        title="SOC 2",
        domain="SECURITY",
        content_type="text/plain",
        body=b"Knotic maintains a current SOC 2 Type II report available to all customers on request.",
    )
    ingest.ingest(
        tenant_id=TENANT_A,
        source_uri="https://docs.example/security-confidential",
        title="Penetration test summary",
        domain="SECURITY",
        content_type="text/plain",
        body=b"The most recent penetration test summary details internal network segmentation controls.",
    )
    return KnowledgeQueryService(PgvectorRetrievalService(store))


def test_search_products_returns_cited_matches_or_explicit_miss() -> None:
    service = _seeded_query_service()
    result = service.search_products(tenant_id=TENANT_A, query="single sign-on", limit=5)
    assert result is not None
    assert result["products"] and result["citations"]
    assert all("chunk_id" in citation for citation in result["citations"])
    assert service.search_products(tenant_id=TENANT_A, query="quantum teleportation", limit=5) is None


def test_get_feature_returns_description_and_citations() -> None:
    service = _seeded_query_service()
    result = service.get_feature(tenant_id=TENANT_A, feature="audit logs")
    assert result is not None
    assert result["feature"] == "audit logs"
    assert "audit logs" in result["description"].casefold()
    assert result["citations"]
    assert service.get_feature(tenant_id=TENANT_A, feature="nonexistent capability xyz") is None


def test_get_integration_reports_support_status() -> None:
    service = _seeded_query_service()
    result = service.get_integration(tenant_id=TENANT_A, integration="Slack")
    assert result is not None
    assert result["support_status"] == "SUPPORTED"
    assert result["citations"]
    assert service.get_integration(tenant_id=TENANT_A, integration="nonexistent tool xyz") is None


def test_compare_competitor_leaves_ungrounded_dimensions_unknown() -> None:
    service = _seeded_query_service()
    result = service.compare_competitor(
        tenant_id=TENANT_A, competitor="Acme", dimensions=["pricing", "quantum computing support"]
    )
    assert result is not None
    grounded_dimensions = {item["dimension"] for item in result["comparisons"]}
    assert "pricing" in grounded_dimensions
    assert "quantum computing support" in result["unknowns"]
    assert result["citations"]


def test_get_security_information_denies_confidential_without_clearance() -> None:
    service = _seeded_query_service()
    public, denied = service.get_security_information(
        tenant_id=TENANT_A, topic="SOC 2 report", customer_clearance=None
    )
    assert denied is False
    assert public is not None
    assert public["classification"] == "PUBLIC"

    confidential_denied, denied = service.get_security_information(
        tenant_id=TENANT_A, topic="penetration test summary", customer_clearance=None
    )
    assert denied is True
    assert confidential_denied is None

    confidential_allowed, denied = service.get_security_information(
        tenant_id=TENANT_A,
        topic="penetration test summary",
        customer_clearance="CUSTOMER_CONFIDENTIAL",
    )
    assert denied is False
    assert confidential_allowed is not None
    assert confidential_allowed["classification"] == "CUSTOMER_CONFIDENTIAL"
