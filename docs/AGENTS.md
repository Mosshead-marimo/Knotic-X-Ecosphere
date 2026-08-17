# AGENTS.md

## Purpose
Instructions for Codex and other coding agents working on this repository.

## Source-of-truth order
1. `REQUIREMENTS.md`
2. `System_Design.md`
3. `ARCHITECTURE_DECISIONS.md`
4. `API_CONTRACTS.md`
5. `DATA_MODEL.md`
6. `MCP_TOOLS.md`
7. tests
8. existing code

## Implementation task tracking

- Executable work orders live in `phases/README.md` and the numbered phase files it links.
- When instructed to complete a task such as `P2-T004`, open the matching phase file and complete only that task after checking its dependencies.
- A phase task defines implementation scope and verification; it never overrides the source-of-truth order above.
- Mark a task complete only when every acceptance criterion and verification requirement passes.
- After completing a task, update `CHANGES_MADE.md` with the files changed, verification evidence, and remaining follow-up.

## Mandatory stack
- Frontend: React / Next.js + TypeScript
- Voice: Agora Web SDK / Agora RTC
- Backend: Python + Flask
- Agent orchestration: LangGraph
- LLM/RAG utilities: LangChain where useful
- Tool/integration boundary: MCP
- Active state: Redis
- Durable DB: PostgreSQL
- Vector search: pgvector
- Packaging/deployment: Docker

Do not replace Flask with FastAPI, LangGraph with a custom loop, or MCP with direct ad-hoc integrations unless explicitly requested.

## Ownership
- Agora owns realtime voice and interruption behavior.
- Flask owns APIs, sessions, token generation, and service composition.
- LangGraph owns adaptive sales workflow and state transitions.
- MCP owns access to external knowledge and business actions.
- Redis owns hot conversational state.
- PostgreSQL owns durable records.
- pgvector owns semantic knowledge retrieval.

## Non-negotiable rules
1. Never hardcode live pricing into prompts.
2. Never claim a meeting is booked until the calendar tool confirms success.
3. Never fabricate CRM/tool success.
4. Raw transcript is not the only memory source; maintain structured state.
5. Latest confirmed requirement replaces prior active value.
6. Preserve change history for auditability.
7. High-impact actions require deterministic policy checks/human approval.
8. Human escalation must include a structured context packet.
9. External dependency failures must degrade safely.
10. Log all MCP calls and important state changes.
11. Do not expose server credentials to the frontend.
12. Do not allow the LLM to execute arbitrary SQL.

## Coding guidance
- Keep modules small and typed.
- Separate pure decisions from I/O.
- Keep prompts in dedicated modules/files.
- Validate tool inputs/outputs.
- Add unit tests for new decision logic.
- Prefer mock adapters for early end-to-end work.
