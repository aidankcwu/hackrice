/**
 * Server-only orchestration: backend fetch (or the fixture when offline)
 * → adapter → engine → shape. `loadDashboardData` is what the page and
 * `GET /api/score` call.
 */
import "server-only";
import { buildDayRequests } from "./adapter";
import { apiBase, loadDayInputs, localIsoDate } from "./backend";
import { runEngineBatch } from "./engine";
import { fixtureDays, fixtureSource } from "./fixture";
import { shapeDashboard } from "./shape";
import { GOALS, type DashboardData, type DataSource, type DayInputs, type Goal, type Person } from "./types";

export interface LoadOptions {
  goal?: Goal;
  /** Overrides NEXT_PUBLIC_API_BASE. */
  apiBase?: string;
  /** Force the fixture path (what `npm run dev:mock` does). */
  mock?: boolean;
}

// ---------------------------------------------------------------------------
// Person / profile
// ---------------------------------------------------------------------------

const PERSON_DEFAULTS = { name: "Asher", age: 20, sex: "M", device: "Ray-Ban Meta + WHOOP" } as const;

/** Anything that is not a known goal scores as "average" rather than failing the page. */
export function resolveGoal(value: unknown): Goal {
  return GOALS.find((g) => g.value === value)?.value ?? "average";
}

const goalLabel = (goal: Goal): string => GOALS.find((g) => g.value === goal)?.label ?? goal;

function personFromEnv(goal: Goal): Omit<Person, "bedtime_hh"> {
  const env = process.env;
  const age = Number.parseInt(env.BRIAN_PERSON_AGE ?? "", 10);
  // Same rule as the engine's `remaining_life_years`: anything starting with F is female.
  const sex = (env.BRIAN_PERSON_SEX ?? PERSON_DEFAULTS.sex).trim().toUpperCase();
  return {
    name: env.BRIAN_PERSON_NAME?.trim() || PERSON_DEFAULTS.name,
    age: Number.isInteger(age) && age > 0 ? age : PERSON_DEFAULTS.age,
    sex: sex.startsWith("F") ? "F" : "M",
    goal,
    profileLabel: goalLabel(goal),
    device: env.BRIAN_DEVICE?.trim() || PERSON_DEFAULTS.device,
  };
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

const OFFLINE_WARN_EVERY_MS = 60_000;
let lastOfflineWarnAt = 0;

/** Live inputs, or the fixture when `mock` is set or the backend cannot be reached. */
async function loadInputs(base: string, mock: boolean): Promise<{ days: DayInputs[]; source: DataSource }> {
  if (!mock) {
    try {
      return await loadDayInputs(base);
    } catch (e) {
      // Polled every few seconds, so one line a minute is enough to notice.
      const now = Date.now();
      if (now - lastOfflineWarnAt > OFFLINE_WARN_EVERY_MS) {
        lastOfflineWarnAt = now;
        console.warn(`[brian] backend at ${base} unavailable (${e instanceof Error ? e.message : String(e)}); using the fixture`);
      }
    }
  }
  const nowT = Date.now() / 1000;
  const today = localIsoDate(nowT);
  return { days: fixtureDays(today, nowT), source: fixtureSource(today, nowT) };
}

async function build(goal: Goal, mock: boolean, base: string): Promise<DashboardData> {
  const { days, source } = await loadInputs(base, mock);
  const person = personFromEnv(goal);
  const requests = buildDayRequests(days, { age: person.age, sex: person.sex, goal });
  const started = performance.now();
  const payloads = await runEngineBatch(requests);
  const engineMs = Math.round(performance.now() - started);
  const bedtime_hh = requests[requests.length - 1]?.profile?.bedtime_hh ?? 23;
  return shapeDashboard({ payloads, days, person: { ...person, bedtime_hh }, source, engineMs });
}

// ---------------------------------------------------------------------------
// Cache — one engine run at a time per (goal, mock, base)
// ---------------------------------------------------------------------------

const RESULT_TTL_MS = 2000;

interface CacheEntry {
  promise: Promise<DashboardData>;
  /** null while in flight; every caller shares the same promise until it settles. */
  settledAt: number | null;
}

const cache = new Map<string, CacheEntry>();

export function clearLoaderCache(): void {
  cache.clear();
}

// Not `async` on purpose: callers must receive the cached promise itself so
// concurrent polls share one engine run rather than one wrapper each.
export function loadDashboardData(opts: LoadOptions = {}): Promise<DashboardData> {
  const goal = resolveGoal(opts.goal);
  const mock = opts.mock ?? process.env.NEXT_PUBLIC_MOCK === "1";
  const base = opts.apiBase ?? apiBase();
  const key = `${goal}|${mock}|${base}`;

  const hit = cache.get(key);
  if (hit !== undefined && (hit.settledAt === null || Date.now() - hit.settledAt < RESULT_TTL_MS)) return hit.promise;

  const entry: CacheEntry = { promise: build(goal, mock, base), settledAt: null };
  cache.set(key, entry);
  entry.promise.then(
    () => {
      entry.settledAt = Date.now();
    },
    () => {
      // A failure is not a result worth serving for two seconds; let the next caller retry.
      if (cache.get(key) === entry) cache.delete(key);
    },
  );
  return entry.promise;
}
