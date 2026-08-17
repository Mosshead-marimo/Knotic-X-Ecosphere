import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";
import process from "node:process";

const root = resolve(import.meta.dirname, "..");
const frontend = join(root, "frontend");
const readableExtensions = new Set([".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".txt", ".map"]);
const ignoredDirectories = new Set(["node_modules", "cache"]);

const forbiddenPatterns = [
  {
    name: "server-only Knotic setting",
    expression:
      /KNOTIC_(?:DATABASE_URL|REDIS_URL|MCP_AUTH_TOKEN(?:_PREVIOUS)?|AGORA_APP_CERTIFICATE)/i,
  },
  {
    name: "publicly exposed credential variable",
    expression: /NEXT_PUBLIC_[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|CERTIFICATE|PRIVATE_KEY|DATABASE_URL|REDIS_URL)/i,
  },
  {
    name: "private key material",
    expression: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  },
  {
    name: "credential-bearing connection URL",
    expression: /(?:postgres(?:ql)?|redis(?:s)?)?:\/\/[^\s/:]+:[^\s@]+@/i,
  },
];

function collectFiles(path) {
  if (!statSync(path).isDirectory()) {
    return readableExtensions.has(extname(path)) ? [path] : [];
  }

  const files = [];
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    if (entry.isDirectory() && ignoredDirectories.has(entry.name)) {
      continue;
    }
    const entryPath = join(path, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(entryPath));
    } else if (readableExtensions.has(extname(entry.name))) {
      files.push(entryPath);
    }
  }
  return files;
}

function scanText(text) {
  return forbiddenPatterns.filter(({ expression }) => expression.test(text)).map(({ name }) => name);
}

if (process.argv.includes("--self-test")) {
  const safe = scanText("NEXT_PUBLIC_AGORA_APP_ID=public-id");
  const unsafe = scanText("NEXT_PUBLIC_API_TOKEN=should-never-ship");
  if (safe.length !== 0 || !unsafe.includes("publicly exposed credential variable")) {
    throw new Error("frontend secret scanner self-test failed");
  }
  console.log("Frontend secret scanner self-test passed");
  process.exit(0);
}

const scanRoots = [join(frontend, "src"), join(frontend, "public"), join(frontend, "package.json")];
const nextOutput = join(frontend, ".next");
try {
  if (statSync(nextOutput).isDirectory()) {
    scanRoots.push(join(nextOutput, "static"), join(nextOutput, "server"));
  }
} catch {
  // A source-only scan is valid before the first production build.
}

const violations = [];
let scannedFiles = 0;
for (const scanRoot of scanRoots) {
  try {
    for (const file of collectFiles(scanRoot)) {
      scannedFiles += 1;
      for (const match of scanText(readFileSync(file, "utf8"))) {
        violations.push(`${relative(root, file)}: ${match}`);
      }
    }
  } catch (error) {
    if (error?.code !== "ENOENT") {
      throw error;
    }
  }
}

if (violations.length > 0) {
  console.error("Frontend secret scan failed:");
  for (const violation of violations) {
    console.error(`- ${violation}`);
  }
  process.exit(1);
}

console.log(`Frontend secret scan passed (${scannedFiles} files checked)`);

