# Production Configuration and Secret Handling

## Loading model

Backend and MCP settings are typed and validated before either process starts. Both services share the pure `knotic-config` package for environment selection, secret-provider resolution, safe error summaries, and logging redaction.

Configuration precedence is explicit:

1. Constructor values supplied by application composition.
2. Process environment for non-secret settings.
3. An explicitly supplied environment file for local development or tests.
4. Model defaults for safe, non-secret development values only.

Environment files are never loaded implicitly. Production and staging must inject configuration through the deployment runtime and secrets through a `SecretProvider`; `.env.example` is a local template and intentionally fails until placeholders are replaced.

## Environment separation

| Environment | Debug | Origins/hosts | Secrets |
|---|---|---|---|
| `development` | Optional | Local values permitted | Local environment or approved developer secret store |
| `test` | Optional | Test-local values permitted | Injected deterministic test provider |
| `staging` | Disabled | Explicit non-local hosts and HTTPS browser origins required | Staging secret namespace only |
| `production` | Disabled | Explicit non-local hosts and HTTPS browser origins required | Production secret namespace only |

Never reuse staging or production secrets across environments. Each environment must have separate database credentials, Redis credentials, MCP tokens, and Agora certificates.

## Setting inventory

| Name | Classification | Consumers | Required |
|---|---|---|---|
| `KNOTIC_ENV`, `KNOTIC_LOG_LEVEL`, `KNOTIC_DEBUG` | Non-secret | Backend, MCP | Defaults exist |
| `KNOTIC_BACKEND_HOST`, `KNOTIC_BACKEND_PORT` | Non-secret | Backend | Defaults exist |
| `KNOTIC_ALLOWED_ORIGINS` | Non-secret JSON list | Backend | Staging/production |
| `KNOTIC_MCP_BASE_URL` | Non-secret | Backend | Always |
| `KNOTIC_AGORA_APP_ID` | Server configuration | Backend | Always |
| `KNOTIC_MCP_HOST`, `KNOTIC_MCP_PORT` | Non-secret | MCP | Defaults exist |
| `KNOTIC_MCP_ALLOWED_HOSTS` | Non-secret JSON list | MCP | Staging/production |
| `KNOTIC_DATABASE_URL` | Secret | Backend, MCP | Always |
| `KNOTIC_REDIS_URL` | Secret | Backend, MCP | Always |
| `KNOTIC_MCP_AUTH_TOKEN` | Secret | Backend, MCP | Always |
| `KNOTIC_MCP_AUTH_TOKEN_PREVIOUS` | Secret | Backend, MCP | Only during rotation |
| `KNOTIC_AGORA_APP_CERTIFICATE` | Secret | Backend | Always |
| `KNOTIC_SESSION_SECURITY_KEY` | Secret | Backend | Always |

No server-only variable may use a `NEXT_PUBLIC_` prefix or appear in frontend source, assets, generated bundles, browser logs, or client error payloads.

## Secret providers

`EnvironmentSecretProvider` is the deployment-neutral default. Production deployments should inject an adapter implementing the `SecretProvider` protocol for the chosen secret manager. Provider adapters must authenticate using workload identity, return secrets only by allowlisted name, avoid caching beyond the provider TTL, and never log secret values.

Tests use `MappingSecretProvider`; application code must not read secrets directly from `os.environ`.

## Rotation procedure

1. Create a new secret version in the provider without changing the active version.
2. For MCP authentication, deploy the old token as `KNOTIC_MCP_AUTH_TOKEN_PREVIOUS` and the new token as `KNOTIC_MCP_AUTH_TOKEN` so consumers can overlap safely after authentication behavior is implemented.
3. Roll MCP, then backend, and verify both versions through redacted authentication metrics. Never print or compare tokens in logs.
4. Remove the previous token after the maximum request/session lifetime and a confirmed zero-use window.
5. For database, Redis, Agora, and session-security credentials, use provider-supported overlapping credentials or a blue/green rollout; rebuild connection pools after activation. Rotating the session-security key invalidates browser sessions and outstanding idempotency replays, so use a planned bounded session drain until dual-key decryption is implemented.
6. Revoke the old credential, record actor/time/reason/provider version, and verify that rollback credentials follow the same expiry policy.

Emergency rotation skips the overlap only when compromise risk exceeds availability risk. The incident owner must document the resulting interruption.

## Logging and errors

- Store credential fields as `SecretStr`; representations remain masked.
- Install `RedactingFilter` before service handlers emit logs and pass all resolved secret values for exact-value replacement.
- Structured keys such as tokens, passwords, certificates, authorization, cookies, and connection URLs are recursively redacted.
- Startup errors report missing variable names and validation rules, never submitted values.
- Run `npm run scan:frontend-secrets` after every production frontend build.
