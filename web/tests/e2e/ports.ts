/**
 * Ports for one Playwright run. Overridable so several runs (and several agents or terminals)
 * can build and test side by side without one reusing another's server: set
 * `E2E_STUB_PORT`, `E2E_WEB_PORT` and `E2E_DIST_DIR` together.
 */
export const STUB_API_PORT = Number(process.env.E2E_STUB_PORT || 8099);
export const WEB_PORT = Number(process.env.E2E_WEB_PORT || 3099);
export const STUB_API_URL = `http://127.0.0.1:${STUB_API_PORT}`;
