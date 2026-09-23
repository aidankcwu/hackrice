/**
 * Server-side fetch against the pipeline's FastAPI backend
 * (backend/pipeline/api/routes.py): one `DayInputs` per calendar day for the
 * week table, and the backend's own healthspan score.
 *
 * No React and no engine here: HTTP and a pivot of the long-format seeded rows.
 * Anything that fails as a whole throws `BackendOffline`, and there is no
 * fallback: a page with no backend behind it shows no numbers (R1).
 */
import type { Status, WearableMetricRow, WearablesStatus } from "@/lib/types";
import type { DataSource, DayInputs, Goal, HealthspanWeek, PipelineEpisode } from "./types";

export const DEFAULT_API_BASE = "http://localhost:8010";
/** The seeded window is seven days ending today (fixtures.py `DAY_COUNT`). */
export const WINDOW_DAYS = 7;
/**
 * `/api/healthspan?days=7` scores eight engine runs server-side (today in full,
 * each trailing day lite); ~0.3 s on the seeded demo DB, more on a long run.
 */
const HEALTHSPAN_TIMEOUT_MS = 10_000;

export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? DEFAULT_API_BASE;
}

/** The backend's API_TOKEN (docs/DEPLOY.md), or "" when unset: auth off, nothing sent. */
export function apiToken(): string {
  return process.env.NEXT_PUBLIC_API_TOKEN ?? "";
}

/** Spread into a fetch init: `Authorization: Bearer` when a token is set, else nothing. */
export function authInit(): { headers?: Record<string, string> } {
  const token = apiToken();
  return token ? { headers: { authorization: `Bearer ${token}` } } : {};
}

/** For URLs that cannot carry a header (`<img src>`, download links): `?token=`. */
export function withToken(url: string): string {
  const token = apiToken();
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
}

/** Network error, timeout, non-2xx, or a body that is not JSON. */
export class BackendOffline extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "BackendOffline";
  }
}

const describe = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export async function fetchJson<T>(base: string, path: string, timeoutMs = 2500): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, { cache: "no-store", signal: AbortSignal.timeout(timeoutMs), ...authInit() });
  } catch (cause) {
    throw new BackendOffline(`${path}: ${describe(cause)}`, { cause });
  }
  if (!response.ok) throw new BackendOffline(`${path}: HTTP ${response.status}`);
  try {
    return (await response.json()) as T;
  } catch (cause) {
    throw new BackendOffline(`${path}: body is not JSON`, { cause });
  }
}

// ---------------------------------------------------------------------------
// Dates — local time, to match the backend's `day_key`
// ---------------------------------------------------------------------------

const pad2 = (n: number): string => String(n).padStart(2, "0");

/** `YYYY-MM-DD` of a unix-seconds timestamp in local time (backend `db.day_key`). */
export function localIsoDate(t: number): string {
  const d = new Date(t * 1000);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** The `n` ISO dates ending at `dateIso` inclusive, oldest first (fixtures.py `days_ending`). */
export function daysEnding(dateIso: string, n = WINDOW_DAYS): string[] {
  const [y, m, d] = dateIso.split("-").map(Number);
  // Day arithmetic through the Date constructor so month ends and DST are handled.
  return Array.from({ length: n }, (_, i) => localIsoDate(new Date(y, m - 1, d - (n - 1 - i)).getTime() / 1000));
}

// ---------------------------------------------------------------------------
// Response shapes (only the fields read here; everything is treated as optional)
// ---------------------------------------------------------------------------

interface SeededRow {
  source?: string;
  day: string;
  metric: string;
  value: number;
}

/** The routes return bare arrays today; older builds wrapped them (`{rows: []}`). */
function asList<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value !== null && typeof value === "object") {
    const inner = (value as Record<string, unknown>)[key];
    if (Array.isArray(inner)) return inner as T[];
  }
  return [];
}

const finite = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

/** Long rows `{day, metric, value}` → `date -> metric -> value`. */
/** `day -> metric -> source`: which stream wrote each row (the demo seed, or a device such as `fitbit`). */
export function pivotSeededSources(rows: SeededRow[]): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {};
  for (const r of rows) {
    if (typeof r.day !== "string" || typeof r.metric !== "string" || typeof r.source !== "string") continue;
    (out[r.day] ??= {})[r.metric] = r.source;
  }
  return out;
}

export function pivotSeeded(rows: SeededRow[]): Record<string, Record<string, number>> {
  const byDay: Record<string, Record<string, number>> = {};
  for (const row of rows) {
    const value = finite(row.value);
    if (typeof row.day !== "string" || typeof row.metric !== "string" || value === undefined) continue;
    (byDay[row.day] ??= {})[row.metric] = value;
  }
  return byDay;
}

// ---------------------------------------------------------------------------
// The score
// ---------------------------------------------------------------------------

/**
 * `GET /api/healthspan?day=<day>&days=7&goal=<goal>`: the backend's own score —
 * the same engine, with its coverage rule and per-factor provenance applied —
 * for `day` in full plus every day of the window's hours, in one round trip.
 * `day` is pinned rather than left to the backend's clock so the score and the
 * rows `loadDayInputs` read are the same day. Pins carry their saved evidence
 * frame as a server-relative `img` (healthspan.py `_frame_urls`).
 */
export async function fetchHealthspanWeek(base: string, day: string, goal: Goal): Promise<HealthspanWeek> {
  const query = new URLSearchParams({ day, days: String(WINDOW_DAYS), goal });
  const week = await fetchJson<Partial<HealthspanWeek>>(base, `/api/healthspan?${query.toString()}`, HEALTHSPAN_TIMEOUT_MS);
  if (week.today === undefined || week.today === null || !Array.isArray(week.days)) {
    throw new BackendOffline("/api/healthspan: no {days, today} in the response");
  }
  // The header names the person the score was computed for; a payload that does
  // not say who that was is not shown with a guessed age or sex instead.
  const profile: Partial<HealthspanWeek["today"]["profile"]> | undefined = week.today.profile;
  if (typeof profile?.age !== "number" || !Number.isFinite(profile.age) || typeof profile.sex !== "string") {
    throw new BackendOffline("/api/healthspan: no profile {age, sex} in the response");
  }
  return week as HealthspanWeek;
}

// ---------------------------------------------------------------------------
// The load
// ---------------------------------------------------------------------------

const byStart = (a: PipelineEpisode, b: PipelineEpisode): number => a.start_t - b.start_t;

/**
 * `DataSource` plus what `GET /api/wearables/status` reports about real devices.
 * Declared here rather than in types.ts (owned by the rings lane) — the three
 * fields below are additive and safe to widen `DataSource` with later.
 */
export interface LiveDataSource extends DataSource {
  /** Any live sample in the backend's freshness window (routes.py `LIVE_FRESH_S`). */
  live_connected: boolean;
  /** Device ids actually pushing, e.g. `["whoop"]` or `["fitbit"]`; empty when none are. */
  live_devices: string[];
  /** One row per stored metric: `{metric, source, origin, count, last_t}`. */
  live_metrics: WearableMetricRow[];
}

type WearableFacts = Pick<LiveDataSource, "live_connected" | "live_devices" | "live_metrics">;

/** `/api/wearables/status` is a decoration, not a gate: an absent one means "no device". */
function readWearables(raw: unknown): WearableFacts {
  if (raw === null || typeof raw !== "object") return { live_connected: false, live_devices: [], live_metrics: [] };
  const status = raw as Partial<WearablesStatus>;
  const devices = Array.isArray(status.live_devices) ? status.live_devices.filter((d): d is string => typeof d === "string") : [];
  return {
    live_connected: status.live_connected === true,
    live_devices: devices,
    live_metrics: asList<WearableMetricRow>(status.metrics, "metrics"),
  };
}

/**
 * Status, seeded rows and episodes are required — any of them failing throws
 * `BackendOffline`. The wearable status only decorates the page (the device
 * string), so it is best-effort.
 */
export async function loadDayInputs(base: string = apiBase()): Promise<{ days: DayInputs[]; source: LiveDataSource }> {
  const status = await fetchJson<Partial<Status>>(base, "/api/status");
  const nowT = finite(status.last_tick_t) ?? Date.now() / 1000;
  const today = localIsoDate(nowT);
  const dates = daysEnding(today, WINDOW_DAYS);
  const todayIndex = dates.length - 1;

  const [seededRaw, episodesRaw, wearablesRaw] = await Promise.all([
    fetchJson<unknown>(base, `/api/seeded?days=${WINDOW_DAYS}`),
    Promise.all(dates.map((date) => fetchJson<unknown>(base, `/api/episodes?day=${date}`))),
    fetchJson<unknown>(base, "/api/wearables/status").catch((): unknown => null),
  ]);
  const seeded = pivotSeeded(asList<SeededRow>(seededRaw, "rows"));
  const seededSources = pivotSeededSources(asList<SeededRow>(seededRaw, "rows"));
  const episodes = episodesRaw.map((raw) => asList<PipelineEpisode>(raw, "episodes").sort(byStart));

  const source: LiveDataSource = {
    mode: "live",
    api_base: base,
    day: today,
    tick_count: finite(status.tick_count),
    capture_source: typeof status.source === "string" ? status.source : undefined,
    demo_mode: typeof status.demo_mode === "boolean" ? status.demo_mode : undefined,
    last_tick_t: nowT,
    wearable_sources: seededSources[today] ?? {},
    ...readWearables(wearablesRaw),
  };
  const days: DayInputs[] = dates.map((date, i) => ({
    date,
    episodes: episodes[i],
    seeded: seeded[date] ?? {},
    isToday: i === todayIndex,
    nowT,
  }));
  return { days, source };
}
