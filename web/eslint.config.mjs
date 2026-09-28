import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTypeScript,
  globalIgnores([
    ".next/**",
    // Playwright dist dirs (E2E_DIST_DIR): each agent/CI run builds into its own
    // ".next-e2e-*" so parallel E2E runs don't collide; none of them are source.
    ".next-e2e-*/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);
