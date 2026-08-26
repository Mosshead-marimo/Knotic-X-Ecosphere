# Phase 2 — Adaptive Sales Workflow

## Objective

Deliver a deterministic, testable LangGraph sales brain that handles nonlinear conversations without fabricating actions or facts.

## Entry criteria

- Phase 1 gate passed.
- Model provider, prompt, and evaluation policies are approved.

## Tasks

### [x] P2-T001 — Define graph state and node contracts

- Dependencies: P1-T001, P1-T008.
- Implement: Versioned LangGraph state, node input/output schemas, checkpoint identity, error types, and pure-versus-I/O node boundaries.
- Acceptance: Every graph node has typed inputs/outputs and explicit allowed state mutations.
- Verify: Schema and graph-compilation tests.

### [x] P2-T002 — Implement turn understanding and structured extraction

- Dependencies: P2-T001.
- Implement: Intent/entity extraction with validated structured output, confidence, provenance, ambiguity handling, and prompt-injection-resistant boundaries.
- Acceptance: All FR-06 intents are representable; invalid model output cannot mutate state.
- Verify: Golden conversation set, malformed-output, multilingual/accent, and adversarial tests.

### [x] P2-T003 — Implement memory update graph node

- Dependencies: P1-T006, P1-T007, P2-T002.
- Implement: Apply confirmed extractions to structured memory, preserve uncertain claims separately, emit events, and maintain current topic.
- Acceptance: Latest confirmed requirements replace active values; uncertain statements do not overwrite confirmed facts.
- Verify: Revision, ambiguity, topic-switch, and replay tests.

### [x] P2-T004 — Implement intent routing and nonlinear topic control

- Dependencies: P2-T002, P2-T003.
- Implement: Routes for discovery, pricing, product, competitor, objection, revision, demo, booking, follow-up, handoff, general questions, and closing; support return to prior topics.
- Acceptance: Every FR-06 intent reaches an allowed path; routing is reproducible from state and turn evidence.
- Verify: Route matrix and multi-topic conversation tests.

### [ ] P2-T005 — Implement objection detection and policy

- Dependencies: P2-T002.
- Implement: Detect every FR-07 category, store evidence/history, select approved handling policy, and escalate high-risk security/legal/trust cases.
- Acceptance: Objections are not erased by topic changes; unsupported claims are grounded or escalated.
- Verify: Category matrix, repeated objection, and escalation tests.

### [ ] P2-T006 — Implement qualification engine

- Dependencies: P2-T003.
- Implement: Pure scoring rules for the seven FR-09 dimensions, stage thresholds, evidence, missing-data behavior, recalculation, and explicit-request overrides.
- Acceptance: Scores are bounded 0–100, explainable, deterministic, and historically auditable.
- Verify: Boundary tests at 39/40/59/60/74/75 and property-based score tests.

### [ ] P2-T007 — Implement next-best-action engine

- Dependencies: P2-T004, P2-T005, P2-T006.
- Implement: Deterministic policy for every FR-10 action, precedence, required data, approval requirements, and safe fallback.
- Acceptance: Actions cannot bypass booking confirmation, tool grounding, or human-approval policies.
- Verify: Decision-table tests and prohibited-transition tests.

### [ ] P2-T008 — Implement response planning and generation

- Dependencies: P2-T007.
- Implement: Dedicated prompt modules, grounded context assembly, concise voice-oriented plans, uncertainty language, citation metadata, and output validation.
- Acceptance: Responses do not invent pricing, availability, integrations, CRM state, or completed actions.
- Verify: Golden outputs, hallucination probes, injection tests, and human rubric evaluation.

### [ ] P2-T009 — Implement graph checkpointing, retries, and safe failures

- Dependencies: P2-T001, P2-T008.
- Implement: Per-session checkpoints, bounded retries, timeouts, resumable failures, duplicate-turn protection, and deterministic terminal states.
- Acceptance: Reinvoking a committed turn does not duplicate side effects; failures yield recoverable user-safe outcomes.
- Verify: Fault injection at every node and replay tests.

### [ ] P2-T010 — Build conversation evaluation suite

- Dependencies: P2-T004–P2-T009.
- Implement: Versioned datasets for discovery, revision, objections, pricing, competitors, demo, follow-up, handoff, closing, unsafe requests, and failure cases; automated quality thresholds.
- Acceptance: CI measures routing, extraction, score, policy, groundedness, and response quality with approved minimum thresholds.
- Verify: Reproducible evaluation run and regression-failure demonstration.

## Phase gate

All intents, objections, scores, actions, and failure paths pass deterministic tests and evaluation thresholds; graph replay is idempotent; no transactional success can be asserted without a validated tool result.
