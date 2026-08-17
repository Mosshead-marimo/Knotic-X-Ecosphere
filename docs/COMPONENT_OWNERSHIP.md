# Component Ownership and Dependency Boundaries

## Purpose

Define authoritative responsibilities and allowed dependencies. This document refines repository organization without overriding `AGENTS.md`, `REQUIREMENTS.md`, or `System_Design.md`.

## Ownership matrix

| Component | Owns | May depend on | Must not own |
|---|---|---|---|
| Frontend | Browser UI, device controls, Agora Web SDK integration, call presentation | Versioned Flask APIs and Agora client APIs | Server credentials, durable business state, direct database or business integrations |
| Flask backend | APIs, authentication context, sessions, Agora token generation, service composition, LangGraph entry | Redis, PostgreSQL repositories, LangGraph, typed MCP client | Browser audio transport internals, ad-hoc external integrations |
| LangGraph workflow | Adaptive sales state transitions, intent and objection routing, qualification, next-best action, escalation decisions | Structured state and validated MCP results | Direct provider SDK calls, arbitrary SQL, unvalidated model/tool output |
| MCP service | Authentication, policy, validation, audit, knowledge tools, and external business action tools | Authoritative providers, PostgreSQL/pgvector through controlled repositories | Conversation transport, ungoverned model-driven side effects |
| Redis/data platform | Active state, concurrency control, durable records, semantic index, backup and recovery mechanisms | Approved service identities and migrations | Conversation decisions or externally reported transactional success |
| Infrastructure/operations | Containers, deployment, secrets delivery, observability, reliability, release and recovery | Versioned service artifacts and approved configuration contracts | Product policy or business fact invention |

## Required dependency direction

```text
Frontend -> Flask backend -> LangGraph -> MCP client -> MCP service -> providers
                               |                         |
                               +-> Redis/PostgreSQL      +-> pgvector/approved data
```

- Frontend access to server-side data and actions goes through authenticated Flask APIs.
- LangGraph receives external facts and actions only through validated MCP results.
- Database access uses typed repositories; the LLM never receives arbitrary SQL capability.
- Mutable transactional truth comes from authoritative providers, not prompts or RAG documents.
- Cross-component contract changes must be documented and tested before consumers rely on them.

## Repository locations

| Location | Responsible boundary |
|---|---|
| `frontend/` | Frontend and realtime browser experience |
| `backend/` | Flask API, sessions, state composition, and LangGraph |
| `mcp/` | MCP gateway, tools, policies, and provider adapters |
| `packages/config/` | Shared pure configuration, secret-provider, and redaction primitives |
| `infra/` | Container, deployment, and observability configuration |
| `tests/` | Cross-service verification |
| `docs/` | Product, architecture, contracts, task planning, and audit history |

Named human owners and on-call escalation are established in operational readiness work. Until then, changes require review from the engineer responsible for the affected boundary and every downstream contract consumer.
