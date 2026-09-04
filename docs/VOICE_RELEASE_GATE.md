# Realtime Voice Release Gate

## Status

The certification mechanism is implemented. A production certification is not yet issued because
the required approved Agora/speech accounts, quota evidence, regional/data-policy approval, and a
production-like test run have not been supplied. Do not mark `P4-T010` or the Phase 4 gate complete
until a trusted release signer produces a `PASS` report for the exact release commit.

## Version 1 thresholds

The checked-in policy at `tests/performance/voice-release-policy.v1.json` is the proposed release
baseline: at least 1,000 turns, 100 peak concurrent calls, a 120-minute soak, error rate at or below
1%, provider quota peak at or below 80%, first-audio p50/p95/p99 at or below 800/1,500/2,500 ms, and
interruption p50/p95/p99 at or below 120/300/500 ms. Product, platform, security, and provider owners
must approve this version before it can be used as release authority.

The compatibility plan is `tests/e2e/voice-compatibility-matrix.v1.json`. A certification input must
contain at least 20 recorded cases, every case must pass, and each case must identify the exact
browser/version, OS, device, network profile, commit SHA, timestamp, and immutable evidence URI.

## Evidence and signing procedure

1. Deploy the exact candidate commit to production-like infrastructure with the approved provider
   accounts, regions, quotas, secrets, and observability configuration.
2. Run the compatibility matrix, a 100-concurrent-call capacity test, and a 120-minute soak. Export
   content-free first-audio/interruption samples, total/failed turns, peak concurrency, peak quota
   use, and immutable result URIs into the `VoiceCertificationEvidence` JSON schema.
3. Store the Ed25519 release-signing private key in the managed secret system. Never commit it or
   pass key material on the command line; only a protected temporary key-file path is accepted.
4. Run `uv run knotic-certify-voice evidence.json --policy tests/performance/voice-release-policy.v1.json --private-key <protected-path> --key-id <trusted-key-id> --output report.json`.
5. A non-zero exit or any `FAIL`/`INCOMPLETE` status blocks release. Verify the detached signature
   with the registered Ed25519 public key before approving deployment, archive the signed report
   and raw evidence immutably, and link them from the release record.

The evaluator hashes the canonical raw evidence into the report, computes nearest-rank p50/p95/p99,
checks error/capacity/soak/quota/matrix gates, and refuses `PASS` without a provider-approval reference.
