import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const signatures = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  /(?:aws_access_key_id|aws_secret_access_key)\s*[:=]\s*["']?(?!replace-me)/i,
  /(?:api[_-]?key|client[_-]?secret|password|token)\s*[:=]\s*["'][A-Za-z0-9_+./=-]{24,}["']/i,
  /(?:postgres(?:ql)?|redis):\/\/[^\s:@/]+:[^\s@/]{8,}@/i,
];

function findings(path, content) {
  const placeholder = /(?:replace-me|example|placeholder|test-only|secure-password|secure-redis-password|db-password|do-not-print|:password@)/i;
  return content.split(/\r?\n/).flatMap((line, index) => {
    if (placeholder.test(line)) return [];
    return signatures.flatMap((signature) => signature.test(line) ? [`${path}:${index + 1}: ${signature}`] : []);
  });
}

if (process.argv.includes("--self-test")) {
  const detected = findings("deliberate-invalid-fixture", 'api_key = "realisticSecretMaterial1234567890"'); // test-only negative fixture
  if (detected.length !== 1) throw new Error("secret scanner failed its deliberate failing-change test");
  console.log("Secret scanner self-test passed: deliberate credential was rejected.");
  process.exit(0);
}

const files = execFileSync("git", ["ls-files", "-z"], { encoding: "utf8" }).split("\0").filter(Boolean);
const failures = [];
for (const file of files) {
  if (/\.(?:png|jpe?g|gif|ico|woff2?|lock)$/i.test(file)) continue;
  try {
    failures.push(...findings(file, readFileSync(file, "utf8")));
  } catch {
    // Ignore non-text tracked artifacts; dedicated artifact scanners own them.
  }
}
if (failures.length) {
  console.error(`Potential committed credentials detected:\n${failures.join("\n")}`);
  process.exit(1);
}
console.log(`Repository secret scan passed across ${files.length} tracked files.`);
