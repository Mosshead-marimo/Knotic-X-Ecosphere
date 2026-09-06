# Phase 7 — Interactive Frontend Design

## Objective

Give the sales-agent frontend a clean, interactive, production-quality design across every customer-facing surface, built iteratively with Superdesign one target (page, flow, or component) at a time, then implemented as accessible, responsive Next.js UI without regressing any Phase 4 voice functionality.

## Entry criteria

- Phase 4 gate passed for the voice call experience this phase redesigns rather than replaces.
- Superdesign CLI installed and authenticated (`superdesign login`) for this repository.
- `superdesign init` has completed and `.superdesign/init/` exists with the extracted design-system context. Design tasks below cannot start until this file exists — do not fabricate a design system from assumptions if init has not actually run.

## Implementation references

Use the Superdesign skill's own SOP (`references/SUPERDESIGN.md` and `references/RESUME.md` in the installed skill) for how each design target is produced: read the init context, gather real source for the target page, create the project, produce a faithful first pass, persist resume state, then branch variations, and return the preview/canvas URL. One target is designed and approved at a time — never propose or batch multiple pages into a single design pass. Preserve every decision already made in `../System_Design.md`, `../ARCHITECTURE_DECISIONS.md`, and Phase 4's voice-call state machine (`frontend/src/features/voice/`); a visual redesign must not change `useAgoraCall`'s state transitions, consent flow, or error handling without a corresponding update to `PHASE_4_REALTIME_VOICE.md`.

## Tasks

### [ ] P7-T001 — Extract and codify the design system

- Dependencies: none (first task; gated on `superdesign init` output existing).
- Implement: Run Superdesign's repo init, then translate its extracted context (color, type scale, spacing, existing component patterns) into a checked-in design-token source (e.g. Tailwind theme config or CSS custom properties) that the rest of this phase's tasks build from.
- Acceptance: A single source of truth for color/typography/spacing exists in the repo; no page-level task hardcodes a one-off value the token set should own.
- Verify: Visual diff of existing pages (home, call) against the new tokens shows no unintended regression; a design-system review confirms tokens match the approved direction.

### [ ] P7-T002 — Redesign the landing/home experience

- Dependencies: P7-T001.
- Implement: Apply Superdesign's per-page SOP to `frontend/src/app/page.tsx` — produce a faithful first pass, iterate to an approved design, then implement it as the real page (not a static mockup) using the Phase 7 design tokens.
- Acceptance: The page is responsive, passes the same accessibility bar as Phase 4 (keyboard access, visible focus, `aria-live` where content updates dynamically), and its Superdesign preview URL and final approved variant are recorded in `docs/CHANGES_MADE.md`.
- Verify: Lighthouse/accessibility audit, responsive-breakpoint review, and stakeholder sign-off on the approved variant.

### [ ] P7-T003 — Redesign the voice call experience

- Dependencies: P7-T001, Phase 4 gate.
- Implement: Restyle `VoiceCallPanel` and `frontend/src/app/call/[sessionId]/page.tsx` to the new design system — consent, status region, start/end call, mute — without altering `useAgoraCall`'s behavior, state names, or error copy defined in Phase 4. Any interaction change (e.g. new call-status visualization) must stay compatible with the existing `CallStatus` union.
- Acceptance: Every P4-T002 acceptance criterion (supported browsers, accessibility targets, safe next step on every failure state) still passes after the redesign.
- Verify: Re-run the Phase 4 browser/accessibility/device matrix against the redesigned UI; regression-test the join/leave/mute/error flows.

### [ ] P7-T004 — Interaction and motion polish pass

- Dependencies: P7-T002, P7-T003.
- Implement: Add purposeful, restrained interactive states (hover/focus/pressed, loading, transition/motion) across the redesigned surfaces using the design-token set, respecting `prefers-reduced-motion`.
- Acceptance: No interactive element lacks a visible state change; motion never blocks or delays a user action, and is fully suppressed under `prefers-reduced-motion`.
- Verify: Manual interaction pass across breakpoints, `prefers-reduced-motion` emulation test.

### [ ] P7-T005 — Frontend design QA and phase certification

- Dependencies: P7-T001–P7-T004.
- Implement: Full responsive, accessibility (WCAG 2.1 AA target), and cross-browser pass across every redesigned page; reconcile any Superdesign resume-state drift against the shipped code.
- Acceptance: All redesigned pages pass the accessibility and responsive bar; no page's shipped implementation has silently diverged from its approved Superdesign preview without a recorded reason.
- Verify: Signed accessibility audit, responsive test matrix, and a design/engineering sign-off recorded in `docs/CHANGES_MADE.md`.

## Phase gate

Every customer-facing page has an approved Superdesign preview, a shipped implementation matching it, a passing accessibility and responsive audit, and zero regressions against the Phase 4 voice-call acceptance criteria.
