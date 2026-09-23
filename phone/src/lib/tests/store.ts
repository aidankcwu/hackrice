"use client";

/**
 * Test results, kept on this phone only (`localStorage` key `brian.tests`):
 *
 *   { [testId]: { t: ISO date, score: number, extra: { [metric]: number } }[] }
 *
 * PVT-B carries twelve seeded days so the result screen has a median and a
 * strip from day one; seeded entries never touch storage, they are merged on
 * read and dropped for any day that has a real result. A browser that refuses
 * storage keeps this page's writes in memory instead.
 */
import { useMemo, useSyncExternalStore } from "react";
import { median, type RunResult, type TestId } from "./engine";

export const TESTS_KEY = "brian.tests";
/** Dispatched on this window after every write, so open hooks re-read. */
const CHANGED = "brian:tests";

export interface TestResult {
  /** ISO date-time of the run. */
  t: string;
  score: number;
  extra: Record<string, number>;
  /** Placeholder history, never written to storage. */
  seeded?: true;
}

export type TestStore = Partial<Record<TestId, TestResult[]>>;

const IDS: readonly TestId[] = ["pvt", "nback", "dsst", "stroop"];

/** This page's writes, for a browser that refuses storage (private window, blocked site data). */
let memoryRaw: string | null = null;

function readRaw(): string | null {
  try {
    const stored = window.localStorage.getItem(TESTS_KEY);
    if (stored !== null) return stored;
  } catch {
    // Storage refused: fall back to what this page wrote.
  }
  return memoryRaw;
}

function isResult(value: unknown): value is TestResult {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.t === "string" &&
    !Number.isNaN(new Date(entry.t).getTime()) &&
    typeof entry.score === "number" &&
    Number.isFinite(entry.score) &&
    typeof entry.extra === "object" &&
    entry.extra !== null
  );
}

/** Parses a stored value, keeping only well-formed entries under known test ids. */
export function parseStore(raw: string | null): TestStore {
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (typeof parsed !== "object" || parsed === null) return {};
  const store: TestStore = {};
  for (const id of IDS) {
    const list = (parsed as Record<string, unknown>)[id];
    if (!Array.isArray(list)) continue;
    store[id] = list.filter(isResult).map(({ t, score, extra }) => ({ t, score, extra }));
  }
  return store;
}

export function readStore(): TestStore {
  return parseStore(readRaw());
}

/** Appends one run and writes the store back. Returns the entry as stored. */
export function saveResult(id: TestId, run: RunResult, now: Date = new Date()): TestResult {
  const entry: TestResult = { t: now.toISOString(), score: run.score, extra: run.extra };
  const store = readStore();
  store[id] = [...(store[id] ?? []), entry];
  const raw = JSON.stringify(store);
  memoryRaw = raw;
  try {
    window.localStorage.setItem(TESTS_KEY, raw);
  } catch {
    // Storage refused: the result lasts for this page.
  }
  window.dispatchEvent(new Event(CHANGED));
  return entry;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGED, onChange);
  };
}

/**
 * The store as a hook. The server render and the first client render see an
 * empty store with `loaded` false, then the stored one, so hydration never
 * mismatches and nothing reads storage during render.
 */
export function useTestStore(): { store: TestStore; loaded: boolean } {
  const raw = useSyncExternalStore<string | null | undefined>(subscribe, readRaw, () => undefined);
  return useMemo(() => (raw === undefined ? { store: {}, loaded: false } : { store: parseStore(raw), loaded: true }), [raw]);
}

/* ---------------------------------------------------------------------------
   Calendar days.
   --------------------------------------------------------------------------- */
/** Local "YYYY-MM-DD". */
export function dayKey(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function daysAgo(now: Date, days: number): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - days);
}

/* ---------------------------------------------------------------------------
   Seeded history: PVT-B only, the twelve days before today.
   --------------------------------------------------------------------------- */
/** Plausible mean reaction times, yesterday first, all inside 285–330 ms. */
const SEEDED_PVT_RT = [298, 312, 305, 291, 322, 309, 315, 287, 301, 328, 296, 310];
const SEEDED_PVT_LAPSES = [2, 3, 2, 1, 4, 3, 3, 1, 2, 5, 2, 3];
const SEEDED_PVT_FALSE_STARTS = [0, 1, 0, 0, 1, 0, 2, 0, 0, 1, 0, 1];
const SEEDED_PVT_TRIALS = [30, 28, 29, 31, 27, 29, 28, 31, 30, 26, 30, 29];

export function seededHistory(id: TestId, now: Date = new Date()): TestResult[] {
  if (id !== "pvt") return [];
  return SEEDED_PVT_RT.map((meanRt, i) => {
    const day = daysAgo(now, i + 1);
    const at = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 10, 0, 0);
    return {
      t: at.toISOString(),
      score: meanRt,
      extra: { meanRt, lapses: SEEDED_PVT_LAPSES[i], falseStarts: SEEDED_PVT_FALSE_STARTS[i], trials: SEEDED_PVT_TRIALS[i] },
      seeded: true,
    };
  });
}

/* ---------------------------------------------------------------------------
   Reads. Each takes the store (default: read now) and "now" (default: now).
   --------------------------------------------------------------------------- */
/** Stored runs plus the seeded days that have no real run, oldest first. */
export function history(id: TestId, store: TestStore = readStore(), now: Date = new Date()): TestResult[] {
  const real = store[id] ?? [];
  const realDays = new Set(real.map((entry) => dayKey(new Date(entry.t))));
  const seeded = seededHistory(id, now).filter((entry) => !realDays.has(dayKey(new Date(entry.t))));
  return [...real, ...seeded].sort((a, b) => new Date(a.t).getTime() - new Date(b.t).getTime());
}

/** The runs from the last seven calendar days, today included. */
export function last7(id: TestId, store: TestStore = readStore(), now: Date = new Date()): TestResult[] {
  const window7 = new Set(Array.from({ length: 7 }, (_, i) => dayKey(daysAgo(now, i))));
  return history(id, store, now).filter((entry) => window7.has(dayKey(new Date(entry.t))));
}

/** The median of the last seven calendar days' scores; null with no runs. */
export function median7(id: TestId, store: TestStore = readStore(), now: Date = new Date()): number | null {
  return median(last7(id, store, now).map((entry) => entry.score));
}

/** True when a seeded day sits inside the median's window. */
export function restsOnSeeded(id: TestId, store: TestStore = readStore(), now: Date = new Date()): boolean {
  return last7(id, store, now).some((entry) => entry.seeded === true);
}

/** Thirty booleans, oldest first, the last one today: whether that day has a run. */
export function dayStrip30(id: TestId, store: TestStore = readStore(), now: Date = new Date()): boolean[] {
  const days = new Set(history(id, store, now).map((entry) => dayKey(new Date(entry.t))));
  return Array.from({ length: 30 }, (_, i) => days.has(dayKey(daysAgo(now, 29 - i))));
}
