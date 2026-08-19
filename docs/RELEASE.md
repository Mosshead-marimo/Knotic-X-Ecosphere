# Release Procedure

Production release is blocked until the deployment platform profile, named on-call owners, secrets adapter, managed data services, telemetry backend, backup policy, and Phase 6 readiness gate are approved. This document defines the provider-neutral release baseline; it does not claim those approvals exist.

## Release inputs

- Reviewed pull request with all required checks green and no unresolved HIGH/CRITICAL findings.
- Immutable Git commit, version, image digests, generated SBOMs, and provenance/signature records.
- Approved change record identifying release owner, incident commander, affected contracts/data, maintenance window, and rollback trigger.
- Tested backup/restore point for affected durable data and verified secret/config references for the target environment.

## Procedure

1. Freeze the candidate commit and verify both lockfiles are unchanged after clean installs.
2. Run `npm run quality` and build all images without cache in a clean trusted runner.
3. Scan, sign, and publish images by digest. Never deploy mutable tags.
4. Apply backward-compatible expand migrations and verify them before application rollout.
5. Roll MCP, backend, then frontend using a canary or blue/green strategy. Keep the previous release available.
6. Validate liveness, readiness, authentication, safe failure behavior, telemetry, and one non-destructive synthetic journey.
7. Observe the agreed bake window. Compare error rate, dependency failures, and latency against the release thresholds.
8. Promote, record exact deployed digests/schema versions, and close the change only after monitoring remains healthy.

Contract removals and destructive schema contraction require a later release after all consumers and retained data have migrated. Secrets are referenced by provider version and never copied into release records.
