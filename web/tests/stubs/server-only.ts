// Test stub for the `server-only` package. The real module throws on import so that a client
// component can never pull in server code; that guard is enforced by the Next build, and
// `npm run build` is what proves it. Under vitest the import is a no-op.
export {};
