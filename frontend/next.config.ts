import path from "node:path";
import type { NextConfig } from "next";

const config: NextConfig = {
  agentRules: false,
  output: "standalone",
  poweredByHeader: false,
  // Turbopack can mis-infer the workspace root in an npm-workspaces monorepo (this app's
  // package.json lives in frontend/, but node_modules and the lockfile are hoisted to the repo
  // root one level up), which makes it fail to resolve hoisted dependencies like
  // @tailwindcss/postcss even though plain Node resolves them fine. Pointing it at the actual
  // monorepo root fixes that resolution.
  turbopack: {
    root: path.join(__dirname, ".."),
  },
};

export default config;
