# Architecture Decision Record

## Purpose and authority

This document records consequential architecture decisions for the Knotic Sales Agent. It refines, but does not override, `REQUIREMENTS.md` or `System_Design.md`. API, data, and MCP contracts created by later Phase 0 tasks must implement these decisions.

## Decision lifecycle

Statuses are `PROPOSED`, `ACCEPTED`, `SUPERSEDED`, or `REJECTED`. An accepted decision is binding until a replacement ADR is accepted. Each replacement must identify the superseded decision, migration plan, compatibility impact, owner, approval date, and verification evidence.

Owners below are accountable engineering roles because named human and on-call ownership is established by `P0-T010`. The role owner must approve changes with every affected downstream contract owner.

## Decision register

| ID | Decision | Status | Owner |
|---|---|---|---|
| ADR-001 | Service boundaries and dependency direction | ACCEPTED | Architecture owner |
| ADR-002 | Deployment topology and network trust zones | ACCEPTED | Platform owner |
| ADR-003 | Human, browser, workload, and resource identity | ACCEPTED | Security owner |
| ADR-004 | Model, voice, embedding, and provider strategy | ACCEPTED | AI platform owner |
| ADR-005 | Realtime voice path and interruption authority | SUPERSEDED by ADR-011 | Realtime voice owner |
| ADR-006 | State ownership and consistency model | ACCEPTED | Data platform owner |
| ADR-007 | Failure, retry, and transactional truth strategy | ACCEPTED | Reliability owner |
| ADR-008 | Secrets, rotation, and cryptographic boundaries | ACCEPTED | Security owner |
| ADR-009 | Observability, audit, and data-minimization baseline | ACCEPTED | Observability owner |
| ADR-010 | Contract evolution and provider replacement | ACCEPTED | Architecture owner |
| ADR-011 | Agora-managed realtime media plane | ACCEPTED | Realtime voice owner |

---

## ADR-001 — Service boundaries and dependency direction

- **Status:** ACCEPTED
- **Owner:** Architecture owner
- **Date:** 2026-08-17

### Context

The product combines browser media, application APIs, adaptive workflow, governed business tools, active state, durable records, and semantic retrieval. Blurring these responsibilities would expose credentials, allow model-driven side effects to bypass policy, and make independent scaling or failure isolation difficult.

### Decision

Use the following authoritative boundaries:

1. The Next.js frontend owns browser UI, device state, and the Agora Web SDK. It calls only versioned Flask APIs and Agora client APIs using short-lived credentials.
2. Flask owns public application APIs, user/session authorization, Agora token issuance, session composition, and invocation of LangGraph.
3. LangGraph modules inside the backend own adaptive sales decisions and typed state transitions. They are pure with respect to provider access and consume only validated repository or MCP results.
4. Agora Conversational AI is the managed realtime media plane. Flask controls agent lifecycle and exposes a private OpenAI-compatible LangGraph boundary; the managed agent does not own sales policy, durable truth, or business tools.
5. The MCP service owns service authentication, authorization policy, schema validation, audit, knowledge access, and business-action adapters. Logical Sales, Knowledge, and Integration namespaces may share this one physical deployment until scaling or isolation evidence justifies a split.
6. Redis and PostgreSQL are accessed through typed backend or MCP repositories. pgvector is a PostgreSQL capability accessed through the Knowledge MCP boundary. The LLM never receives arbitrary SQL or direct provider credentials.

Allowed dependency direction is:

```text
Browser -> Flask API -> LangGraph -> typed MCP client -> MCP gateway -> providers
              |             |                              |
              |             +-> typed state repositories   +-> controlled repositories
              +-> Agora token service

Agora RTC <-> Agora managed agent <-> private Flask/LangGraph turn interface
```

Synchronous reverse dependencies and direct browser-to-MCP/database/provider calls are prohibited. Cross-service calls use versioned contracts and correlation metadata.

### Consequences

- Components can scale and deploy independently while preserving one business-policy authority.
- Managed-agent lifecycle and callback authentication require versioned private contracts and provider smoke tests.
- MCP adds a network hop, but centralizes policy, validation, timeout, and audit controls.
- Repository interfaces and contract tests are required to prevent boundary erosion.

### Rejected alternatives

- **Single Next.js full-stack application:** rejected because it conflicts with the mandatory Flask/LangGraph/MCP stack and mixes browser and server trust zones.
- **Direct LangGraph provider integrations:** rejected because actions could bypass MCP policy and audit.
- **One large service including media processing:** rejected because long-lived media workloads have different scaling and failure characteristics from request/response APIs.
- **One physical MCP service per namespace on day one:** rejected until isolation, ownership, or scaling evidence outweighs operational cost.

---

## ADR-002 — Deployment topology and network trust zones

- **Status:** ACCEPTED
- **Owner:** Platform owner
- **Date:** 2026-08-17

### Context

Local development needs a reproducible topology, while production needs independent scaling, private data services, controlled ingress, safe rollout, and no dependence on machine-local state. No cloud vendor is mandated by the product requirements.

### Decision

Deploy immutable, non-root OCI containers for the frontend, Flask API/event delivery processes, and MCP gateway. Agora supplies the managed realtime media plane. Local and CI use Docker Compose. Production uses a managed container platform selected per environment without changing application contracts.

- Only the frontend and Flask ingress are publicly reachable through TLS. The frontend may also establish its authenticated Agora RTC connection.
- The MCP gateway, Redis, PostgreSQL/pgvector, metrics endpoints, and administrative interfaces remain on private networks.
- PostgreSQL with pgvector and Redis use managed, zone-redundant services in staging and production when the selected platform offers compatible versions and recovery controls. Self-managed production data services require an approved exception and tested backup/failover evidence.
- Application containers are stateless. Active state is in Redis; authoritative durable state is in PostgreSQL; provider truth remains at the authoritative external provider.
- Environments use separate accounts/projects, networks, databases, Redis instances, Agora projects, OpenAI projects, encryption keys, and secret namespaces.
- Rolling or blue/green releases require readiness checks, backward-compatible contracts and migrations, bounded drain time for API requests, and graceful media-session drain for voice workers.
- Outbound access is allowlisted to Agora, OpenAI, the approved identity provider, and configured MCP business providers. Database and cache access is identity/network restricted.

The specific production cloud and regions are deployment parameters, not application dependencies. `P0-T008` must record the selected platform, supported service versions, region/data-residency review, immutable image digests, capacity assumptions, and rollback mechanism before a production environment is approved.

### Consequences

- The application remains portable and local development mirrors process boundaries.
- Production deployment is blocked until `P0-T008` supplies and verifies the environment-specific platform profile.
- Agora media sessions scale independently from Flask request and SSE delivery processes.
- Managed data services reduce operational risk but introduce provider cost and compatibility review.

### Rejected alternatives

- **Public MCP or database endpoints:** rejected because application ingress is the only justified public server boundary.
- **Serverless functions for long-lived voice sessions:** rejected as the primary voice runtime because connection duration and media state require predictable lifecycle control.
- **Shared staging/production data plane:** rejected because it defeats blast-radius, credential, and retention separation.
- **Binding application code to one cloud SDK:** rejected; infrastructure may be provider-specific, application contracts may not be.

---

## ADR-003 — Human, browser, workload, and resource identity

- **Status:** ACCEPTED
- **Owner:** Security owner
- **Date:** 2026-08-17

### Context

The system needs to distinguish a human operator or customer, a browser session, an Agora participant, and each service workload. Authorization must not depend on user-supplied identifiers, and internal network location alone is not identity.

### Decision

- Use an OpenID Connect identity provider with Authorization Code plus PKCE for human authentication. The provider is replaceable through standards-based claims; production configuration must set an exact issuer, audience, and signing-key source.
- Flask is the browser authorization boundary. It validates issuer, audience, signature, expiry, nonce/state, and required claims, then uses a server-managed session in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Browser storage must not contain long-lived access, refresh, OpenAI, MCP, or provider credentials.
- Flask issues short-lived, least-privilege Agora RTC tokens bound to the authorized application session, channel, participant UID, role, and expiry. The Agora App Certificate remains server-only.
- Service-to-service authentication uses workload identity with short-lived tokens or mutually authenticated certificates when the deployment platform supports it. Until that integration exists, the backend-to-MCP boundary uses the rotating high-entropy bearer-token pair defined in `CONFIGURATION.md`, only over TLS and only on the private network.
- Every request carries immutable server-derived `actor_id`, `tenant_id`, `session_id`, and `correlation_id` where applicable. External provider subject IDs are mapped to internal stable IDs rather than reused as primary keys.
- Authorization is deny-by-default and checked at Flask and again at MCP for side effects. Tenant/resource ownership comes from trusted identity context, never request-body claims. High-impact actions additionally require deterministic policy and recorded approval.

### Consequences

- API and MCP contracts must define authentication failures distinctly from authorization and policy-denial failures.
- OIDC integration and secure browser-session lifecycle must be tested before public access.
- Workload identity is platform-specific at deployment time; the application consumes a common credential interface.
- The bootstrap MCP bearer token is a transitional production control with rotation overlap, not permission to trust the network.

### Rejected alternatives

- **Long-lived JWTs in local storage:** rejected due to exfiltration and revocation risk.
- **Agora App Certificate or provider key in the browser:** rejected because server credentials may never enter frontend bundles or runtime.
- **Trusting caller-supplied tenant/session IDs:** rejected because it enables cross-tenant access.
- **Network location as sole service authentication:** rejected because private networking limits reachability but does not establish workload identity.

---

## ADR-004 — Model, voice, embedding, and provider strategy

- **Status:** ACCEPTED
- **Owner:** AI platform owner
- **Date:** 2026-08-17

### Context

The workflow needs structured reasoning and tool selection, the voice path needs low-latency speech input/output and interruption support, and RAG needs stable embeddings. Model aliases and provider behavior can change; business correctness cannot depend on unvalidated natural-language output.

### Decision

Use OpenAI behind typed, provider-neutral ports as the initial AI provider:

| Capability | Initial production baseline | Required boundary |
|---|---|---|
| Turn understanding, structured extraction, response planning | OpenAI Responses API with `gpt-5.6-terra` | Backend model port; schema-validated output |
| Realtime speech interface | OpenAI Realtime API with `gpt-realtime-2.1` | Realtime voice adapter; no direct business actions |
| Knowledge embeddings | OpenAI Embeddings API with `text-embedding-3-large` | Offline/controlled ingestion adapter; vector dimension recorded with each index version |

Model IDs, reasoning effort, timeouts, token limits, voice, and regional endpoint are server configuration. Production must use an explicitly approved model revision where the provider exposes one; mutable aliases require the same evaluation and rollout gate as a model upgrade. Prompts, output schemas, safety settings, and retrieval index versions are versioned artifacts.

LangGraph remains the business orchestration authority. Realtime speech models may transcribe, synthesize, manage acoustic turn detection, and render approved response text, but may not independently quote prices, invoke MCP actions, confirm transactions, alter qualification, or decide handoff. Model-proposed intents and fields are untrusted until Pydantic/schema validation and deterministic policy checks pass.

Before a model or provider change reaches production, run a fixed evaluation suite for intent/objection coverage, requirement replacement, qualification boundaries, grounded pricing, booking truth, escalation, latency, interruption, safety, and cost. Roll out by environment and then canary; retain a tested rollback configuration. Provider requests use project-scoped credentials, least retention available for the approved account, and the environment's data-residency configuration.

### Consequences

- Initial implementation can use one provider while adapters and contracts preserve replacement options.
- OpenAI SDK dependencies and configuration are intentionally deferred until the task that implements each adapter.
- Realtime output must be constrained by LangGraph-approved content, which adds coordination but prevents a second uncontrolled sales brain.
- Embedding model or dimension changes require a new index version and re-embedding; indexes with different dimensions/models cannot be silently mixed.
- Cost, rate limits, model availability, data processing, and regional support must be verified for the deployment account before release.

### Rejected alternatives

- **Model-selected provider or model at runtime:** rejected because it prevents deterministic evaluation, cost control, and audit.
- **Direct model access to SQL, CRM, calendar, or pricing:** rejected because MCP and deterministic policies own those boundaries.
- **Realtime model as the sole conversation state:** rejected because structured Redis/PostgreSQL state and LangGraph are authoritative.
- **Prompt constants for product/pricing facts:** rejected because facts must come from trusted tools.
- **Unversioned automatic model upgrades:** rejected because behavior could change without evaluation.

### Provider references

Provider capabilities were verified against official OpenAI documentation on 2026-08-17:

- [`gpt-5.6-terra`](https://developers.openai.com/api/docs/models/gpt-5.6-terra): Responses API, function calling, and structured outputs.
- [`gpt-realtime-2.1`](https://developers.openai.com/api/docs/models/gpt-realtime-2.1): realtime audio/text input and output, interruption-oriented voice behavior, and tool support.
- [`text-embedding-3-large`](https://developers.openai.com/api/docs/models/text-embedding-3-large): multilingual text embeddings for retrieval.

These references establish capability, not account availability or production approval.

---

## ADR-005 — Realtime voice path and interruption authority

- **Status:** SUPERSEDED by ADR-011
- **Owner:** Realtime voice owner
- **Date:** 2026-08-17

### Context

Agora is the mandatory RTC layer. The product must stop AI playback when the customer interrupts, prioritize the new turn, record the truncated response, and resume the old topic only when still relevant.

### Decision

Use one Agora channel per call and stable server-generated participant identities. The browser publishes customer audio and subscribes to AI audio through Agora. The realtime voice worker joins as a restricted service participant, consumes customer audio, streams it to the configured speech adapter, and publishes synthesized AI audio back to Agora.

The voice worker emits versioned semantic-turn events to Flask. Flask invokes the same LangGraph thread identity for every turn. LangGraph returns approved response text and response metadata; the voice adapter renders it. Media frames are not routed through ordinary Flask HTTP workers.

Barge-in authority is deterministic and lives in the voice worker using Agora/local voice activity plus provider events:

1. Detect customer speech while AI output is active.
2. Stop local/provider generation and Agora publication immediately.
3. Emit an immutable interruption event with response ID, played/truncated offsets, detection timestamps, and correlation ID.
4. Prioritize and transcribe the customer turn.
5. Let LangGraph decide from structured state whether the interrupted topic remains relevant.

Each response and turn carries monotonically ordered per-session sequence numbers. Late audio, transcription, or model events for a cancelled response are discarded idempotently. Raw audio is ephemeral by default; recording requires an explicit policy, consent, encrypted storage, retention, and deletion implementation.

### Consequences

- Voice-worker capacity, channel admission, sequencing, cancellation, and drain behavior require dedicated integration/load tests.
- Agora remains the only browser media plane; the browser never connects directly to OpenAI.
- Barge-in does not depend on an LLM decision, reducing interruption latency and avoiding race-driven double playback.
- Semantic events and interrupted-response metadata become required API/data contract concepts.

### Rejected alternatives

- **Browser-to-OpenAI media connection:** rejected because it creates a second media/control plane and complicates server policy and credential isolation.
- **Proxying audio through Flask request workers:** rejected because request/response workers are unsuitable for continuous media.
- **Waiting for sentence completion before stopping playback:** rejected because it violates FR-02.
- **Letting the realtime model resume automatically:** rejected because LangGraph and current structured state determine relevance.

---

## ADR-006 — State ownership and consistency model

- **Status:** ACCEPTED
- **Owner:** Data platform owner
- **Date:** 2026-08-17

### Context

Realtime turns need low latency, while requirements, provider actions, audit events, and outcomes must survive cache loss and retries. The same call can produce concurrent media, API, tool, and recovery events.

### Decision

- Redis owns hot `SalesState`, active call leases, short-lived idempotency results, sequence/cancellation state, and recent tool results. Redis is never the sole durable record.
- PostgreSQL owns authoritative sessions, leads, messages, requirement history, objections, qualification snapshots, meetings, follow-ups, tool calls, outcomes, and append-only audit events.
- pgvector stores versioned knowledge chunks and embeddings in PostgreSQL. Mutable transactional truth is not embedded as authoritative knowledge.
- Each session has one logical LangGraph writer at a time, enforced by an expiring lease or compare-and-set version. State updates include an expected version; conflicts reload and retry only when safe.
- The latest confirmed requirement becomes active while the old/new transition is durably appended as `REQUIREMENT_UPDATED` in the same database transaction.
- Side-effect workflows use a transactional outbox/inbox pattern. A database commit records intended external work before delivery; provider callbacks/results are idempotently reconciled.
- Redis reconstruction comes from durable PostgreSQL state plus explicitly replayable events, not raw transcript alone.

### Consequences

- `P0-T006` must define authoritative fields, versions, keys, TTLs, transaction boundaries, retention, and rebuild behavior.
- Eventual consistency is allowed between Redis projections and PostgreSQL, but externally reported transactional success must come from authoritative provider confirmation persisted in PostgreSQL.
- Leases, optimistic concurrency, and outbox delivery add complexity but prevent duplicate turns and lost business actions.

### Rejected alternatives

- **Redis as system of record:** rejected because cache loss must not erase durable sales/audit state.
- **Raw transcript as the only memory:** rejected by FR-04 and the repository rules.
- **Last-write-wins without version checks:** rejected because concurrent turn/tool events could silently overwrite state.
- **Distributed two-phase commit with external providers:** rejected because most providers do not participate; idempotency and reconciliation are required instead.

---

## ADR-007 — Failure, retry, and transactional truth strategy

- **Status:** ACCEPTED
- **Owner:** Reliability owner
- **Date:** 2026-08-17

### Context

The system depends on RTC, AI, MCP, databases, cache, CRM, calendar, and knowledge retrieval. Retries can duplicate side effects, and fabricated success would violate core product rules.

### Decision

Classify failures as validation, authentication, authorization/policy, conflict, rate limit, timeout, transient dependency, permanent dependency, or internal. API and MCP contracts must preserve this classification in a safe error envelope without leaking credentials or provider internals.

- Validate before any side effect. Every side-effecting command carries a server-generated idempotency key scoped to tenant, operation, and canonical request.
- Retry only transient failures, using exponential backoff with jitter, a bounded attempt count, and an overall deadline. Honor provider retry hints. Never retry validation, authorization, policy, or confirmed permanent failures.
- Reads may use bounded retries and, only where explicitly allowed, freshness-labelled cached data. Pricing and availability cannot silently fall back to stale or prompt-embedded values.
- Circuit breakers and concurrency limits protect each external dependency. Bulkheads isolate voice, model, MCP domain, database, and provider capacity.
- Calendar success requires provider confirmation; ambiguous timeouts remain `PENDING_CONFIRMATION` and are reconciled before another booking attempt. CRM failures create durable pending work. Pricing failures yield a limitation/clarification. RAG misses yield clarification or escalation. Voice disconnect attempts bounded recovery and then closes gracefully.
- Human-facing responses distinguish accepted/pending/confirmed/failed states. No transaction is described as successful until the authoritative provider result is validated and persisted.

### Consequences

- Contracts and data models need explicit pending and reconciliation states, provider references, attempt records, and idempotency uniqueness.
- Safe degradation may reduce feature availability rather than invent an answer.
- Reliability policies require metrics, dead-letter/pending-work review, and operator runbooks.

### Rejected alternatives

- **Retry every error:** rejected because it amplifies outages and duplicates side effects.
- **Optimistic success before provider confirmation:** rejected by FR-12 and the non-fabrication rules.
- **Silent fallback to model knowledge for failed pricing/RAG:** rejected because business facts must be grounded.
- **Unbounded queues or retries:** rejected because they hide failure and consume capacity indefinitely.

---

## ADR-008 — Secrets, rotation, and cryptographic boundaries

- **Status:** ACCEPTED
- **Owner:** Security owner
- **Date:** 2026-08-17

### Context

The repository has a deployment-neutral `SecretProvider`, startup validation, redaction, and MCP token overlap, but production delivery and rotation authority must be explicit.

### Decision

- The production container platform's managed secret manager is the source of secret values. Workloads authenticate to it using platform workload identity, not a stored bootstrap key. An adapter implements the existing `SecretProvider` allowlist.
- Secret values are injected or fetched at runtime, never baked into images, source, lockfiles, manifests, frontend variables, command lines, or build logs. Environment variables are permitted only as the local/CI adapter and as a platform injection mechanism where process inspection is appropriately restricted.
- TLS is required for all non-local connections. Database, Redis, OIDC, Agora, OpenAI, and business-provider credentials are separate per environment and least-privilege.
- MCP token rotation uses current/previous overlap exactly as documented in `CONFIGURATION.md`, with constant-time comparison, expiry, usage metrics, and removal after the maximum request/session lifetime. Database/Redis/Agora/OpenAI credentials use provider-supported overlapping credentials or blue/green rollout and connection-pool renewal.
- Key/secret access, version activation, revocation, and emergency rotation are auditable administrative events. No secret value or reversible derivative is logged.
- Application data encryption uses provider-managed encryption at rest initially; production approval requires documented key ownership, rotation, backup encryption, and restoration evidence. Fields requiring application-level encryption are selected during the `P0-T006` threat/retention review.

### Consequences

- `P0-T008` must implement and name the environment's secret-manager adapter; a production deployment cannot rely solely on `.env` files.
- Local Compose remains simple through explicit local environment injection while production uses workload identity.
- Rotation tests and redacted metrics are release requirements.

### Rejected alternatives

- **Committed encrypted secrets:** rejected because repository access and decryption-key distribution create unnecessary exposure.
- **One shared secret across environments/services:** rejected due to blast radius and weak attribution.
- **Permanent MCP bearer credential:** rejected; overlap exists only to support bounded rotation.
- **Logging hashes of secrets for diagnosis:** rejected because stable derivatives can still expose correlation and offline-guessing risk.

---

## ADR-009 — Observability, audit, and data-minimization baseline

- **Status:** ACCEPTED
- **Owner:** Observability owner
- **Date:** 2026-08-17

### Context

Low-latency voice and multi-service actions require cross-service diagnosis, but transcripts, audio, identity, and provider payloads can contain sensitive data. Audit records and operational telemetry serve different purposes and lifecycles.

### Decision

- Instrument services with OpenTelemetry-compatible traces and metrics and structured JSON logs. Export through an OpenTelemetry Collector so the storage backend remains replaceable. `P0-T008` must name the production log/trace/metric backend for its selected platform.
- Propagate W3C trace context plus `correlation_id`, `tenant_id`, `session_id`, `turn_id`, `tool_call_id`, and provider request IDs where available. Public error IDs correlate to internal records without exposing stack traces.
- Record latency spans for Agora ingress, speech recognition, Flask/LangGraph, each MCP call, model generation, speech synthesis, and Agora playback. Measure interruption detection-to-stop latency separately.
- Immutable business audit events record actor/workload, action, target, policy/approval decision, idempotency key, timestamps, input/output schema versions, result status, and references to redacted payloads. Audit is stored durably in PostgreSQL and may be exported to append-protected storage.
- Operational logs exclude raw audio, secrets, authorization headers, cookies, full connection URLs, unrestricted prompts/transcripts, and unbounded provider payloads. Allowlisted structured fields are preferred; required text is minimized/redacted and governed by retention policy.
- Metrics must not use customer, tenant, session, request, or provider IDs as unbounded labels. Sampling may apply to traces but never discard required audit events.

### Consequences

- The collector decouples instrumentation from a vendor but adds infrastructure.
- Separate operational and audit pipelines prevent log retention changes from destroying business evidence.
- Some debugging requires access-controlled durable records rather than verbose logs.
- `P0-T010` and Phase 6 must define retention, access, dashboards, alerts, SLOs, and incident procedures.

### Rejected alternatives

- **Provider-specific logging calls throughout application code:** rejected because they create lock-in and inconsistent correlation.
- **Full transcript/prompt logging by default:** rejected due to data minimization and credential/PII leakage risk.
- **Metrics-only monitoring:** rejected because distributed voice/tool failures require traces and structured events.
- **Using application logs as the audit ledger:** rejected because logs may be sampled, rotated, or mutable.

---

## ADR-010 — Contract evolution and provider replacement

- **Status:** ACCEPTED
- **Owner:** Architecture owner
- **Date:** 2026-08-17

### Context

Frontend, Flask, voice workers, LangGraph, MCP, persistence, and providers will evolve independently. Provider-specific payloads or silent breaking changes would couple deployments and make rollback unsafe.

### Decision

- Public APIs, internal turn events, durable events, MCP tools, prompts, and knowledge indexes are explicitly versioned.
- Additive changes are preferred. A breaking contract receives a new major version and a documented compatibility window; producers deploy compatibility before consumers migrate.
- Provider adapters translate provider payloads into canonical typed domain models. Provider request/response objects may not cross the adapter boundary or become durable domain schemas.
- Contract tests verify both producers and consumers. Database migrations are expand/migrate/contract and must remain compatible with the previous application release during rolling deployment.
- A provider replacement must pass the same security, data-processing, latency, correctness, failure, cost, and rollback gates as the original provider. It must not bypass Agora, LangGraph, MCP, or data ownership decisions.

### Consequences

- Later contract documents must define compatibility and deprecation behavior, not only current schemas.
- Adapters add translation code but confine vendor changes and enable controlled comparison.
- Breaking changes take multiple releases but preserve rollback and independent delivery.

### Rejected alternatives

- **Expose provider-native payloads as application contracts:** rejected because provider changes would become system-wide breaking changes.
- **In-place breaking schema changes:** rejected because independently deployed consumers and rollback would fail.
- **Dual-write forever:** rejected; migrations require an owner, observation window, and bounded removal date.

---

## Architecture traceability

| Source requirement/design area | Binding decisions |
|---|---|
| Mandatory React/Next.js, Agora, Flask, LangGraph, MCP stack | ADR-001, ADR-005 |
| Canonical service and data plane | ADR-001, ADR-002, ADR-006 |
| Runtime turn loop and LangGraph reinvocation | ADR-001, ADR-004, ADR-005, ADR-006 |
| MCP logical domains and governed business actions | ADR-001, ADR-007, ADR-010 |
| RAG and authoritative transactional facts | ADR-001, ADR-004, ADR-006, ADR-007 |
| Structured human handoff | ADR-001, ADR-003, ADR-007, ADR-009 |
| Canonical failure principles | ADR-005, ADR-007, ADR-009 |
| FR-01 realtime voice | ADR-002, ADR-004, ADR-005 |
| FR-02 interruption/barge-in | ADR-005, ADR-009 |
| FR-03, FR-04, FR-05, FR-06, FR-07 adaptive workflow and memory | ADR-001, ADR-004, ADR-006 |
| FR-08 grounded business facts | ADR-001, ADR-004, ADR-007 |
| FR-09, FR-10 qualification and action | ADR-001, ADR-004, ADR-007 |
| FR-11 CRM | ADR-001, ADR-006, ADR-007 |
| FR-12 calendar confirmation | ADR-006, ADR-007 |
| FR-13 human escalation | ADR-001, ADR-003, ADR-007 |
| FR-14 outcomes | ADR-006, ADR-009 |
| Low latency and safe failure | ADR-002, ADR-005, ADR-007, ADR-009 |
| Auditability | ADR-003, ADR-006, ADR-008, ADR-009 |
| Server-side secrets | ADR-003, ADR-008 |
| Modular provider integrations | ADR-001, ADR-004, ADR-010 |

## Required follow-through

- `P0-T005` must define the authenticated public API, internal semantic-turn events, errors, idempotency, and compatibility rules.
- `P0-T006` must define state authority, optimistic versions, events, outbox/inbox records, retention, and encryption decisions.
- `P0-T007` must define MCP identity, policy, approval, schemas, transactional states, retry, and audit semantics.
- `P0-T008` must select and document the production platform profile, secret-manager adapter, managed data services, observability backend, network controls, and rollout/drain behavior.
- `P0-T009` must enforce contract, architecture, dependency, secret, and security checks in CI.
- Model/provider availability, regional processing, account limits, and data terms remain deployment approval checks; this document does not claim they are enabled for a particular account.

---

## ADR-011 — Agora-managed realtime media plane

- **Status:** ACCEPTED
- **Owner:** Realtime voice owner
- **Date:** 2026-09-06

### Decision

Agora Conversational AI Engine replaces the self-hosted media worker. The browser joins one server-derived RTC channel, publishes customer audio, subscribes to the managed agent's remote audio, renews short-lived RTC tokens, and stops playback on hang-up. Flask alone creates and stops agents using server-held Agora REST credentials.

The managed chain uses Agora ARES speech recognition, Agora voice activity/interruption handling, and OpenAI text-to-speech. Its LLM target is a private, authenticated, OpenAI-compatible Flask endpoint backed by the existing LangGraph turn executor. Pricing, qualification, policy checks, MCP calls, transaction confirmation, and durable workflow transitions remain backend authority. Provider callbacks are untrusted input and require signature verification, replay protection, tenant/session resolution, and durable deduplication before they can alter projections.

No Agora certificate, Agora REST credential, OpenAI key, private LLM key, or webhook secret enters browser configuration. Agent start/stop results are shown as confirmed only after Agora acknowledges them. Staging and production startup fail unless the complete managed-agent secret set and HTTPS private LLM URL are configured.

### Consequences

- The old self-hosted voice-worker deployment is retired; Flask API and SSE/event relay processes remain independently scalable.
- Real Agora staging calls, quota review, callback verification, inactivity cleanup, and the private LangGraph streaming adapter remain mandatory release evidence.
- Local/CI tests mock only the provider boundary and must never be used as proof of provider availability.
