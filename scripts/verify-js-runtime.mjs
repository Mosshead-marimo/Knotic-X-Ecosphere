import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const manifest = JSON.parse(readFileSync(new URL("../package.json", import.meta.url)));
const expectedNode = manifest.engines.node;
const expectedNpm = manifest.engines.npm;
const actualNode = process.versions.node;
const actualNpm = process.env.npm_execpath
  ? execFileSync(process.execPath, [process.env.npm_execpath, "--version"], {
      encoding: "utf8",
    }).trim()
  : process.platform === "win32"
    ? execFileSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", "npm --version"], {
        encoding: "utf8",
      }).trim()
    : execFileSync("npm", ["--version"], { encoding: "utf8" }).trim();

const failures = [];

if (actualNode !== expectedNode) {
  failures.push(`Node.js ${expectedNode} is required; found ${actualNode}.`);
}

if (actualNpm !== expectedNpm) {
  failures.push(`npm ${expectedNpm} is required; found ${actualNpm}.`);
}

if (failures.length > 0) {
  console.error(failures.join("\n"));
  console.error("Install the versions declared in .node-version and package.json.");
  process.exit(1);
}

console.log(`Runtime verified: Node.js ${actualNode}, npm ${actualNpm}`);
