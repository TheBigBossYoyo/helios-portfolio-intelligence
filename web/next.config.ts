import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Overridable so parallel test runs can build side by side (see tests/e2e/ports.ts).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  allowedDevOrigins: ["127.0.0.1"],
  output: "standalone",
  turbopack: {
    root: process.cwd(),
  },
  typescript: {
    ignoreBuildErrors: false,
  },
};

export default nextConfig;
