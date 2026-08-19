import { spawnSync } from "node:child_process";

const gates = [
  ["npm", ["run", "verify:runtime"]],
  ["npm", ["run", "lint"]],
  ["npm", ["run", "typecheck"]],
  ["npm", ["run", "format:check"]],
  ["npm", ["run", "lint:python"]],
  ["npm", ["run", "typecheck:python"]],
  ["npm", ["run", "test:python"]],
  ["npm", ["run", "validate:api-contract"]],
  ["npm", ["run", "validate:data-model"]],
  ["npm", ["run", "validate:mcp-contract"]],
  ["npm", ["run", "validate:operations-docs"]],
  ["npm", ["run", "scan:frontend-secrets"]],
  ["npm", ["run", "scan:repository-secrets"]],
  ["npm", ["run", "quality:self-test"]],
  ["npm", ["run", "audit:dependencies"]],
];

for (const [command, args] of gates) {
  console.log(`\n> ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, { stdio: "inherit", shell: process.platform === "win32" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
