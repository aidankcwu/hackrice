/**
 * The backend client. Nothing else in the app calls `fetch`.
 *
 *   NEXT_PUBLIC_API_BASE    backend origin, default http://localhost:8010
 *   NEXT_PUBLIC_API_TOKEN   sent as `Authorization: Bearer <token>` when set
 *   NEXT_PUBLIC_FIXTURES=1  serve phone/fixtures/*.json instead of the network
 */
import { fixtureUnreachable, loadFixture } from "./fixtures";
import type { Decision, Episode, EvidenceFrame, Healthspan, Session, Status } from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8010").replace(/\/+$/, "");
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN || "";
export const FIXTURES = process.env.NEXT_PUBLIC_FIXTURES === "1";

/** IOS_SPEC "Data layer": 15 s, then the request counts as failed. */
const TIMEOUT_MS = 15_000;

/**
 * A request that did not produce a payload. `status` is the HTTP status the
 * backend answered with, or `null` when it never answered (offline, wrong
 * address, timeout), so a screen can name the right fix.
 */
export class ApiError extends Error {
  constructor(
    readonly path: string,
    readonly status: number | null,
  ) {
    super(status === null ? `${path} unreachable` : `${path} -> ${status}`);
    this.name = "ApiError";
  }
}

type Method = "GET" | "POST" | "PUT" | "DELETE";

/** One network round trip. `cache: "no-store"` keeps every answer, frames included, off the disk. */
async function send(path: string, method: Method, accept: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = { accept };
  if (API_TOKEN) headers.authorization = `Bearer ${API_TOKEN}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new ApiError(path, null);
  }
  if (!response.ok) throw new ApiError(path, response.status);
  return response;
}

export async function request<T>(path: string, method: Method = "GET", body?: unknown): Promise<T> {
  if (FIXTURES) {
    if (fixtureUnreachable()) throw new ApiError(path, null);
    // Fixtures are read-only captures: a write has nothing to land on.
    const data = method === "GET" ? await loadFixture(path) : undefined;
    if (data === undefined) throw new ApiError(path, method === "GET" ? 404 : 405);
    return data as T;
  }
  return (await (await send(path, method, "application/json", body)).json()) as T;
}

/**
 * One saved evidence frame as an in-memory object URL for an `<img>`. The bearer
 * token cannot ride on an `<img src>`, and a plain `src` would land the JPEG in the
 * browser's disk cache; this does neither. The caller revokes the URL when the view
 * goes away. No frames were captured for fixtures mode, so it answers 404 there.
 */
export async function evidenceFrameUrl(decisionId: string, ref: string): Promise<string> {
  const path = `/api/evidence/${encodeURIComponent(decisionId)}/${encodeURIComponent(ref)}`;
  if (FIXTURES) throw new ApiError(path, fixtureUnreachable() ? null : 404);
  return URL.createObjectURL(await (await send(path, "GET", "image/jpeg")).blob());
}

export const api = {
  status: () => request<Status>("/api/status"),
  /** The open session, `null` when none. `/api/status` carries the same object with `elapsed_s`. */
  session: () => request<Session | null>("/api/session/current"),
  healthspan: () => request<Healthspan>("/api/healthspan"),
  episodes: () => request<Episode[]>("/api/episodes"),
  decisions: (limit = 50) => request<Decision[]>(`/api/decisions?limit=${limit}`),
  evidence: (decisionId: string) => request<EvidenceFrame[]>(`/api/evidence/${encodeURIComponent(decisionId)}`),
};
