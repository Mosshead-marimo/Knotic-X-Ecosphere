# Runtime and Dependency Policy

## Authoritative pins

- JavaScript runtime and package manager: `.node-version`, `.nvmrc`, and root `package.json`.
- Python runtime and uv: `.python-version` and root `pyproject.toml`.
- JavaScript dependency resolution: committed root `package-lock.json`.
- Python workspace resolution: committed root `uv.lock`.
- Database, cache, vector extension, and future container bases: `infra/runtime-versions.env`.

Direct application dependencies are exact in component manifests. Transitive dependencies are exact in lockfiles. Do not hand-edit lockfiles.

## Reproducible installation

```text
npm ci
uv sync --locked --all-packages --all-groups
```

Install commands must fail when manifests and lockfiles disagree. Local toolchains must match the declared runtime pins; do not bypass engine or uv version checks in normal development or CI.

## Vulnerability and malware checks

```text
npm audit --audit-level=high
uv run --locked pip-audit
```

uv malware checking is enabled during sync. Dependabot checks npm and uv dependencies weekly. Repository administrators must also enable Dependabot security updates and vulnerability alerts in the hosting platform settings.

Critical or high vulnerabilities block release unless a documented exception records the affected package, exposure, compensating controls, owner, and expiry date. A dependency update must regenerate the relevant lockfile and pass clean-install, audit, build, and regression checks.

## Update boundaries

- Patch and minor dependency updates may be grouped by ecosystem after automated verification exists.
- Major updates require an explicit compatibility review and must not be grouped automatically.
- Runtime, database, Redis, pgvector, and base-image upgrades require an architecture/operations review because they affect deployment and recovery compatibility.
- `P0-T008` must replace mutable container tags with verified immutable digests while preserving the human-readable version pins.

