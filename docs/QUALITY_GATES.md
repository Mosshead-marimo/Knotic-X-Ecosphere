# Quality Gates

## Local parity

Install the exact runtimes from `.node-version`, `package.json`, `.python-version`, and `uv.toml`, then run:

```console
npm ci
uv sync --locked --all-packages --all-groups
npm run quality
docker compose build
```

`npm run quality` is the same fail-fast gate used by CI. It verifies runtime pins, ESLint, strict TypeScript, Ruff formatting/linting, strict mypy, Python unit tests, API/data/MCP contracts, frontend and repository credential scans, the secret-scanner negative fixture, and npm/Python dependency audits.

## Required GitHub checks

The `Required quality gates` workflow runs on every pull request and on pushes to `dev` and `main`. Repository rules must require these checks before merge:

- `quality`
- `container-security (frontend)`
- `container-security (backend)`
- `container-security (mcp)`
- `topology`

The container jobs build from the committed Dockerfiles and use an immutable Trivy action to reject fixable HIGH or CRITICAL findings. Workflow permissions are read-only; third-party actions are pinned to commits; jobs have timeouts; redundant runs are cancelled.

Repository administrators must also require pull requests, dismiss stale approvals after new commits, require the branch to be current, block force pushes/deletions, and prevent bypass. The workflow is enforcement code, while the GitHub ruleset is the server-side merge control; both are required.

## Deliberate failure proof

`npm run quality:self-test` supplies a credential-shaped invalid fixture to the repository scanner. The command succeeds only when the scanner rejects that fixture. Contract validators also contain invalid schema examples that must be rejected. This proves the gates detect known-bad changes rather than merely returning success.

Do not weaken a gate to make a change pass. Fix the change, or update the policy and its rationale in the same reviewed pull request.
