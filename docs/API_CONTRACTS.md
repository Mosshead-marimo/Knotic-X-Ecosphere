# API Contracts

## Purpose and authority

This document defines version 1 of the HTTP and semantic-event contracts between the Next.js frontend, Flask backend, and realtime voice worker. The machine-readable source is [`contracts/openapi.v1.json`](contracts/openapi.v1.json). If prose and the OpenAPI artifact disagree, the OpenAPI artifact controls for wire shape while `REQUIREMENTS.md`, `System_Design.md`, and `ARCHITECTURE_DECISIONS.md` retain their higher source-of-truth priority.

`P0-T005` defines transport contracts only. PostgreSQL/Redis lifecycle and field authority are defined by `P0-T006`; MCP tool schemas and policies are defined by `P0-T007`.

## Protocol baseline

- Production transport is HTTPS with JSON encoded as UTF-8.
- Public resources use `/api/v1`; private voice-worker resources use `/internal/v1`.
- Clients send `Accept: application/json`. Requests with bodies send `Content-Type: application/json`; unsupported media types return `415`.
- Field names are `snake_case`. Timestamps are RFC 3339 UTC strings. Durations are integer milliseconds. Identifiers are opaque UUID strings and must never be parsed for business meaning.
- Unknown request fields are rejected with `422 VALIDATION_FAILED`. Clients must ignore unknown response fields to permit additive evolution.
- Every response contains `X-Request-ID`; correlated requests also return `X-Correlation-ID`. Clients may provide a valid `X-Request-ID`, but Flask replaces malformed or duplicate values.
- Credentials, cookies, provider payloads, stack traces, database details, and server configuration never appear in a response.

## Authentication, session, and request integrity

### Browser API

Human authentication uses OIDC Authorization Code with PKCE through provider-neutral Flask routes. `GET /api/v1/auth/login` creates server-side state/nonce/PKCE material and redirects to the configured issuer. `GET /api/v1/auth/callback` validates the callback and establishes the application session. `GET /api/v1/auth/session` returns the safe current actor projection. `POST /api/v1/auth/logout` revokes the local session and expires its cookie. The provider is selected by deployment configuration without changing these frontend contracts.

`return_to` is a relative application path; absolute, protocol-relative, or non-allowlisted destinations return `400` to prevent open redirects. Callback state is single-use and time-bounded. Authentication errors use the standard safe envelope and never include authorization codes, provider tokens, claims, or raw provider messages.

Flask establishes the server-managed `knotic_session` cookie with `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/api`, rotation on authentication/privilege change, bounded idle/absolute expiry, and server-side revocation. A callback or login response is never cached.

All `/api/v1` operations except `GET /api/v1/health/live` require the cookie. A missing, expired, or invalid session returns `401`; an authenticated actor without permission returns `403`. Cross-tenant resources intentionally return `404` to avoid existence disclosure.

Every state-changing browser request also requires:

- an allowlisted HTTPS `Origin` validated by Flask;
- `X-CSRF-Token`, bound to the authenticated server session; and
- `Idempotency-Key` for every public `POST` in this contract.

Cookies or tokens must not be stored in browser local storage or exposed to JavaScript. Responses containing Agora credentials use `Cache-Control: no-store`.

### Internal voice API

`/internal/v1` is private and requires deployment workload identity using a short-lived bearer token or mutual TLS. The verified workload identity must have the `voice-worker` audience and the specific operation permission. Private networking is not authentication. Browser session cookies and CSRF headers are not accepted as internal identity.

## Endpoint registry

| Method and path | Caller | Success | Purpose |
|---|---|---|---|
| `GET /api/v1/health/live` | Platform | `200` | Process liveness only; never checks dependencies or returns configuration. |
| `GET /api/v1/auth/login` | Browser | `302` | Start OIDC Authorization Code + PKCE using server-held state. |
| `GET /api/v1/auth/callback` | OIDC user agent | `303` | Validate callback, establish the application cookie, and redirect safely. |
| `GET /api/v1/auth/session` | Browser | `200` | Read the authenticated actor and application-session expiry. |
| `POST /api/v1/auth/logout` | Browser | `200` | Revoke the local session and expire the application cookie. |
| `POST /api/v1/sessions` | Browser | `201` | Create an authorized sales session. |
| `GET /api/v1/sessions/{session_id}` | Browser | `200` | Read the latest authorized session projection. |
| `POST /api/v1/sessions/{session_id}/agora-credentials` | Browser | `200` | Mint short-lived, session/channel/UID-bound Agora credentials. |
| `POST /api/v1/sessions/{session_id}/turns` | Browser | `200` or `202` | Submit a text turn; return a completed result or a pollable operation. |
| `POST /api/v1/sessions/{session_id}/end` | Browser | `200` or `202` | Idempotently request graceful session completion. |
| `GET /api/v1/sessions/{session_id}/events` | Browser | `200` | Read an ordered, cursor-paginated event projection. |
| `GET /api/v1/operations/{operation_id}` | Browser | `200` | Read an authorized asynchronous operation. |
| `POST /internal/v1/voice/turns` | Voice worker | `200` or `202` | Submit a final semantic customer turn using session sequence ordering. |
| `POST /internal/v1/voice/interruptions` | Voice worker | `202` | Record playback cancellation/truncation before the next turn is processed. |

`202` never means that a requested business result succeeded. For turn/end commands it contains a named `Operation`; the client polls the URL from `Location` and uses its terminal `SUCCEEDED` or `FAILED` state. For `POST /internal/v1/voice/interruptions`, it means only that the immutable interruption event identified by `event_id` was durably accepted—there is no later business result. Calendar, CRM, follow-up, and handoff success remain governed by later MCP contracts and authoritative provider confirmation.

## Resource semantics

### Session

A session has a server-generated `session_id`, a monotonically increasing `version`, lifecycle status, timestamps, and a read-only sales-state projection. The version changes after every accepted state transition. Lifecycle values are:

- `CREATED`: allocated but no active turn has completed;
- `ACTIVE`: accepting turns;
- `ENDING`: end was accepted and cleanup/reconciliation is pending;
- `ENDED`: terminal normal completion;
- `FAILED`: terminal safe failure after recovery is exhausted.

Turns are rejected with `409 SESSION_NOT_ACCEPTING_TURNS` when the session is `ENDING`, `ENDED`, or `FAILED`. Repeating an idempotent request returns the stored original result instead of creating another turn or session.

The public `SalesStateView` is a projection, not the storage model. It exposes current intent, stage, qualification, next action, summary, latest request, customer facts, current confirmed requirements, objections, competitors, and outcome. It never exposes prompts, chain-of-thought, provider credentials, raw tool payloads, or internal policy material.

### Turn

Text turns contain final customer text, client locale, and optional client timestamp. Voice turns contain the final transcript plus the session sequence, source timing, and speech-provider event reference. Partial transcripts and raw audio frames are not sent to Flask's semantic-turn API.

On completion, `TurnResult` identifies the accepted turn and approved assistant response, returns the new session version/projection, and includes grounded citations when present. `response_disposition` is `SPOKEN`, `SILENT_HANDOFF`, or `SILENT_END`. `SPOKEN` requires non-null response ID/text; either silent disposition requires both to be null. Pending work is represented by `Operation`, never a completed turn with missing fields.

The voice worker must submit exactly one final voice turn for each `sequence`. A duplicate sequence with the same canonical payload replays the result; a different payload returns `409 SEQUENCE_CONFLICT`. Late turns for a terminal session are rejected.

### Agora credentials

The response contains only a short-lived RTC token, channel name, participant UID, role, and expiry. It never contains the Agora App Certificate. Credentials are bound to the authorized session and cannot select an arbitrary channel or UID. The client must refresh before expiry through the same endpoint.

### Operation

An asynchronous operation is `PENDING`, `RUNNING`, `SUCCEEDED`, or `FAILED`. Terminal operations include either a typed `result` or safe `ErrorObject`, never both. `Retry-After` tells clients when to poll a non-terminal operation. Operation ownership is derived from trusted identity context; guessing an ID cannot cross tenant/session boundaries.

## Idempotency contract

`Idempotency-Key` is required on all `POST` operations and is an opaque 16–128 character ASCII value generated per logical command. UUIDs are recommended. The server scope is trusted tenant + actor/workload + HTTP method + canonical path + key.

1. The server hashes the canonical validated request and atomically reserves the scope before starting work.
2. The same key and canonical request replay the original HTTP status, body, and relevant headers. The replay includes `Idempotency-Replayed: true`.
3. The same key with a different canonical request returns `409 IDEMPOTENCY_CONFLICT` and performs no new work.
4. Authentication, authorization, CSRF, malformed JSON, and pre-validation failures are not stored as idempotent results.
5. Once external work may have started, timeouts become a persisted pending operation; they are never returned as a safe-to-repeat unknown `500`.
6. Records are retained for at least 24 hours and never less than the maximum retry/reconciliation window for the operation. `P0-T006` may extend, but not shorten, this guarantee.

The header does not make non-identical operations interchangeable and does not replace provider idempotency keys. MCP adapters derive a separate provider-safe key from the accepted command.

## Optimistic concurrency and ordering

- Session representations return an `ETag` derived from the session version.
- Commands carry `expected_session_version` when the caller is making a version-sensitive decision. A mismatch returns `409 VERSION_CONFLICT` with the current safe version in `error.details`.
- Voice events require a positive, monotonically increasing `sequence` per session.
- Domain events are ordered by `(occurred_at, event_id)` within the returned stable cursor snapshot. Consumers must still deduplicate by `event_id`.
- HTTP arrival order is never assumed to be business-event order.

## Pagination

Only cursor pagination is supported. `GET /api/v1/sessions/{session_id}/events` accepts:

- `limit`: default `50`, minimum `1`, maximum `100`;
- `cursor`: opaque token returned by the previous page.

Responses contain `items`, `next_cursor`, and `has_more`. `next_cursor` is non-null exactly when `has_more` is true. Cursors are scoped to the authenticated actor, tenant, resource, filter, and stable ordering; a malformed, expired, or scope-mismatched cursor returns `400 INVALID_CURSOR`. Clients must not construct, persist indefinitely, or decode cursors.

## Rate limits and overload

The baseline limits are contract defaults; production may lower limits only with a versioned compatibility notice and may raise them without a contract change.

| Class | Default quota | Scope |
|---|---:|---|
| Liveness | 60/minute | source IP |
| Authentication bootstrap/callback/logout | 30/minute | source IP plus server-side state/session where available |
| Public reads | 120/minute | tenant + actor |
| Session creation/end | 30/minute | tenant + actor |
| Text/voice semantic turns | 60/minute | tenant + session |
| Agora credential minting | 10/minute | tenant + session |
| Internal interruption events | 120/minute | workload + session |

Every rate-limited response includes `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset` (UTC epoch seconds). `429 RATE_LIMITED` also includes `Retry-After` in whole seconds. Dependency overload uses `503 DEPENDENCY_UNAVAILABLE`, not `429`, and may include `Retry-After`. Clients use bounded exponential backoff with jitter and must not retry non-retryable errors.

## Error envelope and HTTP mapping

All non-2xx JSON responses use:

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "category": "VALIDATION",
    "message": "The request did not pass validation.",
    "retryable": false,
    "request_id": "8cd1571f-b370-4a53-b347-4a8caadcd57b",
    "correlation_id": "59e57939-c5c3-4faf-b80c-58b7f806a8c8",
    "details": [
      {"field": "text", "issue": "must contain at least one non-whitespace character"}
    ]
  }
}
```

| HTTP | Category | Codes |
|---:|---|---|
| `400` | `VALIDATION` | `MALFORMED_JSON`, `INVALID_CURSOR`, `INVALID_HEADER` |
| `401` | `AUTHENTICATION` | `AUTHENTICATION_REQUIRED`, `AUTHENTICATION_INVALID` |
| `403` | `AUTHORIZATION` / `POLICY` | `FORBIDDEN`, `CSRF_FAILED`, `ORIGIN_DENIED`, `POLICY_DENIED` |
| `404` | `NOT_FOUND` | `RESOURCE_NOT_FOUND` |
| `409` | `CONFLICT` | `VERSION_CONFLICT`, `SEQUENCE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `SESSION_NOT_ACCEPTING_TURNS` |
| `415` | `VALIDATION` | `UNSUPPORTED_MEDIA_TYPE` |
| `422` | `VALIDATION` | `VALIDATION_FAILED` |
| `429` | `RATE_LIMIT` | `RATE_LIMITED` |
| `500` | `INTERNAL` | `INTERNAL_ERROR` |
| `503` | `DEPENDENCY` | `DEPENDENCY_UNAVAILABLE` |
| `504` | `DEPENDENCY` | `DEPENDENCY_TIMEOUT` |

`retryable` is authoritative only for the same safe request and remains subject to `Retry-After`, idempotency, and the client's total deadline. A client must never infer retryability solely from status code. Validation details use public field names and rules, not submitted secrets or raw values.

## Versioned semantic events

All events use `event_version: 1` and this envelope:

```json
{
  "event_id": "6fa459ea-ee8a-4ca4-894e-db77e160355e",
  "event_type": "requirement.updated",
  "event_version": 1,
  "occurred_at": "2026-08-18T08:00:02Z",
  "tenant_id": "60b059c2-9d6f-4d3f-b98d-f72bfb7a5691",
  "session_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "sequence": 7,
  "correlation_id": "59e57939-c5c3-4faf-b80c-58b7f806a8c8",
  "causation_id": "64a95db5-d568-4f2f-b624-76a3f675a6bc",
  "actor": {"type": "CUSTOMER", "id": "7db03395-8778-4a76-9c90-d3cb3d4fccb8"},
  "payload": {
    "field": "users",
    "old_value": 50,
    "new_value": 250,
    "confirmed": true
  }
}
```

Version 1 event types are:

- `session.created`
- `turn.accepted`
- `turn.completed`
- `response.interrupted`
- `requirement.updated`
- `qualification.updated`
- `operation.updated`
- `session.ended`

`requirement.updated` always includes the field plus old/new values and explicit confirmation. `response.interrupted` includes `response_id`, played/truncated offsets, detection timestamp, and reason. `operation.updated` never claims provider success unless its status is `SUCCEEDED` after validated confirmation.

Events are immutable. Consumers ignore unknown event types only when they have opted into forward-compatible projection behavior; exhaustive business consumers must stop and alert. A breaking payload change requires `event_version: 2` or a new event type, with dual-publish/migration documented by the compatibility policy.

## Contract examples

### Create a session

```http
POST /api/v1/sessions HTTP/1.1
Cookie: knotic_session=<redacted>
Origin: https://sales.example.com
X-CSRF-Token: <session-bound-token>
Idempotency-Key: 7d6fb69a-22e8-4c62-a1b1-82b4dbeb6381
Content-Type: application/json

{"locale":"en-IN","timezone":"Asia/Calcutta"}
```

```http
HTTP/1.1 201 Created
Location: /api/v1/sessions/f47ac10b-58cc-4372-a567-0e02b2c3d479
ETag: "1"
X-Request-ID: 8cd1571f-b370-4a53-b347-4a8caadcd57b

{
  "session_id":"f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "version":1,
  "status":"CREATED",
  "created_at":"2026-08-18T08:00:00Z",
  "updated_at":"2026-08-18T08:00:00Z",
  "ended_at":null,
  "state":{"current_intent":null,"buying_stage":"NURTURE","qualification_score":0,"next_best_action":"ASK_DISCOVERY","conversation_summary":"","latest_request":null,"outcome":null}
}
```

### Submit a text turn with immediate completion

```http
POST /api/v1/sessions/f47ac10b-58cc-4372-a567-0e02b2c3d479/turns HTTP/1.1
Cookie: knotic_session=<redacted>
Origin: https://sales.example.com
X-CSRF-Token: <session-bound-token>
Idempotency-Key: 38ee0f3a-668f-470d-8da2-bf6825f39f20
Content-Type: application/json

{"text":"We need support for 250 users.","locale":"en-IN"}
```

```http
HTTP/1.1 200 OK
ETag: "7"

{
  "turn_id":"64a95db5-d568-4f2f-b624-76a3f675a6bc",
  "status":"COMPLETED",
  "response_disposition":"SPOKEN",
  "response_id":"e5d5f6f2-c79d-41a3-a608-050e18d638a5",
  "response_text":"Thanks — I have updated the requirement to 250 users. Which integrations are essential?",
  "session_version":7,
  "state":{"current_intent":"CHANGE_REQUIREMENT","buying_stage":"FOLLOWUP","qualification_score":45,"next_best_action":"ASK_DISCOVERY","conversation_summary":"Customer needs support for 250 users.","latest_request":"Support 250 users","outcome":null},
  "citations":[]
}
```

### Accepted asynchronous work

```http
HTTP/1.1 202 Accepted
Location: /api/v1/operations/2ebc3bc3-8b7b-4f77-8d23-856f548b2e5c
Retry-After: 2

{
  "operation_id":"2ebc3bc3-8b7b-4f77-8d23-856f548b2e5c",
  "kind":"PROCESS_TURN",
  "status":"PENDING",
  "session_id":"f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "created_at":"2026-08-18T08:00:01Z",
  "updated_at":"2026-08-18T08:00:01Z",
  "result":null,
  "error":null
}
```

## Compatibility and deprecation policy

- URI major versions define wire compatibility. Version 1 remains supported for at least 90 days after a replacement reaches production unless an active security incident requires accelerated retirement.
- Additive optional response fields, new endpoints, new non-required request capabilities, and new error detail fields are backward-compatible. Clients must ignore unknown response fields.
- Removing/renaming fields, changing meaning/type/requiredness, narrowing accepted input, changing idempotency scope, or changing a success state is breaking and requires `/v2`.
- New enum or event values are additive only for consumers documented to handle unknown values. Otherwise they require a new major schema/event version.
- Requests reject unknown fields; adding a new optional request field is compatible. Making it required is breaking.
- Deprecation is announced in documentation and through `Deprecation: true`, `Sunset`, and `Link: <migration-guide>; rel="deprecation"` headers where applicable.
- Producers deploy backward-compatible support before consumers use it. Database changes follow expand/migrate/contract and preserve the previous application release during rollout.
- OpenAPI changes require the executable contract validator, examples, frontend/backend consumer tests, and explicit review by both boundary owners.

## Validation

Run from the repository root:

```text
npm run validate:api-contract
```

The dependency-free validator parses the OpenAPI 3.1 document, resolves local schema references, checks versioned paths and unique operation IDs, enforces authentication/CSRF/idempotency/pagination rules, validates standard error coverage, and validates every `x-contract-example` against its declared schema. `P0-T009` must add this command to CI and add framework-level producer/consumer contract tests.
