from __future__ import annotations

from uuid import UUID

import pytest

from knotic_mcp.knowledge import ApprovedSource, KnowledgeIngestionService, KnowledgeStore, PgvectorRetrievalService

TENANT_A = UUID("0193a2d7-1000-7000-8000-000000000001")
TENANT_B = UUID("0193a2d7-1000-7000-8000-000000000002")


def test_ingestion_versions_tombstones_and_retrieval_isolation() -> None:
    store = KnowledgeStore()
    ingest = KnowledgeIngestionService(store, chunk_size=40)
    ingest.register_source(ApprovedSource("https://docs.example/product", TENANT_A, frozenset({"PRODUCT"})))
    ingest.register_source(ApprovedSource("https://docs.example/product", TENANT_B, frozenset({"PRODUCT"})))

    assert ingest.ingest(tenant_id=TENANT_A, source_uri="https://docs.example/product", title="Product", domain="PRODUCT", content_type="text/markdown", body=b"Knotic supports secure SSO and detailed audit logs.") == 1
    assert ingest.ingest(tenant_id=TENANT_B, source_uri="https://docs.example/product", title="Other", domain="PRODUCT", content_type="text/plain", body=b"Tenant B confidential pricing data.") == 1
    assert ingest.ingest(tenant_id=TENANT_A, source_uri="https://docs.example/product", title="Product", domain="PRODUCT", content_type="text/markdown", body=b"Knotic supports SSO, audit logs, and SCIM.") == 2

    results = PgvectorRetrievalService(store).search(tenant_id=TENANT_A, query="Does Knotic support SSO?", domains={"PRODUCT"}, limit=5)
    assert results and all("Tenant B" not in result.text for result in results)
    assert all(chunk.tombstoned_at is not None for chunk in store.chunks if chunk.tenant_id == TENANT_A and chunk.document_version == 1)


def test_ingestion_rejects_unapproved_and_poison_documents() -> None:
    service = KnowledgeIngestionService(KnowledgeStore())
    with pytest.raises(ValueError, match="approved"):
        service.ingest(tenant_id=TENANT_A, source_uri="https://evil.example/doc", title="x", domain="PRODUCT", content_type="text/plain", body=b"instructions")
    service.register_source(ApprovedSource("https://docs.example/product", TENANT_A, frozenset({"PRODUCT"})))
    with pytest.raises(ValueError, match="safety"):
        service.ingest(tenant_id=TENANT_A, source_uri="https://docs.example/product", title="x", domain="PRODUCT", content_type="text/plain", body=b"MZ malware")
