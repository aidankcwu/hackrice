/**
 * Psychomotor vigilance test (PVT) runs and the within-person baseline.
 *
 * The maths here is bookkeeping, not scoring: the engine's `rt_z` factor
 * (Hagger-Johnson 2014) takes a z-score against the person's own baseline,
 * and this file is where that baseline lives. The first three completed
 * tests define `mu_rt` / `sd_rt`; from then on every test gets
 * `rt_z = (mean_rt − mu_rt) / max(sd_rt, 25 ms)`.
 */
import { readJson, updateJson } from "./jsonStore";

/** A reaction time above this is a lapse (work order; the 3-min PVT-B uses a lower cut, this is the conservative sleep-lab one). */
export const LAPSE_MS = 500;
/** Completed tests that define the baseline. */
export const BASELINE_RUNS = 3;
/** Floor on the baseline SD so three near-identical tests do not make a 10 ms drift look like 2 SD. */
export const SD_FLOOR_MS = 25;
/** A test shorter than this is not the validated 3-minute version and never enters the baseline. */
export const MIN_COMPLETE_S = 170;

export interface PvtTrial {
  /** Milliseconds from stimulus to tap; null for a false start or a timed-out stimulus. */
  rt_ms: number | null;
  false_start: boolean;
  /** No tap within the stimulus window; counted as a lapse. */
  timeout?: boolean;
}

export interface PvtCheck {
  energy: number;
  mood: number;
  clarity: number;
}

export interface PvtSummary {
  mean_rt: number | null;
  median_rt: number | null;
  lapses: number;
  false_starts: number;
  rt_sd: number | null;
  /** Valid (responded) trials behind the mean. */
  n_valid: number;
  n_trials: number;
}

export interface PvtRun extends PvtSummary {
  id: string;
  /** ISO timestamp of the test start, local offset kept. */
  timestamp: string;
  duration_s: number;
  completed: boolean;
  trials: PvtTrial[];
  check: PvtCheck | null;
}

export interface PvtBaseline {
  mu_rt: number;
  /** After the 25 ms floor. */
  sd_rt: number;
  /** Raw SD across the baseline tests, before the floor. */
  sd_raw: number;
  run_ids: string[];
}

export interface PvtInput {
  trials: PvtTrial[];
  check?: PvtCheck | null;
  timestamp?: string;
  duration_s: number;
}

export interface PvtRecordResult {
  run: PvtRun;
  baseline: PvtBaseline | null;
  /** Null until the baseline exists. */
  rt_z: number | null;
  /** mean_rt − mu_rt, ms; null until the baseline exists. */
  vs_baseline_ms: number | null;
  /** Completed tests stored so far, including this one. */
  completed_runs: number;
}

const STORE = "pvt";

const mean = (xs: number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** Sample standard deviation (n − 1); 0 for fewer than two values. */
function sampleSd(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((acc, x) => acc + (x - m) ** 2, 0) / (xs.length - 1));
}

const round1 = (x: number): number => Math.round(x * 10) / 10;

export function summarise(trials: PvtTrial[]): PvtSummary {
  const valid = trials.filter((t): t is PvtTrial & { rt_ms: number } => !t.false_start && t.rt_ms !== null && Number.isFinite(t.rt_ms));
  const rts = valid.map((t) => t.rt_ms);
  const timeouts = trials.filter((t) => t.timeout === true && !t.false_start).length;
  return {
    mean_rt: rts.length ? round1(mean(rts)) : null,
    median_rt: rts.length ? round1(median(rts)) : null,
    lapses: rts.filter((rt) => rt > LAPSE_MS).length + timeouts,
    false_starts: trials.filter((t) => t.false_start).length,
    rt_sd: rts.length ? round1(sampleSd(rts)) : null,
    n_valid: rts.length,
    n_trials: trials.length,
  };
}

const byTime = (a: PvtRun, b: PvtRun): number => a.timestamp.localeCompare(b.timestamp);

/** Completed runs with a mean, oldest first. */
export function completedRuns(runs: PvtRun[]): PvtRun[] {
  return runs.filter((r) => r.completed && r.mean_rt !== null).sort(byTime);
}

/** The first `BASELINE_RUNS` completed tests, or null while there are fewer. */
export function baselineOf(runs: PvtRun[]): PvtBaseline | null {
  const first = completedRuns(runs).slice(0, BASELINE_RUNS);
  if (first.length < BASELINE_RUNS) return null;
  const means = first.map((r) => r.mean_rt as number);
  const sdRaw = sampleSd(means);
  return { mu_rt: round1(mean(means)), sd_rt: Math.max(SD_FLOOR_MS, round1(sdRaw)), sd_raw: round1(sdRaw), run_ids: first.map((r) => r.id) };
}

export function rtZ(meanRt: number, baseline: PvtBaseline): number {
  return Math.round(((meanRt - baseline.mu_rt) / Math.max(SD_FLOOR_MS, baseline.sd_rt)) * 100) / 100;
}

const isScale = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= 5;

/** A 1–5 self-check, or null when any tap is missing or out of range. */
export function normaliseCheck(raw: unknown): PvtCheck | null {
  if (raw === null || typeof raw !== "object") return null;
  const { energy, mood, clarity } = raw as Record<string, unknown>;
  return isScale(energy) && isScale(mood) && isScale(clarity) ? { energy, mood, clarity } : null;
}

function normaliseTrial(raw: unknown): PvtTrial | null {
  if (raw === null || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const falseStart = r.false_start === true;
  const timeout = r.timeout === true;
  const rt = typeof r.rt_ms === "number" && Number.isFinite(r.rt_ms) && r.rt_ms >= 0 ? Math.round(r.rt_ms) : null;
  if (!falseStart && !timeout && rt === null) return null;
  return { rt_ms: falseStart || timeout ? null : rt, false_start: falseStart, ...(timeout ? { timeout: true } : {}) };
}

/** Body of `POST /api/pvt` → a validated input, or a reason it was rejected. */
export function parseInput(body: unknown): { ok: true; input: PvtInput } | { ok: false; error: string } {
  if (body === null || typeof body !== "object") return { ok: false, error: "body must be an object" };
  const b = body as Record<string, unknown>;
  if (!Array.isArray(b.trials)) return { ok: false, error: "trials must be an array" };
  const trials: PvtTrial[] = [];
  for (const raw of b.trials) {
    const t = normaliseTrial(raw);
    if (t === null) return { ok: false, error: "each trial needs rt_ms (ms) or false_start / timeout" };
    trials.push(t);
  }
  const duration = typeof b.duration_s === "number" && Number.isFinite(b.duration_s) ? b.duration_s : NaN;
  if (!(duration > 0)) return { ok: false, error: "duration_s must be a positive number" };
  const timestamp = typeof b.timestamp === "string" && !Number.isNaN(Date.parse(b.timestamp)) ? b.timestamp : undefined;
  return { ok: true, input: { trials, check: normaliseCheck(b.check), timestamp, duration_s: duration } };
}

function newId(at: Date): string {
  return `pvt_${at.toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}_${Math.random().toString(36).slice(2, 6)}`;
}

export function resultFor(run: PvtRun, runs: PvtRun[]): PvtRecordResult {
  const baseline = baselineOf(runs);
  const z = baseline !== null && run.mean_rt !== null ? rtZ(run.mean_rt, baseline) : null;
  return {
    run,
    baseline,
    rt_z: z,
    vs_baseline_ms: baseline !== null && run.mean_rt !== null ? round1(run.mean_rt - baseline.mu_rt) : null,
    completed_runs: completedRuns(runs).length,
  };
}

/** Store a run and return it with the baseline as it stands afterwards. */
export async function recordRun(input: PvtInput, now = new Date()): Promise<PvtRecordResult> {
  const summary = summarise(input.trials);
  const run: PvtRun = {
    id: newId(now),
    timestamp: input.timestamp ?? now.toISOString(),
    duration_s: Math.round(input.duration_s),
    completed: input.duration_s >= MIN_COMPLETE_S && summary.n_valid > 0,
    trials: input.trials,
    check: input.check ?? null,
    ...summary,
  };
  const runs = await updateJson<PvtRun[]>(STORE, [], (current) => [...current, run]);
  return resultFor(run, runs);
}

export async function allRuns(): Promise<PvtRun[]> {
  return readJson<PvtRun[]>(STORE, []);
}

export interface PvtForScoring {
  /** What the engine's `utility_today` and `rt_z` factor take; absent until the baseline exists. */
  pvt?: { rt_z: number; lapses: number };
  check?: PvtCheck;
  latest?: PvtRecordResult;
}

/**
 * The latest run on `dayIso` (local date), shaped for the score request.
 * `pvt` needs a baseline; `check` only needs the three taps.
 */
export function forScoring(runs: PvtRun[], dayIso: string): PvtForScoring {
  const today = runs.filter((r) => localDay(r.timestamp) === dayIso).sort(byTime);
  const latest = today[today.length - 1];
  if (latest === undefined) return {};
  const result = resultFor(latest, runs);
  const out: PvtForScoring = { latest };
  if (result.rt_z !== null) out.pvt = { rt_z: result.rt_z, lapses: latest.lapses };
  if (latest.check) out.check = latest.check;
  return out;
}

const pad2 = (n: number): string => String(n).padStart(2, "0");

/** ISO date of a timestamp in this process's local time (the pipeline's `day_key` convention). */
export function localDay(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}
