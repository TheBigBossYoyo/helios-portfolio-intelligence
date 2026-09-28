import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
      // `server-only` throws on import outside a server condition. The guard is a build-time
      // contract for Next, not something the unit tests need to re-prove.
      "server-only": fileURLToPath(new URL("./tests/stubs/server-only.ts", import.meta.url)),
      // `revalidatePath` needs an active Next request store. The actions' contract with the
      // API is what these tests check; Next's cache bookkeeping is the framework's own.
      "next/cache": fileURLToPath(new URL("./tests/stubs/next-cache.ts", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    // Card spending is grouped in local time; pin the zone so day/week buckets are the same on
    // every machine that runs the suite.
    env: { TZ: "UTC" },
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
