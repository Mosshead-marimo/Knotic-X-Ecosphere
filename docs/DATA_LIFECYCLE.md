# Data Lifecycle Operations

## Scope and authority

This runbook implements the privacy, minimization, retention, export, and erasure baseline in `DATA_MODEL.md` and ADR-008/ADR-009. It is an engineering control, not legal advice. Each production tenant must have an approved policy version, region review, legal-hold process, backup expiry schedule, and named privacy/security owners before the retention worker is enabled.

All workflows are tenant scoped, correlation identified, and audited through allowlisted metadata. Raw transcript, decrypted identifiers, cookies, credentials, provider payloads, and connection URLs are prohibited in audit metadata and operational logs.

## Database identities

Migration `20260822_0005` creates non-login group roles. The platform provisions separate login identities and grants exactly one group role; application containers never receive the migration-owner credential.

| Group role | Intended identity | Allowed | Explicitly prohibited |
|---|---|---|---|
| `knotic_runtime` | Flask/LangGraph runtime | Tenant-RLS reads and mutable application writes; append audit/events | DDL; migration state writes; update/delete of append-only history/audit |
| `knotic_retention` | Scheduled lifecycle worker | Tenant-RLS reads, approved deletion/minimization, operation updates, audit append | DDL; audit mutation/deletion; cross-tenant bypass |
| `knotic_auditor` | Read-only audit/export reviewer | Tenant-RLS reads | All writes and DDL |

All roles are `NOLOGIN`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, and `NOBYPASSRLS`. Production login creation, rotation, and role membership are platform operations outside Alembic. Tests must prove an auditor cannot insert and a runtime identity cannot observe another tenant after setting its trusted tenant context.

## Encryption and minimization

- PostgreSQL, Redis, volumes, and backups require platform encryption at rest and TLS in transit.
- Recoverable lead, session, and objection text uses `StateFieldCipher`: AES-256-GCM with a purpose-separated key, random 96-bit nonce, version byte, and tenant/aggregate/field associated data.
- Keys remain in the approved managed secret/key service. Ciphertext authentication failure stops recovery; plaintext fallback is forbidden.
- Structured audit metadata accepts only the internal allowlist and bounded scalar values. Sensitive application logs pass through `SensitiveDataFilter`, which redacts credentials, authorization/cookie/token fields, emails, authenticated connection URLs, prompts, transcripts, and payload extras before handlers emit them.
- Raw audio is not stored.

## Export workflow

1. Authorize the tenant actor and purpose outside the data service.
2. Call `DataLifecycleService.export_session` with server-derived tenant, actor, session, and correlation UUIDs.
3. The service loads tenant-scoped typed state, emits `SESSION_EXPORTED`, and returns schema-versioned data. It does not include arbitrary database rows, credentials, raw provider payloads, or raw audio.
4. Encrypt the delivered artifact, apply a short approved delivery TTL, and audit delivery in the external request boundary. The in-process export object must not be logged.

Export should be completed before an erasure request. A pending erasure installs a Redis privacy tombstone and blocks state hydration.

## Erasure workflow

1. `request_session_erasure` atomically installs a seven-day Redis privacy block, evicts active state, creates or reuses one pending `SESSION_ERASURE` operation, and appends `SESSION_ERASURE_REQUESTED`.
2. Every new-processing boundary calls `processing_allowed`; `SalesStateHydrator` also fails closed on the Redis tombstone.
3. If a provider link exists, request provider deletion/unlink through the approved idempotent MCP integration. Until provider confirmation is validated, `execute_session_erasure` records `SESSION_ERASURE_BLOCKED` with `PENDING_CONFIRMATION` and does not claim success.
4. Confirm there is no active `LEGAL_HOLD`. Holds are access controlled, time bounded operations owned by the privacy/legal role; an active hold prevents minimization and erasure.
5. After provider confirmation where required, execute erasure. The service revalidates the operation, evicts cache, deletes the session graph in foreign-key order, preserves the minimized audit proof, nulls the retained operation's session reference, and records `SESSION_ERASED` plus `COMPLETED`.
6. Verify the session graph and Redis state are absent and the audit/operation proof remains. A failed transaction leaves durable data intact; a failed Redis privacy decision stops before durable deletion.

Backups expire on their independent approved schedule. After any restore, the recovery owner must replay `SESSION_ERASED` audit tombstones and completed erasure operations before reopening traffic; failure to reconcile is a release blocker.

## Retention workflow

The default `RetentionPolicy` is versioned and bounded:

| Class | Default action |
|---|---|
| Redis active projection | 24-hour sliding expiry; explicit eviction for lifecycle work |
| Idempotency | Purge after its recorded expiry, never earlier than 24 hours |
| Terminal operations | After 30 days, remove safe result/detail while retaining the operation proof |
| Session content | At 365 days after end, remove messages, requirements/history, objections, competitors, qualification, summary, latest request, and topic/intent |
| Business records and events | At 400 days after end, delete the remaining session graph while retaining minimized audit evidence |

`run_retention` processes bounded tenant batches, purges older sessions first, evicts Redis outside database transactions, skips active legal holds, records `RETENTION_MINIMIZED`/`RETENTION_PURGED`, and is replay safe because successful audit actions exclude completed targets. Run one tenant and policy version at a time. Alert on errors, backlog age, legal-hold skips, provider-confirmation backlog, or repeated zero-progress batches.

## Required rehearsal

Before production enablement, run the pinned integration suite and retain evidence for:

- cross-tenant RLS hiding under the runtime role;
- auditor write denial and append-only history denial;
- encrypted export plus minimized audit output;
- processing block and Redis tombstone behavior;
- provider-confirmation pending behavior;
- legal-hold skip;
- 365-day minimization and 400-day purge;
- erasure rollback/failure injection;
- backup restore followed by erasure-ledger replay.
