# MCP Server Integration Guide

## What the console does

The **Add MCP** action under `/console/integrations` creates a tenant-scoped, audited registration request. It does not connect to the supplied URL from the browser, accept credentials, or claim that the server is active. Only `ADMIN` and `SUPERVISOR` roles can submit requests.

## Server prerequisites

Before requesting registration, the MCP owner must provide:

1. A stable public HTTPS endpoint using Streamable HTTP where possible; SSE is supported only for legacy servers.
2. A documented tool inventory with JSON schemas, bounded inputs/outputs, timeouts, and safe errors.
3. One or more supported capability classifications: `KNOWLEDGE`, `CRM`, `CALENDAR`, `MESSAGING`, or `HANDOFF`.
4. An owner, support contact, data classification, regions, rate/quota limits, and outage behavior.
5. An authentication method: bearer workload token, OAuth 2.0, or explicitly approved unauthenticated access.
6. Idempotency and provider-confirmation behavior for every side-effecting tool.

The server must never accept tenant identity, actor identity, credentials, arbitrary SQL, or unrestricted provider URLs from model-controlled tool arguments.

## Request registration

1. Sign in to the console as an administrator or supervisor.
2. Open **MCP gateway** and select **Add MCP**.
3. Enter a human-readable name and the HTTPS MCP endpoint.
4. Select the transport, authentication scheme, and capabilities.
5. Submit the request. A `requested` card appears immediately.

Do not paste an API key, bearer token, client secret, password, or private certificate into any frontend field.

## Platform validation and activation

The platform owner must then:

1. Resolve the hostname and reject loopback, private, link-local, metadata-service, or unexpected addresses at connection time to prevent SSRF and DNS-rebinding bypasses.
2. Pin the allowed HTTPS origin and verify TLS hostname/certificate policy.
3. Fetch and validate the MCP tool registry through a restricted egress worker, never during the database transaction that accepted the request.
4. Compare every tool against `MCP_TOOLS.md`; reject unknown identity fields, arbitrary URLs/SQL, unbounded payloads, missing timeouts, or unsafe result schemas.
5. Approve least-privilege scopes and deterministic policy/human approval for side effects.
6. Create the credential in the environment-specific secret manager. Store only the provider reference in deployment configuration; never persist or return the secret through the console API.
7. Configure per-provider deadline, retry, circuit breaker, bulkhead, quota, audit, and redaction controls.
8. Run authentication-expiry, tenant-isolation, schema, idempotency, timeout, invalid-result, outage, and provider-confirmation tests.
9. Activate the registration through the platform-owned activation workflow only after every check passes.

The current delivery implements the registration request and visibility boundary. The restricted validation worker, secret-reference binding, and activation endpoint remain required before a requested server can become operational.

## Environment variables

Existing first-party Flask-to-MCP communication uses:

```dotenv
KNOTIC_MCP_BASE_URL=https://mcp.internal.example.com
KNOTIC_MCP_AUTH_TOKEN=<secret-manager-injected-token-at-least-32-characters>
KNOTIC_MCP_AUTH_TOKEN_PREVIOUS=
KNOTIC_MCP_ALLOWED_HOSTS=["mcp.internal.example.com"]
```

Do not invent a dynamic MCP credential variable yet: the current runtime has no code that consumes one. When the activation worker is implemented, it must use an allow-listed secret-provider reference associated with the registration ID and document the exact binding. Production secrets must be injected by the deployment runtime, not committed to `.env` or placed in any `NEXT_PUBLIC_` variable.

## Verification

After activation exists, verify each server in staging by checking that:

- the console moves from `requested` to `validating` and finally `active` only after runtime confirmation;
- unauthorized roles cannot register or activate it;
- another tenant cannot list or invoke it;
- no credential appears in API responses, frontend bundles, logs, audit metadata, or error messages;
- read tools return schema-valid grounded data with citations where required;
- writes remain pending until authoritative provider confirmation;
- outage and quota failures degrade safely and remain visible for reconciliation.
