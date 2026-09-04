# Phase 4 — Realtime Agora Voice Experience

## Objective

Provide secure, accessible, low-latency voice conversations with correct interruption, recovery, and state synchronization.

## Entry criteria

- Phase 3 gate passed.
- Agora and speech provider production accounts, quotas, regions, and data policies are approved.

## Implementation references

Use [`../AGORA_VOICE_AI.md`](../AGORA_VOICE_AI.md) for the Agora CLI onboarding path, managed-provider prototype, BYOK guardrails, and official recipes relevant to this phase. Recipes are references only; implementations must preserve the Flask, LangGraph, MCP, security, and state-ownership decisions in the source-of-truth documents.

## Tasks

### [ ] P4-T001 — Implement secure Agora session/token service

- Dependencies: P0-T003, P1-T005.
- Implement: Authenticated short-lived token issuance, channel/user authorization, server-side credentials, renewal, revocation, rate limits, and audit events.
- Acceptance: Users cannot join unauthorized tenant/session channels; secrets never reach the frontend.
- Verify: Authorization, expiry, renewal, replay, and bundle-secret tests.

### [ ] P4-T002 — Implement production call UI

- Dependencies: P4-T001.
- Implement: Join/leave, consent, microphone/device controls, permission states, call status, reconnecting state, errors, keyboard access, and responsive UI.
- Acceptance: Supported browsers and accessibility targets pass; all failure states provide a safe next step.
- Verify: Browser matrix, accessibility audit, device/permission tests.

### [ ] P4-T003 — Implement realtime speech input pipeline

- Dependencies: P4-T001, P2-T002.
- Implement: Audio capture, voice activity/turn detection, streaming transcription, semantic turn finalization, language handling, confidence, cancellation, and session correlation.
- Acceptance: Turns are ordered, deduplicated, attributable, and measured without storing unnecessary raw audio.
- Verify: Noise, silence, accents, packet loss, long-turn, duplicate-event, and privacy tests.

### [ ] P4-T004 — Implement AI speech output pipeline

- Dependencies: P2-T008, P4-T002.
- Implement: Stream response plans to synthesis/playback, chunking, cancellation, buffer limits, voice configuration, fallback, and content synchronization.
- Acceptance: Playback begins within the approved latency budget and never continues after cancellation confirmation.
- Verify: Latency, cancellation, long-response, provider-failure, and device-switch tests.

### [ ] P4-T005 — Implement barge-in and interrupted-response state

- Dependencies: P4-T003, P4-T004.
- Implement: Detect customer speech during playback, immediately cancel/truncate output, prioritize input, record delivered/interrupted boundaries, and resume only if still relevant.
- Acceptance: FR-02 behavior is deterministic; interrupted text is not treated as heard beyond the delivered boundary.
- Verify: Repeated barge-in, false-positive, race, rapid-turn, and topic-change tests.

### [x] P4-T006 — Synchronize voice events with backend state

- Dependencies: P1-T008, P2-T009, P4-T003–P4-T005.
- Implement: Ordered event protocol, sequence numbers, acknowledgements, idempotency, reconnect replay, and reconciliation between client, Agora, Flask, and LangGraph.
- Acceptance: Disconnects and duplicate/out-of-order events cannot corrupt the conversation timeline.
- Verify: Network partition, reordering, reconnect, multi-tab, and backend-restart tests.

### [ ] P4-T007 — Implement recovery and graceful termination

- Dependencies: P4-T006.
- Implement: Token renewal, transient reconnect, provider failover where approved, timeout policy, customer messaging, state persistence, and clean end-call behavior.
- Acceptance: Recoverable faults resume within limits; unrecoverable faults preserve state and terminate without false claims.
- Verify: Agora, speech, network, backend, and Redis failure drills.

### [ ] P4-T008 — Enforce voice privacy, consent, and abuse controls

- Dependencies: P4-T002, P4-T003.
- Implement: Consent capture, recording/transcript policy, retention, sensitive-data handling, mute guarantees, abuse/rate controls, regional routing, and deletion workflow.
- Acceptance: Data processing matches approved policy and consent; muted audio is not transmitted.
- Verify: Privacy review, mute traffic inspection, retention/deletion test.

### [ ] P4-T009 — Add end-to-end voice observability

- Dependencies: P4-T003–P4-T007.
- Implement: Per-turn traces for capture, transcription, graph, tools, synthesis, first audio, interruption, failures, and quality signals without logging sensitive content by default.
- Acceptance: Operators can isolate latency and failure ownership using one session correlation ID.
- Verify: Trace walkthrough, dashboard/alert review, redaction test.

### [ ] P4-T010 — Certify realtime performance and compatibility

- Dependencies: P4-T001–P4-T009.
- Implement: Browser/device/network test matrix, concurrent-call capacity tests, latency percentiles, soak tests, quota monitoring, and release thresholds.
- Acceptance: Approved p50/p95/p99 latency, interruption, error-rate, and concurrency targets pass in production-like infrastructure.
- Verify: Signed performance report and release-gate results.

## Phase gate

Multi-turn calls, barge-in, reconnect, privacy, browser compatibility, and production-load tests pass; end-to-end latency and reliability meet approved targets; secrets and tenant channels are protected.
