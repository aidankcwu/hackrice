/**
 * Where the dashboard is mounted and where the pipeline backend is, resolved
 * at runtime in the browser, so one build serves both localhost dev and a
 * hosted tester behind the reverse proxy:
 *
 *   https://DOMAIN/t/NAME/dashboard/   the dashboard (Next basePath, not stripped)
 *   https://DOMAIN/t/NAME/api/...      that tester's backend (proxy strips /t/NAME)
 *
 * Isomorphic on purpose (no `server-only`): client components import it. The
 * Next.js server talks to the backend through `lib/score/backend.ts`, which
 * reads `BACKEND_URL` / `ACCESS_TOKEN` from its own container env instead.
 */

/** Build-time mount point (`NEXT_BASE_PATH`, surfaced by next.config.ts). "" at the root. */
export const BASE_PATH: string = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** The header every backend request carries the tester's token in. */
export const TOKEN_HEADER = "X-Access-Token";
/** Query param: read from the dashboard URL on first load, appended to image URLs. */
export const TOKEN_PARAM = "token";
/** Cookie the middleware sets once a `?token=` passes, so page loads stay authorised. */
export const TOKEN_COOKIE = "bryan_token";

/**
 * localStorage key prefix. Every tester's dashboard is on the SAME origin
 * (https://DOMAIN/t/NAME/dashboard), and localStorage is per-origin, so one key
 * would let Bob's link overwrite Alice's token on a shared laptop. The key is
 * namespaced by the `/t/NAME` mount instead — see `tokenStorageKey`.
 */
const TOKEN_STORAGE_KEY = "bryan.token";
/** The backend's port in local dev (backend/README: `uvicorn ... --port 8010`). */
export const DEV_BACKEND_PORT = "8010";
export const DEV_API_BASE = `http://localhost:${DEV_BACKEND_PORT}`;

/** A tester's mount: `/t/NAME` at the start of the path. */
const TESTER_PREFIX = /^\/t\/[^/]+/;

const trimSlash = (s: string): string => s.replace(/\/+$/, "");

/** `/foo` → `${BASE_PATH}/foo`, for the places Next does not prefix itself (fetch, plain <a>). */
export function withBasePath(path: string): string {
  if (!path.startsWith("/")) return path;
  return `${BASE_PATH}${path}`;
}

export interface LocationLike {
  protocol: string;
  hostname: string;
  port: string;
  pathname: string;
  origin: string;
}

/**
 * The backend base URL for a page at `loc`, no trailing slash.
 *
 * 1. `NEXT_PUBLIC_API_BASE`, when the build set one, always wins.
 * 2. A `/t/NAME` prefix on the page means the reverse proxy: same origin, same prefix.
 * 3. A page on the scheme's default port (no `:port`) is behind a proxy too: same origin.
 * 4. Otherwise it is local dev (`next dev` on :3000): the backend is on :8010 of the
 *    same host — `localhost:3000` → `localhost:8010`, exactly as before this existed.
 */
export function resolveApiBase(loc: LocationLike, configured?: string): string {
  const explicit = configured?.trim();
  if (explicit) return trimSlash(explicit);
  const prefix = TESTER_PREFIX.exec(loc.pathname);
  if (prefix) return `${loc.origin}${prefix[0]}`;
  if (loc.port === "") return loc.origin;
  return `${loc.protocol}//${loc.hostname}:${DEV_BACKEND_PORT}`;
}

/** Backend base for this page. During SSR there is no location: the configured or dev default. */
export function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE;
  if (typeof window === "undefined") return configured?.trim() ? trimSlash(configured.trim()) : DEV_API_BASE;
  return resolveApiBase(window.location, configured);
}

// ---------------------------------------------------------------------------
// The tester's token
// ---------------------------------------------------------------------------

/** The localStorage key for a page at `pathname`: `bryan.token:/t/NAME` behind the proxy, `bryan.token` at the root (dev). */
export function tokenStorageKey(pathname: string): string {
  const prefix = TESTER_PREFIX.exec(pathname);
  return prefix ? `${TOKEN_STORAGE_KEY}:${prefix[0]}` : TOKEN_STORAGE_KEY;
}

/**
 * `href` with every `?token=` removed, as a path+query+hash suitable for
 * `history.replaceState`; null when there was nothing to remove.
 */
export function stripTokenParam(href: string): string | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (!url.searchParams.has(TOKEN_PARAM)) return null;
  url.searchParams.delete(TOKEN_PARAM);
  return `${url.pathname}${url.search}${url.hash}`;
}

/**
 * Takes `?token=` out of the address bar once it has been captured, so it does
 * not sit in history, get copied with the URL, or leak via a screenshot. The
 * middleware has already set the httpOnly cookie on this same request, and the
 * token is in storage/memory, so nothing needs the query param any more.
 * Deferred a tick: `accessToken()` can run during a React render, and Next's
 * patched `history.replaceState` updates router state, which must not happen
 * mid-render. `history.state` is passed through so Next keeps its own entry.
 */
function scrubTokenFromAddressBar(): void {
  const run = () => {
    try {
      const cleaned = stripTokenParam(window.location.href);
      if (cleaned !== null) window.history.replaceState(window.history.state, "", cleaned);
    } catch {
      // No history API (sandboxed frame): leaving the URL alone is harmless.
    }
  };
  setTimeout(run, 0);
}

/** Survives a blocked localStorage for the life of the tab, keyed like storage. */
const memoryTokens = new Map<string, string>();

/**
 * `?token=` on the current URL (stored for later, then scrubbed from the address
 * bar), else the stored one for this page's `/t/NAME`, else null. The URL is
 * re-read on every call so the very first request after load already carries
 * it, whichever component happens to fire first.
 */
export function accessToken(): string | null {
  if (typeof window === "undefined") return null;
  let key = TOKEN_STORAGE_KEY;
  try {
    key = tokenStorageKey(window.location.pathname);
    const fromUrl = new URLSearchParams(window.location.search).get(TOKEN_PARAM)?.trim();
    if (fromUrl) {
      memoryTokens.set(key, fromUrl);
      try {
        window.localStorage.setItem(key, fromUrl);
      } catch {
        // Storage blocked (private mode): the in-memory copy carries this tab.
      }
      scrubTokenFromAddressBar();
      return fromUrl;
    }
  } catch {
    // No usable location; fall through to what was stored.
  }
  const inMemory = memoryTokens.get(key);
  if (inMemory) return inMemory;
  try {
    const stored = window.localStorage.getItem(key)?.trim();
    if (stored) memoryTokens.set(key, stored);
    return stored || envToken();
  } catch {
    return envToken();
  }
}

/**
 * The single deploy token compiled in as `NEXT_PUBLIC_API_TOKEN` (docs/DEPLOY.md,
 * one backend behind one token), used only when no per-tester token was pasted.
 */
function envToken(): string | null {
  return process.env.NEXT_PUBLIC_API_TOKEN?.trim() || null;
}

/** Headers for a backend request: the token when there is one, plus `extra`. */
export function backendHeaders(extra?: Record<string, string>, token: string | null = accessToken()): Record<string, string> {
  return token ? { ...extra, [TOKEN_HEADER]: token } : { ...extra };
}

/** Appends `?token=` (or `&token=`) to a URL; unchanged when there is no token. */
export function withToken(url: string, token: string | null): string {
  if (!token) return url;
  const hash = url.indexOf("#");
  const [head, tail] = hash === -1 ? [url, ""] : [url.slice(0, hash), url.slice(hash)];
  const sep = head.includes("?") ? "&" : "?";
  return `${head}${sep}${TOKEN_PARAM}=${encodeURIComponent(token)}${tail}`;
}

/**
 * Whether absolute `url` is under the backend `base`: same origin and, when the
 * base has a path (`/t/alice`), inside it on a segment boundary — so
 * `/t/alice/api/…` qualifies but `/t/alicex/…` and `/t/bob/…` do not.
 */
export function isUnderBase(url: string, base: string): boolean {
  let u: URL;
  let b: URL;
  try {
    u = new URL(url);
    b = new URL(base);
  } catch {
    return false;
  }
  if (u.origin !== b.origin) return false;
  const prefix = trimSlash(b.pathname);
  return prefix === "" || u.pathname === prefix || u.pathname.startsWith(`${prefix}/`);
}

/**
 * A browser-loadable backend URL (for <img src>): a server-relative path is put
 * on the backend base and gets `?token=`. Images cannot send headers, so the
 * query param is how the backend sees them. An absolute URL is kept, and only
 * gets the token when it points under that same backend base: the token is a
 * credential, and a frame URL naming some other host (or another tester's
 * prefix on this host) must never receive it.
 */
export function backendUrl(pathOrUrl: string, base: string = apiBase(), token: string | null = accessToken()): string {
  const absolute = /^https?:\/\//i.test(pathOrUrl);
  if (absolute) return isUnderBase(pathOrUrl, base) ? withToken(pathOrUrl, token) : pathOrUrl;
  return withToken(`${base}${pathOrUrl.startsWith("/") ? "" : "/"}${pathOrUrl}`, token);
}

/** `fetch` against the backend with the token header; `path` starts with `/`. */
export function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = backendHeaders(init.headers as Record<string, string> | undefined);
  return fetch(`${apiBase()}${path}`, { ...init, headers });
}

/**
 * `fetch` against this dashboard's own `/api/*` routes: basePath-prefixed, and
 * with the token header too, so the middleware passes it even where the
 * cookie did not stick (blocked cookies, a plain-http test proxy).
 */
export function appFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = backendHeaders(init.headers as Record<string, string> | undefined);
  return fetch(withBasePath(path), { ...init, headers });
}

/** Constant-time string compare (the middleware's token check). */
export function sameToken(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}
