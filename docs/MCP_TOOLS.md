# MCP Tool Contracts and Policies

## Authority and versioning

This document defines logical MCP tool contract version 1. The machine-readable registry is [`contracts/mcp-tools.v1.json`](contracts/mcp-tools.v1.json). It is subordinate to requirements, system design, accepted architecture decisions, API contracts, and the data model.

Tool names are stable `<domain>.<action>` identifiers. Input/output changes follow the API compatibility policy: additive optional fields are compatible; removed, renamed, newly required, type/meaning, approval, or side-effect changes require a new tool major version. The physical MVP may host all logical domains in one private MCP gateway.

## Invocation envelope and trust boundary

Flask/LangGraph calls the private MCP gateway through a typed client. The gateway authenticates workload identity (or the rotating current/previous bootstrap token over private TLS), checks audience and scopes, and derives this context from trusted identity—not model arguments:

- `tool_call_id`, `tenant_id`, `actor_id`, `session_id`, `turn_id`;
- `correlation_id`, `causation_id`, requested tool/version;
- granted scopes, policy version, approval reference;
- `idempotency_key` for side effects and absolute deadline.

The model supplies only the validated tool-specific `arguments`. Unknown fields, caller-provided tenant/actor identity, arbitrary provider options, raw SQL, URLs outside provider allowlists, prompt text, and credentials are rejected before policy or provider access.

Every result uses one envelope:

```json
{
  "tool_call_id": "6fa459ea-ee8a-4ca4-894e-db77e160355e",
  "tool": "calendar.book_meeting",
  "version": 1,
  "status": "PENDING",
  "data": null,
  "error": null,
  "provider_reference": null,
  "started_at": "2026-08-20T08:00:00Z",
  "completed_at": null
}
```

`SUCCEEDED` requires schema-valid `data` and, for provider actions, validated provider confirmation. `PENDING` means accepted/reconciling and never means success. `FAILED` requires a safe typed error. Provider-native payloads never cross the adapter boundary.

## Authorization and approval levels

Scopes are deny-by-default and checked by the MCP gateway even when Flask has already authorized the actor.

| Level | Meaning | Examples |
|---|---|---|
| `NONE` | Read-only or pure calculation within granted scope | knowledge search, qualification |
| `POLICY` | Deterministic server policy must approve; no model-only approval | CRM notes, follow-up creation |
| `CUSTOMER_CONFIRMATION` | A recorded explicit customer selection/request plus policy approval | calendar booking |
| `HUMAN_APPROVAL` | Authorized human approval is required | reserved for future discount/legal/high-risk actions |

Approval references bind tenant, actor/customer, tool, canonical arguments hash, policy version, approver, decision, and expiry. Changed arguments invalidate approval. The gateway does not ask the model to interpret approval.

## Idempotency, timeouts, and retries

- Every side-effect tool requires a 16–128 character idempotency key reserved before provider access. Same scope/key/hash replays; same key with different arguments returns `IDEMPOTENCY_CONFLICT`.
- Provider idempotency keys are derived from the accepted command. If a provider lacks idempotency, reconciliation searches the authoritative provider before retrying.
- Registry timeout values are total per-attempt bounds; the invocation deadline is authoritative and may be shorter.
- Read-only tools allow at most two transient attempts. Side-effect tools allow one provider attempt unless the provider proves the request was not accepted or supports safe idempotent retry; calendar booking never blindly retries an ambiguous timeout.
- Backoff is exponential with jitter and honors provider retry hints. Validation, authentication, authorization, policy, not-found, conflict, and invalid-result failures are not retried.
- Circuit breakers, bulkheads, and rate limits are per provider/domain. Retry exhaustion returns safe failure or durable pending reconciliation.

## Failure envelope

Errors contain `code`, safe `message`, `retryable`, optional `retry_after_seconds`, and redacted `details`. Allowed common codes are:

`INVALID_ARGUMENT`, `UNAUTHENTICATED`, `PERMISSION_DENIED`, `POLICY_DENIED`, `APPROVAL_REQUIRED`, `NOT_FOUND`, `CONFLICT`, `IDEMPOTENCY_CONFLICT`, `RATE_LIMITED`, `TIMEOUT`, `DEPENDENCY_UNAVAILABLE`, `INVALID_RESULT`, `NO_GROUNDED_RESULT`, `PENDING_CONFIRMATION`, and `INTERNAL_ERROR`.

Specific safe degradation:

- Pricing unavailable: return `PRICE_UNAVAILABLE`; never use prompt/model memory as price.
- Knowledge miss: `NO_GROUNDED_RESULT` with suggested clarification/escalation.
- Calendar ambiguity: `PENDING_CONFIRMATION`, persist/reconcile, never claim booked.
- CRM write failure: durable pending update with retry status; never fabricate success.
- Invalid provider output: `INVALID_RESULT`, quarantine/redact payload, alert.

## Audit contract

Every attempt writes `tool_calls`; terminal validated results write `tool_results`; side effects also write domain records, `domain_events`, and outbox work in short transactions. Audit records include tool/version, trusted context IDs, scope/policy/approval decision, canonical request hash (not secrets), schema versions, idempotency-key HMAC, attempt/deadline/latency, provider reference, result status/error code, and correlation IDs. Arguments/results are encrypted or redacted according to `DATA_MODEL.md`.

## Tool registry

The JSON registry defines exact Draft 2020-12 input and data-output schemas plus policy metadata. Summary:

| Tool | Scope | Side effect / approval | Success data | Safe failure notes |
|---|---|---|---|---|
| `pricing.get_quote` | `sales:read` | no / `NONE` | quote, currency, line items, validity/source | `PRICE_UNAVAILABLE`; no invented price |
| `pricing.compare_plans` | `sales:read` | no / `NONE` | grounded plan comparison/source | missing facts are explicit |
| `lead.qualify` | `sales:read` | no / `NONE` | seven components, total, stage | deterministic score validation |
| `lead.next_action` | `sales:read` | no / `NONE` | action and reason codes | policy-limited enum only |
| `followup.create` | `followup:write` | yes / `POLICY` | follow-up ID/status | pending/retry is explicit |
| `knowledge.search` | `knowledge:read` | no / `NONE` | ranked cited chunks/index version | `NO_GROUNDED_RESULT` |
| `product.search` | `knowledge:read` | no / `NONE` | cited product matches | active approved sources only |
| `product.get_feature` | `knowledge:read` | no / `NONE` | cited feature fact | no prompt constants |
| `product.get_integration` | `knowledge:read` | no / `NONE` | cited integration support/status | mutable state from authority |
| `competitor.compare` | `knowledge:read` | no / `NONE` | dimensioned cited comparison | unknowns remain unknown |
| `security.get_information` | `knowledge:read` | no / `NONE` | approved security answer/citations | restricted docs policy |
| `crm.get_lead` | `crm:read` | no / `NONE` | canonical lead or not-found | tenant/provider scoped |
| `crm.create_lead` | `crm:write` | yes / `POLICY` | lead/provider reference/status | reconcile ambiguous result |
| `crm.update_lead` | `crm:write` | yes / `POLICY` | version/status/provider reference | expected-version conflict |
| `crm.add_note` | `crm:write` | yes / `POLICY` | note reference/status | encrypted/minimized content |
| `crm.add_call_summary` | `crm:write` | yes / `POLICY` | activity reference/status | summary schema required |
| `calendar.get_slots` | `calendar:read` | no / `NONE` | timezone-aware slots/provider snapshot | availability is ephemeral |
| `calendar.book_meeting` | `calendar:write` | yes / `CUSTOMER_CONFIRMATION` | meeting status/reference | only confirmed provider result succeeds |
| `handoff.request_agent` | `handoff:write` | yes / `POLICY` | request/status | explicit request or deterministic FR-13 trigger is recorded |
| `handoff.transfer_context` | `handoff:write` | yes / `POLICY` | transfer/status | all FR-13 fields, encrypted |

## Requirement traceability

| Requirement/design | Tools and policy evidence |
|---|---|
| FR-08 grounded business facts | pricing, knowledge, product, integration, competitor, security, CRM, and calendar reads require validated authoritative results/citations |
| FR-09 qualification | `lead.qualify` output constrains seven components, total, and stage |
| FR-10 next action | `lead.next_action` plus governed pricing/knowledge/follow-up/calendar/handoff tools |
| FR-11 CRM | five CRM tools with idempotency, provider confirmation, pending reconciliation, audit |
| FR-12 calendar | slot read plus booking requiring bound customer confirmation and provider confirmation |
| FR-13 handoff | request/transfer tools with deterministic triggers, approval, and complete structured packet |
| FR-14 outcomes | tool results are inputs; LangGraph/PostgreSQL owns final constrained outcome |
| System Design MCP inventory | all 20 named Sales, Knowledge, and Integration MCP tools appear exactly once |

## Verification

Run `node scripts/validate-mcp-contract.mjs`. It validates registry JSON, unique/versioned names, Draft 2020-12 schema structure, side-effect/idempotency/approval/retry invariants, common failure/audit metadata, all 20 System Design tools, and FR-08–FR-14 traceability.
