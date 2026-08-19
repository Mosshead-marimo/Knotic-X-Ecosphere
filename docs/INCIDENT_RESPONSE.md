# Incident Response Baseline

## Contacts and release gate

The operating organization must map these roles to named people, a 24x7 paging target, and an approved incident channel in its private on-call system before staging or production approval. Personal contact details and paging tokens must not be committed here.

| Role | Responsibility |
|---|---|
| Incident commander | Severity, coordination, timeline, and closure |
| Communications lead | Internal/customer/status updates |
| Application on-call | Frontend, Flask, LangGraph, and release diagnosis |
| MCP/integrations on-call | Knowledge, CRM, calendar, and pending-work reconciliation |
| Data/platform on-call | PostgreSQL, pgvector, Redis, networking, backups, and capacity |
| Security/privacy on-call | Credential, authorization, prompt-injection, and data-exposure response |

The private on-call system is authoritative. `docs/COMPONENT_OWNERSHIP.md` determines the accountable engineering boundary.

## Severity and first response

- SEV-1: security/data-integrity event, broad outage, or fabricated/unsafe business action. Page immediately; stop affected writes/actions.
- SEV-2: major degradation or critical journey unavailable without an unsafe result. Page the owning teams and establish an incident channel.
- SEV-3: limited degradation with a safe workaround. Track during business support hours.

For every severity: assign commander and scribe, state customer impact and start time, preserve correlation IDs/evidence, identify the last change, and set the next update time. Never place credentials, raw sensitive transcripts, or unnecessary personal data in incident chat or tickets.

## Triage order

1. Protect customers and external systems; disable high-impact writes when truth cannot be confirmed.
2. Check ingress and frontend proxy, Flask readiness, MCP readiness, then PostgreSQL and Redis health.
3. Correlate structured logs/traces by request, session, operation, and tool-call IDs.
4. Check pending/idempotent operations before retrying CRM or calendar calls.
5. Roll back using `ROLLBACK.md` when the latest release is implicated.
6. Communicate only confirmed facts and label unknowns.

Close only after service recovery, external-action reconciliation, evidence retention, customer communication, and assigned corrective actions. SEV-1/2 incidents require a blameless review with timeline, contributing controls, detection gaps, and dated owners.
