import { createRequire } from "node:module";

// Turbopack's own postcss transform resolves each plugin key by requiring it directly from the
// perspective of frontend/.next/build's internal chunk (not plain Node resolution), and fails to
// find dependencies hoisted to the monorepo root's node_modules/ (one level up from frontend/)
// even though `require.resolve` from this file finds them fine. Handing it an already-resolved
// absolute path instead of the bare package name sidesteps that resolution entirely.
const require = createRequire(import.meta.url);

/** @type {import('postcss-load-config').Config} */
const config = {
  plugins: {
    [require.resolve("@tailwindcss/postcss")]: {},
  },
};

export default config;
