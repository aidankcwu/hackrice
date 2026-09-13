/**
 * Adherence log: every lever the dashboard showed, and whether the wearer
 * did it within 24 h. The Beta-Bernoulli state `{lever_key: [alpha, beta]}`
 * goes into the score request as `adherence_state`; the engine's
 * `Adherence` class (Thompson sampling) does the ranking — nothing here
 * re-implements that.
 */
import { readJson, updateJson } from "./jsonStore";

export const WINDOW_H = 24;

export interface AdherenceLog {
  id: string;
  lever_key: string;
  /** ISO timestamp the lever was shown. */
  suggested_at: string;
  /** suggested_at + 24 h: undone by then means not done. */
  due_at: string;
  /** null while the window is open. */
  done: boolean | null;
  resolved_at?: string;
  /** What the glasses / WHOOP showed, when `done` came from evidence. */
  evidence?: string;
}

export interface AdherenceFile {
  logs: AdherenceLog[];
}

/** `{lever_key: [alpha, beta]}` — the engine's `Adherence.ab`. */
export type AdherenceState = Record<string, [number, number]>;

const STORE = "adherence";
const EMPTY: AdherenceFile = { logs: [] };

/** Beta(1 + done, 1 + not done) per lever, resolved logs only. */
export function stateOf(logs: AdherenceLog[]): AdherenceState {
  const state: AdherenceState = {};
  for (const log of logs) {
    if (log.done === null) continue;
    const [a, b] = state[log.lever_key] ?? [1, 1];
    state[log.lever_key] = log.done ? [a + 1, b] : [a, b + 1];
  }
  return state;
}

/** Beta mean: the engine's `Adherence.expected`. */
export function pAdherence(state: AdherenceState, key: string): number {
  const [a, b] = state[key] ?? [1, 1];
  return a / (a + b);
}

const plusHours = (iso: string, h: number): string => new Date(Date.parse(iso) + h * 3_600_000).toISOString();

/**
 * Log the levers shown at `at`. A lever with an open log inside the last
 * 24 h is not logged again — the same suggestion re-rendered by a poll is
 * one suggestion, not twenty.
 */
export async function logSuggestions(keys: string[], at = new Date()): Promise<{ logged: AdherenceLog[]; logs: AdherenceLog[] }> {
  const atIso = at.toISOString();
  const logged: AdherenceLog[] = [];
  const file = await updateJson<AdherenceFile>(STORE, EMPTY, (current) => {
    const logs = [...current.logs];
    for (const key of new Set(keys)) {
      const open = logs.some((l) => l.lever_key === key && l.done === null && Date.parse(l.due_at) > at.getTime());
      if (open) continue;
      const log: AdherenceLog = { id: `adh_${at.getTime().toString(36)}_${key}`, lever_key: key, suggested_at: atIso, due_at: plusHours(atIso, WINDOW_H), done: null };
      logs.push(log);
      logged.push(log);
    }
    return { logs };
  });
  return { logged, logs: file.logs };
}

/** Manually settle one log (a tap on "did it" / "skipped"). */
export async function markDone(id: string, done: boolean, at = new Date()): Promise<AdherenceLog | undefined> {
  let found: AdherenceLog | undefined;
  await updateJson<AdherenceFile>(STORE, EMPTY, (current) => ({
    logs: current.logs.map((l) => {
      if (l.id !== id) return l;
      found = { ...l, done, resolved_at: at.toISOString(), evidence: "entered" };
      return found;
    }),
  }));
  return found;
}

/** What the day's data showed, for the automatic `done` check. */
export interface DayEvidence {
  /** Local ISO date the evidence belongs to. */
  date: string;
  outdoor_min: number;
  /** A vigorous burst or a strain spike on the wearable. */
  vigorous: boolean;
  strength_min: number;
  /** Bedtime within 30 min of habit; null when no bedtime is known yet. */
  bed_in_window: boolean | null;
}

type Check = (e: DayEvidence) => boolean;

/** Lever key → what counts as done. Levers not listed can only be settled by hand or by the 24 h timeout. */
export const LEVER_DONE: Readonly<Record<string, Check>> = {
  bundle_walk: (e) => e.outdoor_min >= 20,
  day_light_min: (e) => e.outdoor_min >= 20,
  nature_min_wk: (e) => e.outdoor_min >= 20,
  vilpa_min: (e) => e.vigorous,
  resistance_min_wk: (e) => e.strength_min >= 20,
  sri: (e) => e.bed_in_window === true,
};

function describe(key: string, e: DayEvidence): string {
  switch (key) {
    case "bundle_walk":
    case "day_light_min":
    case "nature_min_wk":
      return `${Math.round(e.outdoor_min)} min outdoors on ${e.date}`;
    case "vilpa_min":
      return `vigorous burst on ${e.date}`;
    case "resistance_min_wk":
      return `${Math.round(e.strength_min)} min strength on ${e.date}`;
    case "sri":
      return `bedtime inside the window on ${e.date}`;
    default:
      return e.date;
  }
}

const pad2 = (n: number): string => String(n).padStart(2, "0");
const localDate = (ms: number): string => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
};

/**
 * Settle open logs from evidence: done when a day inside the log's 24 h
 * window shows the behaviour, not done once the window has closed without
 * it. Called at each scoring run.
 */
export async function autoResolve(evidence: DayEvidence[], now = new Date()): Promise<{ resolved: AdherenceLog[]; state: AdherenceState }> {
  const byDate = new Map(evidence.map((e) => [e.date, e] as const));
  const resolved: AdherenceLog[] = [];
  const file = await updateJson<AdherenceFile>(STORE, EMPTY, (current) => ({
    logs: current.logs.map((log) => {
      if (log.done !== null) return log;
      const check = LEVER_DONE[log.lever_key];
      const start = Date.parse(log.suggested_at);
      const due = Date.parse(log.due_at);
      if (check) {
        for (const date of [localDate(start), localDate(due)]) {
          const e = byDate.get(date);
          if (e && check(e)) {
            const done: AdherenceLog = { ...log, done: true, resolved_at: now.toISOString(), evidence: describe(log.lever_key, e) };
            resolved.push(done);
            return done;
          }
        }
      }
      if (now.getTime() > due) {
        const missed: AdherenceLog = { ...log, done: false, resolved_at: now.toISOString(), evidence: "no sign of it within 24 h" };
        resolved.push(missed);
        return missed;
      }
      return log;
    }),
  }));
  return { resolved, state: stateOf(file.logs) };
}

export async function adherenceFile(): Promise<AdherenceFile> {
  return readJson<AdherenceFile>(STORE, EMPTY);
}

export async function adherenceState(): Promise<AdherenceState> {
  return stateOf((await adherenceFile()).logs);
}
