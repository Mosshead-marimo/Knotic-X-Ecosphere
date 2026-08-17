# Phase 3 — MCP, Knowledge, and Grounded Business Facts

## Objective

Provide secure, observable MCP tools and production-quality retrieval so mutable facts and business actions always come from authoritative sources.

## Entry criteria

- Phase 2 gate passed.
- `MCP_TOOLS.md` and data classification are approved.

## Tasks

### [ ] P3-T001 — Implement authenticated MCP gateway

- Dependencies: P0-T007.
- Implement: Service identity, tenant context, authorization, schema validation, request IDs, rate limits, timeouts, response-size limits, and audit hooks.
- Acceptance: Unknown tools, invalid inputs, unauthorized tenants, and oversized payloads fail closed.
- Verify: Contract, authorization, fuzz, and abuse tests.

### [ ] P3-T002 — Implement MCP client and LangGraph boundary

- Dependencies: P3-T001, P2-T009.
- Implement: Typed client, tool registry, deadlines, cancellation, bounded retries, circuit breaking, result validation, and normalized errors.
- Acceptance: Graph nodes never call integrations directly and cannot consume unvalidated tool output.
- Verify: Adapter tests and simulated timeout/corruption/outage tests.

### [ ] P3-T003 — Implement MCP audit and approval policies

- Dependencies: P3-T001.
- Implement: Immutable call records, actor/session/tenant, redacted inputs/outputs, latency, result, retry lineage, approval state, and policy decision.
- Acceptance: Every MCP call and high-impact decision can be reconstructed without exposing secrets.
- Verify: Audit completeness and redaction tests.

### [ ] P3-T004 — Implement knowledge ingestion pipeline

- Dependencies: P1-T002.
- Implement: Approved-source registry, fetch/import, malware/type checks, cleaning, deduplication, chunking, metadata, embedding, versioning, tombstones, and reindex jobs.
- Acceptance: Only approved, current sources are searchable; deleted or superseded content is removed predictably.
- Verify: Reproducible ingest, update, deletion, and poison-document tests.

### [ ] P3-T005 — Implement pgvector retrieval

- Dependencies: P3-T004.
- Implement: Tenant/source filters, hybrid retrieval, reranking, relevance thresholds, result caps, source metadata, and query observability.
- Acceptance: Retrieval respects isolation and returns traceable evidence within its latency budget.
- Verify: Relevance benchmark, tenant-leak test, explain/query-plan review, load test.

### [ ] P3-T006 — Implement Knowledge MCP tools

- Dependencies: P3-T001, P3-T005.
- Implement: `knowledge.search`, `product.search`, feature, integration, competitor, and security tools with typed results and citations.
- Acceptance: Low-confidence or missing evidence returns an explicit miss, never an inferred business fact.
- Verify: Contract tests and curated question benchmark.

### [ ] P3-T007 — Implement Sales MCP read tools

- Dependencies: P3-T001.
- Implement: Authoritative pricing quote, plan comparison, qualification, and next-action tools with effective dates, currency, region, and source/version metadata.
- Acceptance: No live pricing is embedded in prompts or source code; stale pricing is rejected or clearly bounded.
- Verify: Contract, boundary, stale-data, and currency/region tests.

### [ ] P3-T008 — Implement prompt-injection and data-exfiltration defenses

- Dependencies: P3-T002, P3-T004, P3-T006.
- Implement: Treat retrieved/tool content as untrusted data, instruction separation, allowlisted tool routing, least privilege, output filtering, and canary tests.
- Acceptance: Documents or tool payloads cannot redirect policies, expose unrelated tenant data, or trigger side effects.
- Verify: Adversarial corpus and exfiltration tests.

### [ ] P3-T009 — Implement freshness, cache, and degradation strategy

- Dependencies: P3-T006, P3-T007.
- Implement: Per-tool cache policy, provenance, freshness windows, invalidation, stale-if-safe rules, provider health, and explicit fallback/escalation behavior.
- Acceptance: Pricing failure never produces invented values; knowledge misses clarify or escalate; degraded state is visible.
- Verify: Expiry, invalidation, provider-outage, and fallback tests.

### [ ] P3-T010 — Establish MCP/RAG production SLOs

- Dependencies: P3-T001–P3-T009.
- Implement: Metrics, traces, dashboards, alerts, capacity tests, error budgets, and runbooks for latency, errors, retrieval quality, index freshness, and policy denials.
- Acceptance: Approved service-level objectives are measured under expected and peak load.
- Verify: Load/failure exercise and alert/runbook drill.

## Phase gate

All facts are tool-grounded and traceable; isolation and injection defenses pass; pricing and knowledge failures degrade safely; MCP and retrieval meet approved quality, latency, and availability targets.

