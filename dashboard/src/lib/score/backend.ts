/**
 * Server-side fetch against the pipeline's FastAPI backend
 * (backend/pipeline/api/routes.py) → one `DayInputs` per calendar day.
 *
 * No React and no engine here: HTTP, a pivot of the long-format seeded rows,
 * and the evidence-frame lookup with its cache. Anything that fails as a whole
 * throws `BackendOffline` so the loader can fall back to the fixture.
 */
import type { Decision, Status } from "@/lib/types";
import type { DataSource, DayInputs, PipelineEpisode } from "./types";

export const DEFAULT_API_BASE = "http://localhost:8010";
/** The seeded window is seven days ending today (fixtures.py `DAY_COUNT`). */
export const WINDOW_DAYS = 7;
/** Newest decisions to scan when pairing today's episodes with evidence frames. */
const DECISION_LIMIT = 200;
/** Parallel `/api/evidence/{id}` lookups; the backend is one SQLite connection. */
const EVIDENCE_CONCURRENCY = 8;

export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? DEFAULT_API_BASE;
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
    response = await fetch(`${base}${path}`, { cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
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
  day: string;
  metric: string;
  value: number;
}

interface EvidenceRow {
  decision_id: string;
  frame_ref: string;
  t: number;
  bytes: number;
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
// Evidence frames
// ---------------------------------------------------------------------------

/**
 * decision id → frame URL, or null when no frame survived. The reasoner copies
 * frames at admission and inserts the decision row only after inference, so by
 * the time an id is listed its evidence is complete and never changes — a
 * settled answer is final. Fetch failures are deliberately not cached so the
 * next poll retries them.
 */
const evidenceCache = new Map<string, string | null>();

export function clearEvidenceCache(): void {
  evidenceCache.clear();
}

async function evidenceFrameUrl(base: string, decisionId: string): Promise<string | null> {
  const cached = evidenceCache.get(decisionId);
  if (cached !== undefined) return cached;
  const id = encodeURIComponent(decisionId);
  let rows: EvidenceRow[];
  try {
    rows = asList<EvidenceRow>(await fetchJson<unknown>(base, `/api/evidence/${id}`), "frames");
  } catch {
    return null;
  }
  // Rows come back ordered by `t` ascending, so the last one is the newest frame.
  const last = rows.length > 0 ? rows[rows.length - 1] : undefined;
  const url =
    last !== undefined && typeof last.frame_ref === "string"
      ? `${base}/api/evidence/${id}/${encodeURIComponent(last.frame_ref)}`
      : null;
  evidenceCache.set(decisionId, url);
  return url;
}

async function mapPool<T, R>(items: readonly T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/**
 * Episode id → evidence frame URL for the newest decision on that episode.
 * Dropped decisions (`t1_busy`) are skipped: they carry the episode id but
 * never copy frames, so counting them would blank a pin that has a frame.
 */
async function frameUrlsFor(
  base: string,
  episodes: PipelineEpisode[],
  decisions: Partial<Decision>[],
): Promise<Record<string, string>> {
  const wanted = new Set(episodes.map((e) => e.id));
  const newestDecision = new Map<string, string>();
  const ordered = [...decisions].sort((a, b) => (finite(b.t) ?? 0) - (finite(a.t) ?? 0));
  for (const d of ordered) {
    if (typeof d.id !== "string" || typeof d.episode_id !== "string" || d.dropped === true) continue;
    if (wanted.has(d.episode_id) && !newestDecision.has(d.episode_id)) newestDecision.set(d.episode_id, d.id);
  }
  const pairs = [...newestDecision.entries()];
  const urls = await mapPool(pairs, EVIDENCE_CONCURRENCY, ([, decisionId]) => evidenceFrameUrl(base, decisionId));
  const out: Record<string, string> = {};
  pairs.forEach(([episodeId], i) => {
    const url = urls[i];
    if (url !== null) out[episodeId] = url;
  });
  return out;
}

// ---------------------------------------------------------------------------
// The load
// ---------------------------------------------------------------------------

const byStart = (a: PipelineEpisode, b: PipelineEpisode): number => a.start_t - b.start_t;

/**
 * Status, seeded rows and episodes are required — any of them failing throws
 * `BackendOffline`. Decisions and evidence only decorate today's pins with
 * frames, so they are best-effort and never fail the load.
 */
export async function loadDayInputs(base: string = apiBase()): Promise<{ days: DayInputs[]; source: DataSource }> {
  const status = await fetchJson<Partial<Status>>(base, "/api/status");
  const nowT = finite(status.last_tick_t) ?? Date.now() / 1000;
  const today = localIsoDate(nowT);
  const dates = daysEnding(today, WINDOW_DAYS);
  const todayIndex = dates.length - 1;

  const [seededRaw, episodesRaw, decisionsRaw] = await Promise.all([
    fetchJson<unknown>(base, `/api/seeded?days=${WINDOW_DAYS}`),
    Promise.all(dates.map((date) => fetchJson<unknown>(base, `/api/episodes?day=${date}`))),
    fetchJson<unknown>(base, `/api/decisions?limit=${DECISION_LIMIT}`).catch((): unknown => []),
  ]);
  const seeded = pivotSeeded(asList<SeededRow>(seededRaw, "rows"));
  const episodes = episodesRaw.map((raw) => asList<PipelineEpisode>(raw, "episodes").sort(byStart));
  const frameUrls = await frameUrlsFor(base, episodes[todayIndex], asList<Partial<Decision>>(decisionsRaw, "decisions"));

  const source: DataSource = {
    mode: "live",
    api_base: base,
    day: today,
    tick_count: finite(status.tick_count),
    capture_source: typeof status.source === "string" ? status.source : undefined,
    demo_mode: typeof status.demo_mode === "boolean" ? status.demo_mode : undefined,
    last_tick_t: nowT,
  };
  const days: DayInputs[] = dates.map((date, i) => ({
    date,
    episodes: episodes[i],
    seeded: seeded[date] ?? {},
    frameUrls: i === todayIndex ? frameUrls : {},
    isToday: i === todayIndex,
    nowT,
  }));
  return { days, source };
}
