/**
 * Where the app is mounted, where its backend is and which token to send,
 * resolved at runtime in the browser, so one build serves both localhost dev
 * and a hosted tester behind the reverse proxy (deploy/README.md):
 *
 *   https://DOMAIN/t/NAME/app/     this app (Next basePath, prefix kept by the proxy)
 *   https://DOMAIN/t/NAME/api/...  that tester's backend (proxy strips /t/NAME)
 *
 * Nothing secret is compiled in. The VC opens `…/t/NAME/app/?token=TOKEN`; the
 * token is kept in localStorage under a key namespaced by `/t/NAME`, taken out
 * of the address bar, and sent as `Authorization: Bearer` on every backend
 * request (the backend accepts it; deploy/README.md "The contract").
 *
 * Isomorphic on purpose: the proxy (src/proxy.ts) and route handlers import
 * the constants and `sameToken`; everything touching `window` guards itself.
 */

/** Build-time mount point (`NEXT_BASE_PATH`, surfaced by next.config.ts). "" at the root. */
export const BASE_PATH: string = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** Query param: read from the page URL on first load, appended to image URLs. */
export const TOKEN_PARAM = "token";
/** Header scripts and curl may use instead (the backend reads it first). */
export const TOKEN_HEADER = "x-access-token";
/** Cookie the proxy sets once a `?token=` passes, so reloads stay authorised. */
export const TOKEN_COOKIE = "brian_app_token";

/** Local dev: the backend's default address (backend/README: `--port 8010`), unchanged. */
export const DEV_API_BASE = "http://localhost:8010";

/**
 * localStorage key prefix. Every tester's app is on the SAME origin, and
 * localStorage is per origin, so one key would let Bob's link overwrite
 * Alice's token on a shared phone. Namespaced by `/t/NAME` instead.
 */
const TOKEN_STORAGE_KEY = "brian.token";

/** A tester's mount: `/t/NAME` at the start of the path. */
const TESTER_PREFIX = /^\/t\/[^/]+/;

const trimSlash = (s: string): string => s.replace(/\/+$/, "");

export interface LocationLike {
  pathname: string;
  origin: string;
}

/**
 * The backend base URL for a page at `loc`, no trailing slash.
 *
 * 1. `NEXT_PUBLIC_API_BASE`, when the build set one, always wins (dev on a Wi‑Fi IP).
 * 2. A `/t/NAME` prefix on the page means the reverse proxy: same origin, same
 *    prefix, the `/app` segment dropped (`/t/alice/app/x` → `https://DOMAIN/t/alice`).
 * 3. Otherwise local dev: `http://localhost:8010`, exactly as before this existed.
 */
export function resolveApiBase(loc: LocationLike | null, configured?: string): string {
  const explicit = configured?.trim();
  if (explicit) return trimSlash(explicit);
  const prefix = loc ? TESTER_PREFIX.exec(loc.pathname) : null;
  if (loc && prefix) return `${loc.origin}${prefix[0]}`;
  return DEV_API_BASE;
}

/** Backend base for this page. During prerender there is no location: the configured or dev default. */
export function apiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE;
  return resolveApiBase(typeof window === "undefined" ? null : window.location, configured);
}

/** True when this page is served under `/t/NAME` (a hosted tester). */
export function isHosted(): boolean {
  return typeof window !== "undefined" && TESTER_PREFIX.test(window.location.pathname);
}

// ---------------------------------------------------------------------------
// The tester's token
// ---------------------------------------------------------------------------

/** `brian.token:/t/NAME` behind the proxy, `brian.token` at the root (dev). */
export function tokenStorageKey(pathname: string): string {
  const prefix = TESTER_PREFIX.exec(pathname);
  return prefix ? `${TOKEN_STORAGE_KEY}:${prefix[0]}` : TOKEN_STORAGE_KEY;
}

/** `href` without any `?token=`, as path+query+hash for `history.replaceState`; null when there was none. */
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
 * Takes `?token=` out of the address bar once captured, so it does not sit in
 * history, get copied with the URL or show in a screenshot. Deferred a tick:
 * `accessToken()` can run during a render, and Next's patched `replaceState`
 * updates router state. `history.state` is passed through so Next keeps its entry.
 */
function scrubTokenFromAddressBar(): void {
  setTimeout(() => {
    try {
      const cleaned = stripTokenParam(window.location.href);
      if (cleaned !== null) window.history.replaceState(window.history.state, "", cleaned);
    } catch {
      // No history API (sandboxed frame): leaving the URL alone is harmless.
    }
  }, 0);
}

/** Survives a blocked localStorage for the life of the tab, keyed like storage. */
const memoryTokens = new Map<string, string>();

function remember(key: string, token: string): void {
  memoryTokens.set(key, token);
  try {
    window.localStorage.setItem(key, token);
  } catch {
    // Storage blocked (private mode): the in-memory copy carries this tab.
  }
}

/**
 * `?token=` on the current URL (stored, then scrubbed from the address bar),
 * else the stored one for this page's `/t/NAME`, else the dev build's
 * `NEXT_PUBLIC_API_TOKEN`, else null. Re-reads the URL on every call so the
 * very first request after load already carries it.
 */
export function accessToken(): string | null {
  if (typeof window === "undefined") return null;
  const key = tokenStorageKey(window.location.pathname);
  const fromUrl = new URLSearchParams(window.location.search).get(TOKEN_PARAM)?.trim();
  if (fromUrl) {
    remember(key, fromUrl);
    scrubTokenFromAddressBar();
    return fromUrl;
  }
  const inMemory = memoryTokens.get(key);
  if (inMemory) return inMemory;
  try {
    const stored = window.localStorage.getItem(key)?.trim();
    if (stored) {
      memoryTokens.set(key, stored);
      return stored;
    }
  } catch {
    // Storage blocked: fall through.
  }
  return process.env.NEXT_PUBLIC_API_TOKEN?.trim() || null;
}

let recovering: Promise<string | null> | null = null;

/**
 * `accessToken()`, or — on a hosted page that has none (a Home Screen launch,
 * whose storage is separate from Safari's; cleared storage) — the token this
 * app's own `/api/token` hands back to a request its proxy already let in by
 * the cookie. Asked at most once per page load.
 */
export async function ensureAccessToken(): Promise<string | null> {
  const known = accessToken();
  if (known || !isHosted()) return known;
  recovering ??= (async () => {
    try {
      const res = await fetch(`${BASE_PATH}/api/token`, { cache: "no-store", credentials: "same-origin" });
      if (!res.ok) return null;
      const { token } = (await res.json()) as { token?: string | null };
      if (!token) return null;
      remember(tokenStorageKey(window.location.pathname), token);
      return token;
    } catch {
      return null;
    }
  })();
  return recovering;
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
 * A browser-loadable backend URL for `<img src>` (images cannot send headers):
 * a backend-relative path (`/api/evidence/…`) on the backend base, with `?token=`.
 * An absolute URL is returned untouched: the token never goes to another host.
 */
export function backendImageUrl(path: string, base: string = apiBase(), token: string | null = accessToken()): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(path)) return path;
  return withToken(`${base}${path.startsWith("/") ? "" : "/"}${path}`, token);
}

/** Constant-time string compare (the proxy's token check). */
export function sameToken(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}
