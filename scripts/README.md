# Repository automation

Portable, reviewed repository automation belongs here. Scripts must fail safely, avoid embedded credentials and machine-specific paths, and document required inputs and side effects.

- `validate-api-contract.mjs` validates the OpenAPI 3.1 document, contract invariants, event mappings, and executable examples without network access or generated output.
- `validate-data-model.mjs` validates durable/active ownership, entities, Redis key contracts, lifecycle controls, and requirement traceability without external services.
- `validate-mcp-contract.mjs` validates all logical MCP tool schemas and their authorization, approval, idempotency, retry, audit, failure, and requirement policies.
