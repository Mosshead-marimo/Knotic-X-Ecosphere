import { existsSync, readFileSync } from "node:fs";

const requirements = new Map([
  ["docs/DEVELOPMENT.md", ["npm ci", "uv sync --locked", "npm run quality", "docker compose up", "Database changes"]],
  ["docs/RELEASE.md", ["immutable", "SBOM", "backward-compatible", "canary", "bake window"]],
  ["docs/ROLLBACK.md", ["last known-good", "forward fix", "idempotency", "provider-confirmed"]],
  ["docs/INCIDENT_RESPONSE.md", ["Incident commander", "SEV-1", "correlation IDs", "on-call"]],
]);

const failures = [];
for (const [path, phrases] of requirements) {
  if (!existsSync(path)) {
    failures.push(`${path}: missing`);
    continue;
  }
  const content = readFileSync(path, "utf8");
  const normalized = content.toLowerCase();
  for (const phrase of phrases) {
    if (!normalized.includes(phrase.toLowerCase())) failures.push(`${path}: missing required guidance '${phrase}'`);
  }
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log(`Operational documentation validation passed: ${requirements.size} runbooks are complete.`);
