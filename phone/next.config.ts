import type { NextConfig } from "next";

/**
 * Hosting knobs, all build-time (Next bakes basePath into the bundle):
 *
 *   NEXT_BASE_PATH=/t/alice/app   mount point behind the reverse proxy, which
 *                                 must NOT strip it. Unset = served at "/" (dev).
 *   NEXT_OUTPUT=standalone        self-contained server for the Docker image.
 *
 * Runtime (per container, no rebuild): ACCESS_TOKEN (src/proxy.ts). The backend
 * address and the token are resolved in the browser (src/lib/runtime.ts), so a
 * hosted image carries no secret and no NEXT_PUBLIC_API_* value. See phone/Dockerfile.
 */
function normalizeBasePath(raw: string | undefined): string {
  const value = (raw ?? "").trim().replace(/\/+$/, "");
  if (value === "") return "";
  return value.startsWith("/") ? value : `/${value}`;
}

const basePath = normalizeBasePath(process.env.NEXT_BASE_PATH);

const nextConfig: NextConfig = {
  ...(basePath ? { basePath } : {}),
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" as const } : {}),
  // The client needs the mount point for its own /api/token and the manifest's URLs.
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
  // `npm run dev -- -H 0.0.0.0`, opened from the iPhone at this PC's Wi-Fi
  // address: the dev server refuses its own scripts to any origin not listed
  // here, so allow the private LAN ranges (dev only; no effect on a build).
  allowedDevOrigins: ["10.*.*.*", "172.*.*.*", "192.168.*.*", "*.local"],
  // The dev badge would sit in every design screenshot.
  devIndicators: false,
};

export default nextConfig;
