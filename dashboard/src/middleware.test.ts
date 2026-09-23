import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
// Next's own matcher compiler and runtime matcher: the exact code the build and
// the standalone server use, so this pins the regression that shipped an
// ungated dashboard root inside the Docker image.
import * as staticInfo from "next/dist/build/analysis/get-page-static-info";
import { getMiddlewareRouteMatcher } from "next/dist/shared/lib/router/utils/middleware-route-matcher";
import { config, middleware } from "./middleware";

// Exported at runtime but not in Next's .d.ts.
const { getMiddlewareMatchers } = staticInfo as unknown as {
  getMiddlewareMatchers: (matcher: string | string[], nextConfig: { basePath: string }) => Parameters<typeof getMiddlewareRouteMatcher>[0];
};

const BASE = "/t/alice/dashboard";

afterEach(() => vi.unstubAllEnvs());

function gated(basePath: string): (pathname: string) => boolean {
  const compiled = getMiddlewareMatchers(config.matcher, { basePath });
  const match = getMiddlewareRouteMatcher(compiled);
  return (pathname) => Boolean(match(pathname, { headers: {} } as never, {}));
}

describe("middleware matcher", () => {
  it("gates the dashboard root under a basePath (no trailing slash)", () => {
    const isGated = gated(BASE);
    expect(isGated(BASE)).toBe(true);
    expect(isGated(`${BASE}/`)).toBe(true);
    expect(isGated(`${BASE}/factors`)).toBe(true);
    expect(isGated(`${BASE}/logs/r1`)).toBe(true);
    expect(isGated(`${BASE}/api/score`)).toBe(true);
  });

  it("leaves health checks and static assets open", () => {
    const isGated = gated(BASE);
    expect(isGated(`${BASE}/healthz`)).toBe(false);
    expect(isGated(`${BASE}/_next/static/chunks/x.js`)).toBe(false);
    expect(isGated(`${BASE}/favicon.ico`)).toBe(false);
  });

  it("gates the root when mounted at / (local dev)", () => {
    const isGated = gated("");
    expect(isGated("/")).toBe(true);
    expect(isGated("/healthz")).toBe(false);
  });
});

describe("middleware gate", () => {
  const req = (path: string, init: { headers?: Record<string, string> } = {}) =>
    new NextRequest(`http://localhost:3000${path}`, { ...init, nextConfig: { basePath: BASE } });

  it("is a no-op without ACCESS_TOKEN", () => {
    vi.stubEnv("ACCESS_TOKEN", "");
    expect(middleware(req(BASE)).status).toBe(200);
  });

  it("401s a missing or wrong token, HTML for pages and JSON for /api", async () => {
    vi.stubEnv("ACCESS_TOKEN", "testtoken123");
    expect(middleware(req(BASE)).status).toBe(401);
    expect(middleware(req(`${BASE}?token=nope`)).status).toBe(401);
    const api = middleware(req(`${BASE}/api/score`));
    expect(api.status).toBe(401);
    expect(await api.json()).toEqual({ error: "missing or wrong access token" });
  });

  it("passes the right token by query, header, or cookie, and sets the cookie on the basePath", () => {
    vi.stubEnv("ACCESS_TOKEN", "testtoken123");
    const first = middleware(req(`${BASE}?token=testtoken123`));
    expect(first.status).toBe(200);
    const cookie = first.cookies.get("bryan_token");
    expect(cookie?.value).toBe("testtoken123");
    expect(cookie?.path).toBe(BASE);
    expect(cookie?.httpOnly).toBe(true);

    expect(middleware(req(BASE, { headers: { cookie: "bryan_token=testtoken123" } })).status).toBe(200);
    expect(middleware(req(`${BASE}/api/score`, { headers: { "X-Access-Token": "testtoken123" } })).status).toBe(200);
  });
});
