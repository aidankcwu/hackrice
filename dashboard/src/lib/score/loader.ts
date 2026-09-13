/**
 * Server-only orchestration: backend fetch → adapter → engine → shape.
 * `loadDashboardData` is what the page and `GET /api/score` call.
 *
 * There is no offline stand-in. When the backend cannot be reached the load
 * rejects with `BackendOffline` and the page renders its offline state — a
 * fabricated day that looks like a real score is worse than no score (R1).
 */
import "server-only";
import { buildDayRequests } from "./adapter";
import { apiBase, loadDayInputs, type LiveDataSource } from "./backend";
import { runEngineBatch } from "./engine";
import { shapeDashboard } from "./shape";
import { GOALS, type DashboardData, type Goal, type Person } from "./types";

export interface LoadOptions {
  goal?: Goal;
  /** Overrides NEXT_PUBLIC_API_BASE. */
  apiBase?: string;
}

// ---------------------------------------------------------------------------
// Person / profile
// ---------------------------------------------------------------------------

/**
 * The wearer is Bryan (R2). Age and sex only reach the engine's
 * `remaining_life_years`, so they are a declared profile, not a measurement.
 */
const PERSON_DEFAULTS = { name: "Bryan", age: 20, sex: "M" } as const;

/** Shown when the glasses are the only thing actually reporting. */
const NO_WEARABLE_DEVICE = "Ray-Ban Meta · no wearable connected";

/** Anything that is not a known goal scores as "average" rather than failing the page. */
export function resolveGoal(value: unknown): Goal {
  return GOALS.find((g) => g.value === value)?.value ?? "average";
}

const goalLabel = (goal: Goal): string => GOALS.find((g) => g.value === goal)?.label ?? goal;

/** `BRYAN_*` is the current spelling; `BRIAN_*` is still read so existing envs keep working. */
const envValue = (env: NodeJS.ProcessEnv, suffix: string): string | undefined =>
  env[`BRYAN_${suffix}`] ?? env[`BRIAN_${suffix}`];

/**
 * The device string names the hardware that is actually reporting: the glasses
 * (the episodes they produce are what every pin is built from) plus whichever
 * wearables `/api/wearables/status` lists as live. Nothing is named on the
 * strength of being configured.
 */
function deviceLabel(source: LiveDataSource): string {
  const devices = [...new Set(source.live_devices)].filter((d) => d.length > 0).sort();
  return devices.length === 0 ? NO_WEARABLE_DEVICE : `Ray-Ban Meta + ${devices.join(" + ")}`;
}

function personFromEnv(goal: Goal, source: LiveDataSource): Omit<Person, "bedtime_hh"> {
  const env = process.env;
  const age = Number.parseInt(envValue(env, "PERSON_AGE") ?? "", 10);
  // Same rule as the engine's `remaining_life_years`: anything starting with F is female.
  const sex = (envValue(env, "PERSON_SEX") ?? PERSON_DEFAULTS.sex).trim().toUpperCase();
  return {
    name: envValue(env, "PERSON_NAME")?.trim() || PERSON_DEFAULTS.name,
    age: Number.isInteger(age) && age > 0 ? age : PERSON_DEFAULTS.age,
    sex: sex.startsWith("F") ? "F" : "M",
    goal,
    profileLabel: goalLabel(goal),
    device: envValue(env, "DEVICE")?.trim() || deviceLabel(source),
  };
}

// ---------------------------------------------------------------------------
// The build
// ---------------------------------------------------------------------------

async function build(goal: Goal, base: string): Promise<DashboardData> {
  // Throws `BackendOffline` when the pipeline is unreachable; nothing substitutes for it.
  const { days, source } = await loadDayInputs(base);
  const person = personFromEnv(goal, source);
  const requests = buildDayRequests(days, { age: person.age, sex: person.sex, goal });
  const started = performance.now();
  const payloads = await runEngineBatch(requests);
  const engineMs = Math.round(performance.now() - started);
  const bedtime_hh = requests[requests.length - 1]?.profile?.bedtime_hh ?? 23;
  return shapeDashboard({ payloads, days, person: { ...person, bedtime_hh }, source, engineMs });
}

// ---------------------------------------------------------------------------
// Cache — one engine run at a time per (goal, base)
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
  const base = opts.apiBase ?? apiBase();
  const key = `${goal}|${base}`;

  const hit = cache.get(key);
  if (hit !== undefined && (hit.settledAt === null || Date.now() - hit.settledAt < RESULT_TTL_MS)) return hit.promise;

  const entry: CacheEntry = { promise: build(goal, base), settledAt: null };
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
