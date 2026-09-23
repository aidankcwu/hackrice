import type { NextConfig } from "next";

/**
 * Hosting knobs, all build-time (Next bakes basePath into the bundle):
 *
 *   NEXT_BASE_PATH=/t/alice/dashboard   mount point behind the reverse proxy.
 *                                       The proxy must NOT strip it for the
 *                                       dashboard. Unset = served at "/" (dev).
 *   NEXT_OUTPUT=standalone              self-contained server for the Docker image.
 *
 * Runtime (per container, no rebuild): BACKEND_URL, ACCESS_TOKEN, BRIAN_PYTHON.
 * See dashboard/Dockerfile.
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
  // The client needs the mount point for fetches to its own /api routes and plain <a> links.
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
};

export default nextConfig;
