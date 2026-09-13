/**
 * Narrator: one plain sentence per engine annotation (`annotate_week`), with
 * a hard rule — a sentence may only contain numbers that exist in its
 * annotation JSON. An LLM may phrase it, from a prompt this module builds out
 * of that same JSON; if its output carries any other number, the template
 * sentence is used instead. Pure functions only; the LLM call lives in
 * `narrator-llm.ts`.
 */

export interface RunAnnotation {
  kind: "run";
  from: string;
  to: string;
  direction: 1 | -1;
  delta_hours: number;
  sleep_from: number | null;
  sleep_to: number | null;
  rec_from: number | null;
  rec_to: number | null;
  drivers: string[];
  evidence: string[];
}

export interface ContrastAnnotation {
  kind: "contrast";
  driver: string;
  n_with: number;
  n_without: number;
  hours_with: number;
  hours_without: number;
  sleep_with: number;
  sleep_without: number;
  rec_with: number;
  rec_without: number;
  evidence: string[];
}

export interface ExtremeAnnotation {
  kind: "extreme";
  worst_day: string;
  worst_hours: number;
  worst_drivers: string[];
  best_day: string;
  best_hours: number;
}

export type Annotation = RunAnnotation | ContrastAnnotation | ExtremeAnnotation;

export interface NarratedSentence {
  kind: Annotation["kind"];
  text: string;
  /** Whether an LLM phrased it or the template did. */
  origin: "llm" | "template";
  evidence: string[];
}

/** Engine driver keys → words. */
export const DRIVER_LABELS: Readonly<Record<string, string>> = {
  caffeine_late: "late caffeine",
  alcohol: "alcohol",
  night_screen: "night screens",
  late_bed: "late bedtime",
  no_daylight: "no daylight",
  isolated: "isolation",
};

export const driverLabel = (key: string): string => DRIVER_LABELS[key] ?? key.replace(/_/g, " ");

// Digits with optional thousands separators and decimals; a leading true minus
// or hyphen belongs to the number, an en dash between numbers ("20–48%") does not.
const NUMBER_RE = /[−-]?\d[\d,]*(?:\.\d+)?/g;

/** Every number in `text` as a non-negative magnitude (sign is a word choice, not a fact). */
export function numbersIn(text: string): number[] {
  const out: number[] = [];
  for (const m of text.match(NUMBER_RE) ?? []) {
    const n = Number(m.replace(/[−-]/, "").replace(/,/g, ""));
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

function collect(value: unknown, into: Set<number>): void {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return;
    const mag = Math.abs(value);
    into.add(mag);
    // "82" for 82.0 and "6.0" for 6 are the same fact in different dress.
    into.add(Math.round(mag * 10) / 10);
    into.add(Math.round(mag));
  } else if (typeof value === "string") {
    for (const n of numbersIn(value)) into.add(n);
  } else if (Array.isArray(value)) {
    for (const v of value) collect(v, into);
  } else if (value !== null && typeof value === "object") {
    for (const v of Object.values(value)) collect(v, into);
  }
}

/** Numbers a sentence about `annotation` may contain, plus any `extra` derived ones (a span length). */
export function allowedNumbers(annotation: Annotation, extra: number[] = []): Set<number> {
  const set = new Set<number>();
  collect(annotation, set);
  for (const n of extra) collect(n, set);
  return set;
}

export function sentenceIsSafe(sentence: string, annotation: Annotation, extra: number[] = []): boolean {
  const allowed = allowedNumbers(annotation, extra);
  return numbersIn(sentence).every((n) => allowed.has(n));
}

const signedH = (h: number): string => `${h < 0 ? "−" : h > 0 ? "+" : ""}${Math.abs(h).toFixed(1)} h`;
const num = (n: number): string => (Number.isInteger(n) ? String(n) : n.toFixed(1));

export interface NarratorContext {
  /** Day labels of the week in order ("Sun" … "Sat"), used to count the days in a run. */
  days?: string[];
}

/** Days a run spans, from the week order; undefined when the days are not known. */
export function runLength(a: RunAnnotation, days: string[] | undefined): number | undefined {
  if (!days) return undefined;
  const i = days.indexOf(a.from);
  const j = days.indexOf(a.to);
  return i >= 0 && j > i ? j - i + 1 : undefined;
}

export function templateSentence(a: Annotation, ctx: NarratorContext = {}): string {
  switch (a.kind) {
    case "run": {
      const n = runLength(a, ctx.days);
      const span = n === undefined ? "" : ` over ${n} days`;
      const thread = a.drivers.length > 0 ? a.drivers.map(driverLabel).join(", ") : "nothing the glasses saw on every day";
      return `${a.from}→${a.to}: ${signedH(a.delta_hours)}${span}. Common thread: ${thread}.`;
    }
    case "contrast":
      return (
        `${a.n_with} ${driverLabel(a.driver)} days averaged ${num(a.sleep_with)} h of sleep and recovery ${num(a.rec_with)}; ` +
        `the other ${a.n_without} averaged ${num(a.sleep_without)} h and ${num(a.rec_without)}.`
      );
    case "extreme": {
      const because = a.worst_drivers.length > 0 ? ` (${a.worst_drivers.map(driverLabel).join(", ")})` : "";
      return `${a.worst_day} was the worst day at ${signedH(a.worst_hours)}${because}; ${a.best_day} the best at ${signedH(a.best_hours)}.`;
    }
  }
}

export function evidenceOf(a: Annotation): string[] {
  return "evidence" in a ? a.evidence.filter((e) => e.length > 0) : [];
}

/** Extra numbers a sentence may legitimately use beyond the JSON (the span length of a run). */
export function extraNumbers(a: Annotation, ctx: NarratorContext): number[] {
  if (a.kind !== "run") return [];
  const n = runLength(a, ctx.days);
  return n === undefined ? [] : [n];
}

const isKind = (v: unknown): v is Annotation["kind"] => v === "run" || v === "contrast" || v === "extreme";

/** Loose runtime check on `annotations[]` from the engine payload. */
export function isAnnotation(v: unknown): v is Annotation {
  return v !== null && typeof v === "object" && isKind((v as { kind?: unknown }).kind);
}

// ---------------------------------------------------------------------------
// Annotations from the week rows (the engine's `annotate_week`, in TypeScript)
// ---------------------------------------------------------------------------

/**
 * One week row in the shape `annotate_week` reads: `{day, hours, sleep, rec,
 * drivers}`. `drivers` holds only the flags the caller can actually prove; an
 * absent flag is absent, never false-by-assumption, because a contrast between
 * "the days with X" and "the days without X" is wrong the moment a day with no
 * evidence is counted as a clean one (product rule R1).
 */
export interface WeekRow {
  day: string;
  hours: number;
  sleep: number | null;
  rec: number | null;
  drivers: Readonly<Partial<Record<string, boolean>>>;
}

/** Driver keys `annotate_week` scans, in its own order. */
export const DRIVER_KEYS: readonly string[] = [
  "caffeine_late",
  "alcohol",
  "night_screen",
  "late_bed",
  "no_daylight",
  "isolated",
];

/** The engine's `_DRIVER_EVIDENCE` citations, so a derived annotation cites what the engine cites. */
const DRIVER_EVIDENCE: Readonly<Record<string, string>> = {
  caffeine_late: "Drake 2013",
  alcohol: "Zhao 2023",
  night_screen: "Brown 2022",
  late_bed: "Windred 2024",
  no_daylight: "Windred 2024",
  isolated: "Holt-Lunstad 2010",
};

const mean1 = (rows: readonly WeekRow[], pick: (r: WeekRow) => number | null): number =>
  Math.round((rows.reduce((sum, r) => sum + (pick(r) ?? 0), 0) / rows.length) * 10) / 10;

/** `_common_drivers`: a driver flagged on all but one day of the span. */
function commonDrivers(span: readonly WeekRow[]): string[] {
  const need = Math.max(2, span.length - 1);
  return DRIVER_KEYS.filter((k) => span.filter((r) => r.drivers[k] === true).length >= need);
}

/**
 * `annotate_week` (brian_score.py) over rows the UI already has: the runs, the
 * contrasts, and the worst/best extreme. Used only when the payload carries no
 * `annotations[]` of its own — the engine's output is always preferred, because
 * it sees drivers (night screens, late bed, no daylight, isolation) these rows
 * cannot prove. Oldest first, like the engine.
 */
export function annotationsFromWeek(rows: readonly WeekRow[], minLen = 2): Annotation[] {
  const out: Annotation[] = [];
  let i = 0;
  while (i < rows.length - 1) {
    const sign: 1 | -1 = rows[i + 1].hours > rows[i].hours ? 1 : -1;
    let j = i;
    while (j < rows.length - 1 && (rows[j + 1].hours - rows[j].hours) * sign > 0) j += 1;
    if (j - i >= minLen) {
      const span = rows.slice(i, j + 1);
      const drivers = commonDrivers(span);
      out.push({
        kind: "run",
        from: rows[i].day,
        to: rows[j].day,
        direction: sign,
        delta_hours: Math.round((rows[j].hours - rows[i].hours) * 10) / 10,
        sleep_from: rows[i].sleep,
        sleep_to: rows[j].sleep,
        rec_from: rows[i].rec,
        rec_to: rows[j].rec,
        drivers,
        evidence: drivers.flatMap((d) => (DRIVER_EVIDENCE[d] ? [DRIVER_EVIDENCE[d]] : [])),
      });
    }
    i = Math.max(j, i + 1);
  }
  for (const key of DRIVER_KEYS) {
    const a = rows.filter((r) => r.drivers[key] === true);
    const b = rows.filter((r) => r.drivers[key] !== true);
    if (a.length < 2 || b.length < 2) continue;
    out.push({
      kind: "contrast",
      driver: key,
      n_with: a.length,
      n_without: b.length,
      hours_with: mean1(a, (r) => r.hours),
      hours_without: mean1(b, (r) => r.hours),
      sleep_with: mean1(a, (r) => r.sleep),
      sleep_without: mean1(b, (r) => r.sleep),
      rec_with: mean1(a, (r) => r.rec),
      rec_without: mean1(b, (r) => r.rec),
      evidence: DRIVER_EVIDENCE[key] ? [DRIVER_EVIDENCE[key]] : [],
    });
  }
  if (rows.length > 0) {
    const worst = rows.reduce((acc, r) => (r.hours < acc.hours ? r : acc));
    const best = rows.reduce((acc, r) => (r.hours > acc.hours ? r : acc));
    out.push({
      kind: "extreme",
      worst_day: worst.day,
      worst_hours: worst.hours,
      worst_drivers: DRIVER_KEYS.filter((k) => worst.drivers[k] === true),
      best_day: best.day,
      best_hours: best.hours,
    });
  }
  return out;
}

export type LlmSentence = (prompt: string) => Promise<string | null>;

/**
 * The prompt for one annotation, built here from the annotation's own JSON.
 *
 * It is deliberately not taken from the caller: a caller-supplied prompt can
 * carry facts that are nowhere in the payload, and the model would repeat them
 * as if measured. The number guard catches invented *numbers*, never invented
 * words, so the facts in the prompt are the facts in the annotation and the
 * template sentence is handed over as the thing to rephrase.
 */
export function narratorPrompt(a: Annotation, ctx: NarratorContext = {}): string {
  return [
    "Rewrite this one sentence about a week of health data so it reads plainly.",
    "Rules: one sentence, at most 30 words. Use only the facts and numbers given —",
    "introduce no number, date, metric or cause that is not already here. No advice,",
    "no diagnosis, no second sentence, no quotation marks.",
    "",
    `Facts (JSON): ${JSON.stringify(a)}`,
    `Sentence: ${templateSentence(a, ctx)}`,
  ].join("\n");
}

/**
 * One sentence per annotation. `llm` (optional) rephrases the template from a
 * prompt built out of the annotation itself; its output is kept only when every
 * number in it is in the annotation, otherwise the template stands.
 */
export async function narrate(annotations: Annotation[], ctx: NarratorContext = {}, llm?: LlmSentence): Promise<NarratedSentence[]> {
  return Promise.all(
    annotations.map(async (a) => {
      const extra = extraNumbers(a, ctx);
      if (llm) {
        const text = clean(await llm(narratorPrompt(a, ctx)).catch(() => null));
        if (text && sentenceIsSafe(text, a, extra)) return { kind: a.kind, text, origin: "llm", evidence: evidenceOf(a) };
      }
      return { kind: a.kind, text: templateSentence(a, ctx), origin: "template", evidence: evidenceOf(a) };
    }),
  );
}

/** First non-empty line, quotes and whitespace trimmed; null when nothing usable came back. */
export function clean(text: string | null): string | null {
  if (!text) return null;
  const line = text
    .split(/\r?\n/)
    .map((l) => l.trim().replace(/^["'“”]+|["'“”]+$/g, "").trim())
    .find((l) => l.length > 0);
  return line && line.length <= 240 ? line : null;
}
