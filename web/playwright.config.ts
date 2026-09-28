import { defineConfig, devices } from "@playwright/test";
import { STUB_API_PORT, WEB_PORT } from "./tests/e2e/ports";

/** A separate build directory per run, so two runs never overwrite each other's `.next`. */
const DIST_DIR = process.env.E2E_DIST_DIR || ".next";

/**
 * E2E runs the real production build against a stubbed Helios API, so a run needs no Trading 212
 * credentials, no database, and no network — and still exercises the actual server-rendered
 * pages, not a mock of them.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  // Every route is `force-dynamic`, and the App Router resolves the RSC payload *before* it
  // commits a URL change. Under a parallel run the heaviest page (performance: 120 NAV points
  // plus every series) can take longer than the 5s default, so a URL assertion fails while the
  // navigation is still perfectly healthy. Raise the assertion budget rather than the app's.
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? "line" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `node tests/e2e/stub-api.mjs`,
      env: { STUB_API_PORT: String(STUB_API_PORT) },
      url: `http://127.0.0.1:${STUB_API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
    },
    {
      command: `npm run build && npx next start --port ${WEB_PORT} --hostname 127.0.0.1`,
      env: { HELIOS_API_URL: `http://127.0.0.1:${STUB_API_PORT}`, NEXT_DIST_DIR: DIST_DIR },
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
    },
  ],
});
