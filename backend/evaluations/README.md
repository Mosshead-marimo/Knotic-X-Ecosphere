# Conversation evaluation

`sales-conversations-v1.json` is the reviewed expected corpus. It covers discovery, confirmed revision, objections, pricing, competitors, demo, follow-up, handoff, closing, unsafe requests, and dependency failure. `reference-predictions-v1.json` is the version-matched candidate output used by the deterministic CI baseline.

Run the gate from the repository root:

```console
uv run --project backend python -m knotic_api.workflow.evaluation
```

The gate measures exact routing, structured extraction, qualification score plus stage, next-action policy, supported-citation grounding, and voice-response safety/quality. Approved minimums are versioned in code: routing, extraction, score, policy, and groundedness are `1.00`; response quality is `0.95`. A missing/duplicate case, version mismatch, unknown citation, unsupported claim, overlong response, more than three sentences, or unconfirmed transaction-success claim fails the relevant metric.

Dataset and prediction changes require review together. Never lower a threshold merely to admit a regression. Create a new dataset version when expected behavior or schema changes, keep the prior corpus for comparison, record reviewer rationale in `docs/CHANGES_MADE.md`, and add a regression test demonstrating that the prior defect fails.
