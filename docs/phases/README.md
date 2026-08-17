# Production Implementation Task Index

## Purpose

This directory converts the product requirements and system design into production-ready, independently addressable work orders.

## Source of truth

Before implementing any task, read these files in order:

1. `../AGENTS.md`
2. `../REQUIREMENTS.md`
3. `../System_Design.md`
4. `../ARCHITECTURE_DECISIONS.md` when available
5. `../API_CONTRACTS.md` when available
6. `../DATA_MODEL.md` when available
7. `../MCP_TOOLS.md` when available

If a task conflicts with a higher-priority document, stop and report the conflict. Do not silently reinterpret the requirement.

## Phase files

| Phase | File | Outcome |
|---|---|---|
| 0 | `PHASE_0_FOUNDATION.md` | Approved contracts and reproducible project foundation |
| 1 | `PHASE_1_STATE_AND_DATA.md` | Reliable active and durable conversation state |
| 2 | `PHASE_2_SALES_WORKFLOW.md` | Tested adaptive LangGraph sales workflow |
| 3 | `PHASE_3_MCP_AND_KNOWLEDGE.md` | Grounded, governed business tools and knowledge |
| 4 | `PHASE_4_REALTIME_VOICE.md` | Low-latency Agora voice with correct interruption |
| 5 | `PHASE_5_INTEGRATIONS.md` | Confirmed CRM, calendar, follow-up, and handoff actions |
| 6 | `PHASE_6_PRODUCTION_READINESS.md` | Secure, observable, recoverable production deployment |

## Task ID format

Task IDs are stable and use `P<phase>-T<three-digit-number>`, for example `P2-T004`.

## Instruction to give Codex

```text
Read docs/AGENTS.md and the source-of-truth documents it lists. Then open
docs/phases/PHASE_2_SALES_WORKFLOW.md and complete task P2-T004 only.
Respect its dependencies, scope, acceptance criteria, and verification steps.
Do not mark it complete unless every acceptance criterion passes. Update
docs/CHANGES_MADE.md with the verified change and report any blockers.
```

For a full phase, instruct Codex to execute the tasks in listed order, stopping on unmet dependencies or failed acceptance criteria.

## Required task workflow

For every task, Codex must:

1. Confirm all dependencies are complete.
2. Inspect existing code and preserve unrelated user changes.
3. Implement only the stated scope plus necessary compatibility changes.
4. Add or update tests for behavior introduced by the task.
5. Run the task verification and relevant regression checks.
6. Record evidence, limitations, and follow-up work.
7. Update `../CHANGES_MADE.md` and the task checkbox only after verification succeeds.

## Definition of production ready

A phase is production ready only when all its tasks are complete and its phase gate passes. Mock adapters, skipped tests, undocumented manual steps, unreviewed security exceptions, and unmeasured reliability claims do not satisfy a production gate. Any temporary exception must have an owner, reason, risk, expiry date, and tracked removal task.

