/**
 * The four cognitive tests as pure functions: timings, stimulus generation and
 * scoring. No DOM, no React, no storage; the screens under
 * `src/components/tests` drive these on `requestAnimationFrame`.
 */
import type { TestDef } from "@/content/types";

export type TestId = TestDef["id"];

/** One finished run: the score and the extra metrics under it, all numbers. */
export interface RunResult {
  score: number;
  extra: Record<string, number>;
}

/** How many decimals a test's score and median show. */
export const DECIMALS: Record<TestId, number> = { pvt: 0, nback: 2, dsst: 0, stroop: 0 };

export function formatScore(id: TestId, value: number): string {
  return value.toFixed(DECIMALS[id]);
}

/** "3 min" from 180, "90 s" from 90: whole minutes only from two minutes up. */
export function durationLabel(seconds: number): string {
  return seconds >= 120 && seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`;
}

/** "1:32" from 92 000 ms, rounding up so the line never reads 0:00 while the test still runs. */
export function clockLeft(ms: number): string {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

type Random = () => number;

function pick<T>(items: readonly T[], random: Random): T {
  return items[Math.floor(random() * items.length)];
}

export function shuffle<T>(items: readonly T[], random: Random = Math.random): T[] {
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/* ---------------------------------------------------------------------------
   PVT-B (Basner 2011, Acta Astronaut): 3 minutes; each trial waits 2–10 s,
   then a counter runs until the tap. RT over 355 ms is a lapse.
   --------------------------------------------------------------------------- */
export const PVT_MS = 180_000;
export const PVT_WAIT_MIN_MS = 2_000;
export const PVT_WAIT_MAX_MS = 10_000;
export const PVT_LAPSE_MS = 355;
/** "Too early" stays this long after a tap before the counter starts. */
export const PVT_EARLY_MS = 400;
/** The reaction time stays frozen on the screen this long. */
export const PVT_FEEDBACK_MS = 600;
/** A counter nobody stops ends here: a lapse with no reaction time. */
export const PVT_TIMEOUT_MS = 10_000;

export function pvtWait(random: Random = Math.random): number {
  return PVT_WAIT_MIN_MS + random() * (PVT_WAIT_MAX_MS - PVT_WAIT_MIN_MS);
}

/**
 * Mean RT is the score; lapses count RTs over 355 ms and counters that ran out.
 * A run with no tap at all scores the timeout, never 0 ms.
 */
export function pvtResult(rts: number[], falseStarts: number, timeouts: number): RunResult {
  const meanRt = rts.length ? Math.round(mean(rts)!) : PVT_TIMEOUT_MS;
  const lapses = rts.filter((rt) => rt > PVT_LAPSE_MS).length + timeouts;
  return { score: meanRt, extra: { meanRt, lapses, falseStarts, trials: rts.length } };
}

/* ---------------------------------------------------------------------------
   2-back: 2 minutes; a letter every 2.5 s, shown for 2 s; 30% of the scorable
   letters match the one two back. Score is d′.
   --------------------------------------------------------------------------- */
export const NBACK_MS = 120_000;
export const NBACK_ISI_MS = 2_500;
export const NBACK_ON_MS = 2_000;
export const NBACK_N = 2;
export const NBACK_TARGET_RATE = 0.3;
export const NBACK_COUNT = Math.floor(NBACK_MS / NBACK_ISI_MS);
export const NBACK_LETTERS: readonly string[] = "BCDFGHJKLMNPQRSTVWXZ".split("");

export type NbackAnswer = "match" | "nomatch" | null;

export interface NbackSequence {
  letters: string[];
  /** True where the letter matches the one two back; always false for the first two. */
  targets: boolean[];
}

export function nbackSequence(count = NBACK_COUNT, targetRate = NBACK_TARGET_RATE, random: Random = Math.random): NbackSequence {
  const scorable = Math.max(0, count - NBACK_N);
  const targetCount = Math.round(scorable * targetRate);
  const positions = shuffle(
    Array.from({ length: scorable }, (_, i) => i + NBACK_N),
    random,
  ).slice(0, targetCount);
  const targets: boolean[] = Array.from({ length: count }, () => false);
  for (const position of positions) targets[position] = true;

  const letters: string[] = [];
  for (let i = 0; i < count; i++) {
    if (targets[i]) {
      letters.push(letters[i - NBACK_N]);
      continue;
    }
    let letter = pick(NBACK_LETTERS, random);
    while (i >= NBACK_N && letter === letters[i - NBACK_N]) letter = pick(NBACK_LETTERS, random);
    letters.push(letter);
  }
  return { letters, targets };
}

/** The standard normal quantile (Acklam's rational approximation, error under 1.2e-9). */
export function zScore(p: number): number {
  if (p <= 0) return -Infinity;
  if (p >= 1) return Infinity;
  const a = [-3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2, 1.38357751867269e2, -3.066479806614716e1, 2.506628277459239];
  const b = [-5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2, 6.680131188771972e1, -1.328068155288572e1];
  const c = [-7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838, -2.549732539343734, 4.374664141464968, 2.938163982698783];
  const d = [7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996, 3.754408661907416];
  const low = 0.02425;
  if (p < low) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > 1 - low) {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  const q = p - 0.5;
  const r = q * q;
  return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/** A rate with the 0.5/N correction: never exactly 0 or 1, so z() stays finite. */
function correctedRate(count: number, n: number): number {
  if (n <= 0) return 0.5;
  if (count <= 0) return 0.5 / n;
  if (count >= n) return (n - 0.5) / n;
  return count / n;
}

/** d′ = z(hit rate) − z(false-alarm rate). */
export function dPrime(hits: number, misses: number, falseAlarms: number, correctRejections: number): number {
  return zScore(correctedRate(hits, hits + misses)) - zScore(correctedRate(falseAlarms, falseAlarms + correctRejections));
}

/** Only letters from the third on are scored; no answer counts as "no match". */
export function nbackResult(targets: boolean[], answers: NbackAnswer[]): RunResult {
  let hits = 0;
  let misses = 0;
  let falseAlarms = 0;
  let correctRejections = 0;
  for (let i = NBACK_N; i < targets.length; i++) {
    const said = answers[i] === "match";
    if (targets[i]) {
      if (said) hits++;
      else misses++;
    } else if (said) falseAlarms++;
    else correctRejections++;
  }
  const score = Math.round(dPrime(hits, misses, falseAlarms, correctRejections) * 100) / 100;
  return { score, extra: { hits, misses, falseAlarms, correctRejections } };
}

/* ---------------------------------------------------------------------------
   DSST: 90 s; nine symbols keyed to the digits 1–9, the key shuffled per run.
   Score is the correct count.
   --------------------------------------------------------------------------- */
export const DSST_MS = 90_000;
export const DSST_SYMBOLS = 9;

/** `key[digit − 1]` is the symbol index (0–8) that digit stands for. */
export function dsstKey(random: Random = Math.random): number[] {
  return shuffle(
    Array.from({ length: DSST_SYMBOLS }, (_, i) => i),
    random,
  );
}

/** The next symbol to show, never the one just shown. */
export function dsstNext(previous: number | null, random: Random = Math.random): number {
  let symbol = Math.floor(random() * DSST_SYMBOLS);
  while (symbol === previous) symbol = Math.floor(random() * DSST_SYMBOLS);
  return symbol;
}

export function dsstResult(correct: number, wrong: number): RunResult {
  return { score: correct, extra: { correct, wrong, trials: correct + wrong } };
}

/* ---------------------------------------------------------------------------
   Stroop: 60 s; a colour word in an ink that matches half the time. Score is
   the interference, mean RT incongruent − mean RT congruent, on correct taps.
   --------------------------------------------------------------------------- */
export const STROOP_MS = 60_000;
/** The blank between a tap and the next word. */
export const STROOP_GAP_MS = 200;
export const STROOP_COLOURS = ["red", "green", "blue", "yellow"] as const;
export type StroopColour = (typeof STROOP_COLOURS)[number];

export interface StroopTrial {
  word: StroopColour;
  ink: StroopColour;
  congruent: boolean;
}

export interface StroopTap {
  congruent: boolean;
  correct: boolean;
  rt: number;
}

export function stroopTrial(random: Random = Math.random): StroopTrial {
  const word = pick(STROOP_COLOURS, random);
  const congruent = random() < 0.5;
  const ink = congruent ? word : pick(STROOP_COLOURS.filter((colour) => colour !== word), random);
  return { word, ink, congruent };
}

export function stroopResult(taps: StroopTap[]): RunResult {
  const congruentRt = mean(taps.filter((tap) => tap.correct && tap.congruent).map((tap) => tap.rt));
  const incongruentRt = mean(taps.filter((tap) => tap.correct && !tap.congruent).map((tap) => tap.rt));
  const interference = congruentRt === null || incongruentRt === null ? 0 : Math.round(incongruentRt - congruentRt);
  const errors = taps.filter((tap) => !tap.correct).length;
  return {
    score: interference,
    extra: {
      congruentRt: Math.round(congruentRt ?? 0),
      incongruentRt: Math.round(incongruentRt ?? 0),
      errors,
      trials: taps.length,
    },
  };
}
