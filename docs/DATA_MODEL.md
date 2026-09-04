# Durable and Active Data Model

## Purpose and authority

This document defines data ownership, identifiers, relationships, invariants, lifecycle, and migration rules for version 1. It implements `REQUIREMENTS.md`, `System_Design.md`, `ARCHITECTURE_DECISIONS.md`, and the wire shapes in `API_CONTRACTS.md`. It does not create physical migrations; migration implementation begins with the owning feature tasks.

PostgreSQL is authoritative for durable business state and audit history. Redis is a reconstructable active-state projection. pgvector stores versioned semantic knowledge derived from approved documents. External CRM/calendar/messaging providers remain authoritative for their confirmed transactions; PostgreSQL stores the validated local record and reconciliation state.

## Global conventions

### Identifiers

- Public and cross-service identifiers are application-generated RFC 9562 UUIDv7 values stored as PostgreSQL `uuid`. PostgreSQL 17 does not generate them; application generation is required and randomness/clock rollback is tested.
- All identifiers are opaque on the wire. They are never derived from email, provider subject, channel name, or other customer data.
- Every tenant-owned table contains `tenant_id uuid not null`. Parent tables expose `unique (tenant_id, id)` and child relationships use composite foreign keys `(tenant_id, parent_id)` to make cross-tenant references impossible.
- Provider identifiers are stored separately from internal IDs, encrypted when sensitive, and unique within `(tenant_id, provider, provider_external_id_hmac)` where lookup is required.
- Redis and event IDs use the same canonical lowercase UUID string representation.

### Types and naming

- PostgreSQL schemas, tables, columns, constraints, and indexes use unquoted lowercase `snake_case`.
- Timestamps use `timestamptz` in UTC. Durations use non-negative `bigint` milliseconds. Scores use constrained `smallint`. Money uses `numeric(19,4)` plus ISO 4217 currency text; floating point is prohibited.
- Lifecycle/status values use `text` with named `check` constraints. PostgreSQL enum types are avoided so additive rollouts do not require blocking enum changes.
- Typed columns own fields used for policy, joins, filtering, ordering, uniqueness, or lifecycle. `jsonb` is limited to bounded versioned snapshots, citations, redacted provider metadata, and event payloads validated before persistence.
- Every mutable row has `created_at`, `updated_at`, and `version bigint not null default 1`. Updates use `where version = :expected_version` and atomically increment the version.
- Soft deletion uses `deleted_at` only where recovery or provider reconciliation is required. Erasure jobs physically delete or irreversibly anonymize according to the retention table.

### Tenant isolation and database roles

- Application connections never use the database owner or superuser. Separate `knotic_migrator`, `knotic_api`, `knotic_mcp`, `knotic_worker`, and `knotic_readonly` roles receive only required schema/table/sequence privileges.
- Tenant-owned tables enable and force row-level security. Each transaction sets `app.tenant_id` and, where relevant, `app.actor_id` using trusted Flask/MCP identity context. Missing or invalid context denies access.
- RLS is defense in depth; repositories still include tenant predicates. Migrations and background purge jobs use narrowly scoped roles with audited elevation.
- `public` receives no schema/table privileges. Database URLs and encryption keys remain server-only.

## PostgreSQL entity catalog

All foreign-key columns are indexed. Composite indexes put equality/tenant/status columns before range/order columns. Indexes listed below are minimum query-support indexes; migration reviews must confirm them with real query plans.

### Identity and tenancy

| Entity | Authoritative fields | Relationships and minimum indexes | Lifecycle |
|---|---|---|---|
| `tenants` | `id`, `slug`, `status`, `retention_policy_version`, timestamps/version | unique active `slug`; referenced by all tenant data | Retained while account exists; tombstone after erasure |
| `actors` | `id`, `tenant_id`, `actor_type`, encrypted `display_name`, `status`, timestamps/version | unique `(tenant_id,id)`; index `(tenant_id,status,id)` | Erase direct identity; retain anonymous audit actor ID |
| `actor_identities` | `id`, `tenant_id`, `actor_id`, `issuer`, encrypted subject, subject HMAC, timestamps | unique `(tenant_id,issuer,subject_hmac)`; FK actor; no tokens | Removed on identity unlink/account erasure |
| `leads` | `id`, `tenant_id`, encrypted name/email/phone/company/role, lookup HMACs, `crm_status`, qualification summary, timestamps/version | index `(tenant_id,crm_status,updated_at,id)` | Default 365 days after last activity unless tenant/legal policy overrides |
| `provider_links` | `id`, `tenant_id`, `lead_id`, `provider`, encrypted external ID, external ID HMAC, sync status/error code, timestamps/version | unique `(tenant_id,provider,external_id_hmac)`; partial index for pending sync | Delete with lead after reconciliation/tombstone window |

OIDC authorization codes, access tokens, refresh tokens, PKCE verifiers, Agora tokens, and provider secrets are never stored in these tables. Server session data uses a dedicated encrypted session store introduced with authentication implementation.

### Conversation and structured sales state

| Entity | Authoritative fields | Relationships and minimum indexes | Lifecycle |
|---|---|---|---|
| `sales_sessions` | `id`, `tenant_id`, optional `lead_id`, `status`, `locale`, `timezone`, `current_topic`, `current_intent`, `buying_stage`, `qualification_score`, `next_best_action`, encrypted summary/latest request, `outcome`, `started_at`, `ended_at`, timestamps/version | index `(tenant_id,status,updated_at,id)` and `(tenant_id,lead_id,created_at,id)` | Default 365 days after end; terminal status required before purge |
| `calls` | `id`, `tenant_id`, `session_id`, `agora_channel_hmac`, server participant IDs, `status`, join/disconnect/end timestamps, disconnect reason, timestamps/version | unique active call per session; index `(tenant_id,session_id,created_at,id)` | Same as session; raw audio absent by default |
| `messages` | `id`, `tenant_id`, `session_id`, `turn_id`, `response_id`, `sequence`, `speaker`, `source`, encrypted content, locale, provider event reference HMAC, interruption flag, timing, timestamps | unique `(tenant_id,session_id,sequence,speaker)` where applicable; keyset index `(tenant_id,session_id,created_at,id)` | Default 365 days; content erased/anonymized on approved deletion |
| `requirements_current` | `id`, `tenant_id`, `session_id`, `field`, typed value columns (`value_integer`, `value_text`, `value_text_array`, `value_numeric`, `currency`), `confirmed_at`, `confidence`, `source_turn_id`, timestamps/version | unique `(tenant_id,session_id,field)`; exactly one value representation allowed; confidence `0..1` | Deleted with session after history retention |
| `requirement_changes` | `id`, `tenant_id`, `session_id`, `requirement_id`, `field`, typed old/new values, `confirmed`, `source_turn_id`, actor/source metadata, `event_id`, `changed_at` | unique `event_id`; index `(tenant_id,session_id,changed_at,id)` | Immutable; same retention as durable events |
| `objections` | `id`, `tenant_id`, `session_id`, `category`, encrypted detail, `status`, first/latest turn IDs, timestamps/version | unique active category per session; index `(tenant_id,session_id,status,category)` | Same as session |
| `objection_evidence` | `id`, tenant/session/objection/source-turn IDs, category, exact offsets, evidence SHA-256, confidence, risk flags, policy action, escalation decision, detected/created times | unique `(tenant_id,session_id,objection_id,source_turn_id)`; index `(tenant_id,session_id,category,detected_at,id)`; immutable runtime history | Same as session |
| `session_competitors` | `id`, `tenant_id`, `session_id`, normalized name, encrypted context, timestamps/version | unique `(tenant_id,session_id,normalized_name)` | Same as session |
| `qualification_snapshots` | `id`, `tenant_id`, `session_id`, seven constrained component scores, `total_score`, `buying_stage`, explicit override/action, source turn, `calculated_at` | check component sum equals total; index `(tenant_id,session_id,calculated_at,id)` | Immutable; same as session |
| `workflow_turn_checkpoints` | `id`, tenant/session/turn IDs, input hash, execution status/attempt, encrypted graph state plus hash, safe failure fields, bounded lease/commit times | unique `(tenant_id,session_id,turn_id)`; partial expired-lease index; forced RLS | Same as session; committed records are immutable to workflow execution |
| `voice_control_events` | UUIDv7 event/stream IDs, tenant/session IDs, per-stream and session-wide sequences, allowlisted event type, schema version, UTC occurrence time, bounded non-sensitive payload, canonical SHA-256 | unique event ID, `(tenant_id,session_id,stream_id,stream_sequence)`, and `(tenant_id,session_id,server_sequence)`; replay index; forced RLS | Same as session; retention role performs approved deletion |

`requirements_current` is the only authoritative current requirement view. A confirmed change transaction locks the current row, appends `requirement_changes`, updates the typed current value/version, and appends `requirement.updated` to `domain_events`. These writes commit together; latest confirmed value wins only through optimistic version/turn ordering. Unconfirmed model extraction never replaces a confirmed value.

Qualification components are constrained to the FR-09 maxima: need 25, product fit 20, deployment fit 15, timeline 15, authority 10, budget 5, purchase intent 10. Stage is derived from total except a separately recorded explicit customer-request override; raw model-provided totals are not authoritative.

### Tools, operations, integrations, and outcomes

| Entity | Authoritative fields | Relationships and minimum indexes | Lifecycle |
|---|---|---|---|
| `tool_calls` | `id`, `tenant_id`, `session_id`, `turn_id`, logical tool/version, approval level/decision, request schema version/hash, status, attempt count, timeout, MCP/provider refs, safe error, timestamps/version | unique provider/idempotency references; index `(tenant_id,session_id,created_at,id)` and partial pending index | Default 400 days for auditability |
| `tool_results` | `id`, `tenant_id`, `tool_call_id`, result schema version/hash, encrypted/redacted result, confirmation status, provider timestamp, created_at | unique `tool_call_id` for terminal result | Same as tool call |
| `operations` | `id`, `tenant_id`, `actor_id`, optional `session_id`, kind/status, safe result reference, safe error fields, timestamps/version | index `(tenant_id,actor_id,updated_at,id)`; partial `(status,updated_at,id)` | 30 days after terminal; durable business records remain elsewhere |
| `idempotency_records` | `id`, tenant/actor/workload scope, method/path/key HMAC, canonical request hash, status, response status/encrypted body/headers, optional operation ID, expires_at, timestamps | unique scope + key HMAC; partial index on unexpired records | At least 24 hours and never shorter than operation reconciliation window |
| `meetings` | `id`, `tenant_id`, `session_id`, `lead_id`, provider, slot/timezone, status, provider confirmation reference HMAC, idempotency key HMAC, safe error, timestamps/version | unique provider confirmation; partial pending index | Default 400 days; success only after provider confirmation |
| `followups` | `id`, `tenant_id`, `session_id`, `lead_id`, channel, scheduled time, status, provider reference HMAC, attempt/safe error, timestamps/version | partial pending/retry index `(status,scheduled_at,id)` | Default 400 days or tenant policy |
| `handoffs` | `id`, `tenant_id`, `session_id`, reason, status, assigned agent, encrypted structured context, provider reference, timestamps/version | index `(tenant_id,status,created_at,id)` | Default 400 days; context contains all FR-13 fields |
| `session_outcomes` | `id`, `tenant_id`, `session_id`, outcome, source, source reference, assigned_at | unique active outcome per session; immutable corrections append replacement event | Same as session/audit retention |
| `pending_provider_updates` | `id`, `tenant_id`, provider/action, aggregate type/id, encrypted payload, status, attempts, next_attempt_at, lease fields, safe error, timestamps/version | partial queue index `(next_attempt_at,id) where status='PENDING'` | Until terminal plus 90 days; dead-letter requires operator resolution |

`PENDING`, `RUNNING`, `PENDING_CONFIRMATION`, `SUCCEEDED`, `FAILED_RETRYABLE`, `FAILED_PERMANENT`, and `CANCELLED` are distinct where applicable. Provider timeout after a possibly accepted write becomes `PENDING_CONFIRMATION`; it is never converted to success without reconciliation.

### Events, outbox, inbox, and audit

| Entity | Authoritative fields | Relationships and minimum indexes | Lifecycle |
|---|---|---|---|
| `domain_events` | API event envelope: event/type/version/time, tenant/session sequence, correlation/causation, actor, validated payload, payload schema version | unique `event_id`; unique `(tenant_id,session_id,sequence,event_type,event_id)`; keyset index `(tenant_id,session_id,occurred_at,id)` | Append-only, default 400 days |
| `outbox_messages` | `id`, `tenant_id`, event ID, destination/topic, payload schema/version/hash, status/attempts/next attempt/lease, timestamps | unique `(destination,event_id)`; partial claim index | Delete/archive 30 days after confirmed delivery |
| `inbox_receipts` | `id`, `tenant_id`, source, message ID/hash, processed_at, result reference | unique `(source,message_id)` | At least provider replay window, default 90 days |
| `audit_events` | `id`, tenant, actor/workload, action/target, policy/approval, idempotency hash, schema versions, result, correlation/tool/provider refs, redacted metadata, occurred_at | immutable keyset index `(tenant_id,occurred_at,id)`; BRIN may supplement at scale | Append-only, default 400 days; legal/security policy may extend |

External network calls never occur inside a database transaction. The transaction commits domain changes plus outbox work; workers atomically claim eligible rows with `for update skip locked`, set a bounded lease, commit, then call the provider. Reconciliation writes provider results in a new short transaction. Multi-row locks are acquired in ascending UUID order to prevent deadlocks.

Version 1 event lifecycle ownership is explicit:

| Event type | Emitted with | Durable payload authority |
|---|---|---|
| `session.created` | committed session creation | `sales_sessions` identity/version/status |
| `turn.accepted` | committed final text/voice turn | `messages`, session sequence, source |
| `turn.completed` | committed LangGraph response/state transition | `messages`, `sales_sessions`, related structured tables |
| `response.interrupted` | durably accepted voice cancellation | `messages` interruption fields and event timing payload |
| `requirement.updated` | atomic confirmed requirement replacement | `requirements_current` plus `requirement_changes` old/new values |
| `memory.updated` | committed confirmed non-requirement memory change | `leads`, `session_competitors`, or `sales_sessions` current topic plus source turn |
| `objection.updated` | committed objection detection or repeated evidence | `objections` current state plus immutable `objection_evidence` provenance |
| `qualification.updated` | deterministic score/stage calculation | `qualification_snapshots` |
| `operation.updated` | every accepted operation transition | `operations` and referenced business record |
| `session.ended` | terminal session/outcome commit | `sales_sessions` plus `session_outcomes` |

Every event is appended to `domain_events` in the same transaction as its authoritative state change, then published through `outbox_messages`. Publication is at-least-once; consumers deduplicate with `event_id`/`inbox_receipts`.

### Knowledge and pgvector

| Entity | Authoritative fields | Relationships and minimum indexes | Lifecycle |
|---|---|---|---|
| `knowledge_documents` | `id`, `tenant_id`, source URI/hash, domain, title, classification, document version, ingestion status, effective/expiry times, timestamps/version | unique active source+version; index `(tenant_id,domain,status,updated_at,id)` | Keep active and superseded versions for 90 days unless policy requires longer |
| `knowledge_chunks` | `id`, `tenant_id`, document ID/version, ordinal, approved chunk text, token count, metadata, content hash, timestamps | unique `(document_id,document_version,ordinal)`; FK index | Delete/rebuild with document/index version |
| `knowledge_embeddings` | `id`, `tenant_id`, chunk ID, embedding provider/model/revision, dimensions, vector, created_at | unique `(chunk_id,model,revision)`; HNSW cosine index per active dimension/model plus tenant/domain filters | Rebuildable derived data; old index retained through validated cutover |
| `knowledge_index_versions` | `id`, tenant/global scope, provider/model/revision, dimensions, chunker version, status, built/activated/retired times, evaluation reference | one active version per scope/domain | Retain metadata permanently; vectors follow rollback window |

The initial OpenAI embedding adapter uses the dimension approved by its configured model revision. A dimension/model change creates a new index version and physical vector column/table or partition; vectors from different models/dimensions are never mixed. Retrieval filters tenant, classification, domain, effective time, and active index version before ranking. Product/security explanations may use approved chunks; mutable price, CRM, calendar, or availability facts never use pgvector as transactional truth.

## Redis active-state model

Redis keys include environment and a cluster hash tag containing tenant/session so related session operations co-locate:

| Key pattern | Value and authority | TTL / behavior |
|---|---|---|
| `knotic:{env}:{tenant_id:session_id}:state:v1` | Typed `SalesState` projection, state version, PostgreSQL event watermark | Sliding 24 hours while active; reconstruct from PostgreSQL |
| `knotic:{env}:{tenant_id:session_id}:privacy-block:v1` | Privacy tombstone preventing active-state hydration/new processing | Seven days by default; refresh while erasure/provider confirmation is pending |
| `knotic:{env}:{tenant_id:session_id}:lease:langgraph` | owner token and fencing number | 30 seconds, renewed; stale owner cannot write |
| `knotic:{env}:{tenant_id:session_id}:voice:sequence` | last accepted customer/event sequence and cancelled response IDs | 24 hours after call/session activity |
| `knotic:{env}:{tenant_id:session_id}:tool-results:v1` | Bounded recent validated MCP results by tool call ID | Maximum 15 minutes unless tool contract is shorter |
| `knotic:{env}:{tenant_id:session_id}:operation:{operation_id}` | Safe operation projection | Maximum 30 minutes; durable `operations` is authoritative |
| `knotic:{env}:idempotency:{scope_hash}:{key_hmac}` | Reservation/result pointer, never plaintext key | At least 24 hours; durable record controls longer windows |
| `knotic:{env}:rate:{scope_hash}:{window}` | Atomic counter/token-bucket state | Window plus bounded clock-skew grace |

Redis values carry `schema_version`, `tenant_id`, `session_id`, `state_version`, and `updated_at`. Updates use a Lua script or transaction that checks lease fencing and expected state version. Payloads are size-bounded; recent messages/tool results have fixed maximum counts. Redis eviction, failover, or flush cannot erase durable state or report provider success.

### Authoritative `SalesState` mapping

| Required state | Redis hot projection | Durable authority |
|---|---|---|
| `session_id`, current topic/intent, buying stage, next action, summary, latest request, outcome | Session state key | `sales_sessions` plus `session_outcomes` |
| customer/company/role | Session state key, decrypted minimum | `leads` |
| users/use cases/integrations/budget/timeline | Session state key | `requirements_current`; `requirement_changes` preserves history |
| objections | Session state key | `objections` |
| competitors | Session state key | `session_competitors` |
| qualification components/score/stage | Session state key | `qualification_snapshots`; latest snapshot projected to session |
| recent messages/current question/unfinished response | Bounded state/voice projection | `messages` and interruption `domain_events` |
| recent tool calls/results | Bounded tool-result key | `tool_calls` and `tool_results` |
| conversation summary | Session state key | encrypted `sales_sessions.conversation_summary` |
| in-flight/full committed graph state | Session execution lease | encrypted `workflow_turn_checkpoints`; normalized domain tables remain business authority |

On a cache miss, Flask obtains the session lease, loads the session and latest related durable rows, verifies the event watermark/version, rebuilds the projection, and writes Redis only if no newer version exists.

## Invariants and transaction boundaries

1. A tenant-owned child can reference only a parent in the same tenant through composite foreign keys and forced RLS.
2. One logical LangGraph writer owns a session at a time; lease fencing plus PostgreSQL optimistic version prevents stale writes.
3. Every accepted semantic turn has an immutable message/turn record, state transition events, and one monotonically increasing session version.
4. A confirmed requirement change updates current value, appends old/new history, and emits `requirement.updated` in one transaction.
5. Qualification total equals its seven components and maps deterministically to the FR-09 stage unless a recorded explicit-request override applies.
6. Calendar/CRM/follow-up/handoff success requires validated provider confirmation. Ambiguous results remain pending and reconcile idempotently.
7. Every MCP call and high-impact policy decision has a durable tool/audit record with schema versions and correlation identifiers.
8. A semantic turn has one durable workflow checkpoint; committed replays return authenticated encrypted state without invoking the graph again, while expired or retryable leases can advance only to the bounded three-attempt maximum.
8. Idempotency uniqueness is enforced before work begins; the same key with a different canonical hash is a conflict.
9. Event and audit tables are append-only to application roles. Corrections append compensating events.
10. Raw audio is not persisted by default. If recording is later enabled, consent, purpose, encrypted object storage, retention, deletion, and access audit require a new accepted decision.

## Encryption and data minimization

- TLS protects all connections; production PostgreSQL, Redis, backups, and volumes use platform encryption at rest with environment-separated keys.
- Direct identifiers, message/transcript text, summaries, handoff contexts, provider payloads, and external IDs use application envelope encryption. Ciphertext rows record algorithm and key version; keys remain in the managed key/secret service.
- Exact-match identity/provider lookup uses normalized keyed HMAC blind indexes with a separate rotating key. Unsalted hashes of emails, phones, tokens, or provider IDs are prohibited.
- Audit/error metadata is allowlisted and redacted. Secrets, authorization codes/tokens, cookies, connection URLs, chain-of-thought, and raw model/provider payloads are never persisted in logs/audit.
- Decryption is authorized in the service layer, purpose-limited, and audited. Analytics uses minimized/pseudonymous projections.

## Retention, deletion, and recovery

Defaults are configuration-backed policy baselines, not legal advice. Production activation requires tenant, legal, security, and regional approval.

| Data class | Default | Deletion behavior |
|---|---:|---|
| Active Redis session projection | 24 hours sliding | Expire; rebuild from durable state |
| Idempotency | at least 24 hours | Purge after all replay/reconciliation windows |
| Operations | 30 days after terminal | Delete safe result; retain referenced business/audit records |
| Sessions, messages, requirements, objections, qualification | 365 days after end | Delete/anonymize tenant graph in FK order; retain non-identifying required audit |
| Tool calls, meetings, follow-ups, handoffs, outcomes | 400 days | Delete encrypted payload/PII, retain minimal proof where required |
| Domain and audit events | 400 days | Partition/purge only through audited retention job; anonymize actor where required |
| Knowledge superseded content | 90 days after replacement | Delete chunks/vectors after rollback window |
| Raw audio | not stored | No object exists to retain |

Deletion requests create an audited erasure job. New processing is blocked, provider deletion/unlink is requested, Redis keys are removed, encrypted content is destroyed or rows deleted, search/vector derivatives are purged, and completion/failures are recorded. Backups expire through their independent bounded schedule; restore procedures reapply tombstones/erasure ledger before serving traffic. Legal holds are explicit, access-controlled, time-bounded records and prevent only the documented data classes from purge.

## Migration strategy

- Migrations are immutable, ordered, checksum-verified, and run once by `knotic_migrator`; services never auto-create schema at startup.
- Use expand/migrate/contract: add nullable/new structures and indexes, deploy dual-compatible code, backfill in bounded resumable batches, validate constraints, switch reads, then remove old structures in a later release.
- Create large indexes concurrently outside transaction blocks. Add expensive checks/foreign keys as `not valid`, validate separately, then enforce. Never use unsupported `add constraint if not exists`.
- Backfills use keyset ranges, statement/lock timeouts, rate limits, progress checkpoints, and observable retry. They do not hold transactions across network calls.
- Destructive or irreversible migrations require tested backup/restore, row-count and invariant reconciliation, explicit approval, rollback/forward-fix plan, and maintenance communication.
- Every migration tests upgrade from the previous release and a fresh database, then verifies foreign-key indexes, RLS, privileges, constraints, extension versions, and query plans for critical access paths.

## Requirement traceability

| Requirement | Authoritative model and lifecycle evidence |
|---|---|
| FR-04 structured memory | `sales_sessions`, `leads`, `requirements_current`, `objections`, `session_competitors`, `qualification_snapshots`, `messages`, tool tables; Redis is a rebuildable projection |
| FR-05 requirement revision | Atomic `requirements_current` replacement + immutable `requirement_changes` + `requirement.updated` event with old/new values |
| FR-07 objections | Nine-category `objections` current view + immutable `objection_evidence` + approved policy/escalation in `objection.updated` |
| FR-11 CRM | `leads`, `provider_links`, `tool_calls/results`, `pending_provider_updates`; provider confirmation remains external truth |
| FR-12 calendar | `meetings` with selected slot, idempotency, explicit pending confirmation, and validated provider confirmation |
| FR-13 human escalation | `handoffs` with encrypted structured context, reason, policy/approval, assignment, provider reference, and audit event |
| FR-14 outcomes | constrained `session_outcomes` plus projected `sales_sessions.outcome` and immutable event/audit history |

## Verification command

Run `node scripts/validate-data-model.mjs`. The validator confirms required entities, state fields, Redis TTLs, event types, invariants, lifecycle sections, and explicit FR-04/FR-05/FR-11–FR-14 traceability. Physical migrations must add database-level schema and migration tests when implemented.
