/**
 * What Today shows, derived from the backend's JSON and nothing else. The phone
 * decides nothing about health: every number and word here is the backend's,
 * rounded, signed, or named per `brian-ios-design` and the IOS_SPEC vocabulary.
 */
import { API_BASE, ApiError, FIXTURES } from "./api";
import type { Decision, Episode, Healthspan, Reported, Session, Status } from "./types";

// ---------------------------------------------------------------------------
// Signed numbers
// ---------------------------------------------------------------------------

/** Rounded to 0.1 before anything else, half away from zero (Swift `Hours.rounded`); never −0. */
export function round1(value: number): number {
  const rounded = (Math.sign(value) * Math.round(Math.abs(value) * 10)) / 10;
  return rounded === 0 ? 0 : rounded;
}

export type Tone = "earn" | "cost" | "zero";

export interface Signed {
  /** "+1.4 h", "−1.1 h", "0.0 h": the sign and the word carry the meaning, colour repeats it. */
  text: string;
  tone: Tone;
  /** For VoiceOver: "plus 1.4 hours". */
  spoken: string;
}

export function signed(value: number, unit: string, spokenUnit = unit): Signed {
  const r = round1(value);
  const magnitude = Math.abs(r).toFixed(1);
  if (r > 0) return { text: `+${magnitude} ${unit}`, tone: "earn", spoken: `plus ${magnitude} ${spokenUnit}` };
  if (r < 0) return { text: `−${magnitude} ${unit}`, tone: "cost", spoken: `minus ${magnitude} ${spokenUnit}` };
  return { text: `${magnitude} ${unit}`, tone: "zero", spoken: `${magnitude} ${spokenUnit}` };
}

/** The word under the hero number (Swift `Hours.word`). */
export function hoursWord(hours: number): string {
  return round1(hours) < 0 ? "healthy life cost today" : "healthy life earned today";
}

// ---------------------------------------------------------------------------
// Provenance chip: Glasses · WHOOP · Health · Seeded
// ---------------------------------------------------------------------------

/** A live input's device, by the backend's `basis`, as the chip names it. */
const DEVICE: Record<string, "Glasses" | "WHOOP" | "Health"> = {
  glasses: "Glasses",
  whoop: "WHOOP",
  apple_watch: "Health",
  apple_health: "Health",
  healthkit: "Health",
  phone: "Health",
};
const DEVICE_ORDER = ["Glasses", "WHOOP", "Health"] as const;

/**
 * "Seeded" whenever fixtures mode is on. Otherwise from the payload: the live
 * sources present, then "Seeded" if any factor is seeded, e.g. "Glasses · WHOOP ·
 * Seeded". Empty (no chip) when neither.
 */
export function provenanceChip(healthspan: Healthspan): string {
  if (FIXTURES) return "Seeded";
  const live = new Set<string>();
  for (const entry of Object.values(healthspan.provenance ?? {})) {
    const device = entry.source === "live" ? DEVICE[entry.basis] : undefined;
    if (device) live.add(device);
  }
  const parts: string[] = DEVICE_ORDER.filter((device) => live.has(device));
  if ((healthspan.factors ?? []).some((factor) => factor.provenance === "seeded")) parts.push("Seeded");
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Status strip
// ---------------------------------------------------------------------------

/**
 * Frames are reaching the backend. From the glasses: the glasses app's socket is
 * open and a frame arrived in the last 10 s. From a stand-in source (`sim`,
 * `replay`, `webcam`): ticks are still coming.
 */
export function glassesConnected(status: Status): boolean {
  const problems = status.health?.problems ?? [];
  if (status.source === "glasses") {
    return Boolean(status.health?.phone?.connected) && !problems.includes("no_packets_10s");
  }
  return !problems.includes("no_ticks_60s");
}

/** "10.0.0.5" for `http://10.0.0.5:8010`: the host the phone is talking to. */
export function backendHost(): string {
  try {
    return new URL(API_BASE).hostname;
  } catch {
    return API_BASE;
  }
}

/** Whole minutes watched, by the backend's clock. */
export function watchedMinutes(session: Session, status: Status | null): number {
  const elapsed =
    session.elapsed_s ?? (status?.last_tick_t != null ? status.last_tick_t - session.started_t : 0);
  return Math.max(0, Math.floor(elapsed / 60));
}

/** One sentence of cause, one of fix. The button beside it is always "Try again". */
export function errorSentence(error: ApiError): string {
  if (error.status === null) return "Backend unreachable. Check that the Mac and phone share Wi‑Fi.";
  if (error.status === 401 || error.status === 403) return "Backend refused this phone. Check the API token.";
  return `Backend error ${error.status}. Restart the backend on the Mac.`;
}

export function toApiError(reason: unknown): ApiError {
  return reason instanceof ApiError ? reason : new ApiError("", null);
}

// ---------------------------------------------------------------------------
// Ledger
// ---------------------------------------------------------------------------

/** Trigger families (brian-ios-design "Icons"), one symbol each. */
export type Family =
  | "food"
  | "caffeine"
  | "alcohol"
  | "outdoor"
  | "screen"
  | "people"
  | "biometric"
  | "medication"
  | "winddown"
  | "walk";

const FAMILY_WORDS: readonly [Family, RegExp][] = [
  ["food", /meal|food/],
  ["caffeine", /caffeine|coffee/],
  ["alcohol", /alcohol/],
  ["outdoor", /outdoor|daylight|nature/],
  ["screen", /screen/],
  ["people", /social|people|person|conversation/],
  ["biometric", /heart|hrv|biometric/],
  ["medication", /medic|pill|dose/],
  ["winddown", /wind.?down|bedtime/],
  ["walk", /walk/],
];

/** The family of an episode `kind` or decision `trigger`; `null` when it names none. */
export function familyOf(...names: (string | null | undefined)[]): Family | null {
  for (const name of names) {
    const key = (name ?? "").toLowerCase();
    const hit = FAMILY_WORDS.find(([, words]) => words.test(key));
    if (hit) return hit[0];
  }
  return null;
}

/** Ledger outcomes, the IOS_SPEC vocabulary. */
export type Outcome = "whispered" | "asked" | "acted" | "held back";
const RANK: Record<Outcome, number> = { "held back": 0, whispered: 1, asked: 2, acted: 3 };

/** What one decision did for the wearer. Anything it did not say, ask or do was held back. */
export function decisionOutcome(decision: Decision): Outcome {
  if (decision.dropped) return "held back";
  const types = new Set(decision.actions.map((action) => action.type));
  if (types.has("act")) return "acted";
  if (!decision.spoke) return "held back";
  if (types.has("ask")) return "asked";
  if (types.has("speak")) return "whispered";
  return "held back";
}

export interface LedgerEntry {
  /** The episode id, or the newest decision's id when no listed episode carries it. */
  id: string;
  /** Epoch seconds: the episode's start, or the decision's time. */
  t: number;
  label: string;
  family: Family | null;
  outcome: Outcome;
  episode: Episode | null;
  /** Newest first. */
  decisions: Decision[];
}

const newestFirst = (a: Decision, b: Decision) => b.t - a.t;

function bestOutcome(decisions: Decision[]): Outcome {
  return decisions.reduce<Outcome>((best, d) => {
    const outcome = decisionOutcome(d);
    return RANK[outcome] > RANK[best] ? outcome : best;
  }, "held back");
}

/**
 * Episodes merged with decisions, newest first, one row per episode id. A decision
 * outside the listed episodes gets its own row only when the wearer heard or saw
 * something from it; the backend's silent re-checks (`change`, `watch:…`) are not
 * moments the wearer lived and stay out of the log.
 */
export function buildLedger(episodes: Episode[], decisions: Decision[]): LedgerEntry[] {
  const byEpisode = new Map<string, Decision[]>();
  const loose: Decision[] = [];
  for (const decision of decisions) {
    if (decision.episode_id) {
      const list = byEpisode.get(decision.episode_id) ?? [];
      list.push(decision);
      byEpisode.set(decision.episode_id, list);
    } else {
      loose.push(decision);
    }
  }

  const entries: LedgerEntry[] = episodes.map((episode) => {
    const own = (byEpisode.get(episode.id) ?? []).sort(newestFirst);
    byEpisode.delete(episode.id);
    return {
      id: episode.id,
      t: episode.start_t,
      label: episode.label || episode.kind.replace(/_/g, " "),
      family: familyOf(episode.kind, own[0]?.trigger),
      outcome: bestOutcome(own),
      episode,
      decisions: own,
    };
  });

  const groups = [...byEpisode.values(), ...loose.map((decision) => [decision])];
  for (const group of groups) {
    const own = group.sort(newestFirst);
    const outcome = bestOutcome(own);
    if (outcome === "held back") continue;
    entries.push({
      id: own[0].id,
      t: own[own.length - 1].t,
      label: own[0].interpretation,
      family: familyOf(own[0].trigger),
      outcome,
      episode: null,
      decisions: own,
    });
  }

  return entries.sort((a, b) => b.t - a.t);
}

/** The entry a detail URL names: by row id, episode id, or any of its decision ids. */
export function findEntry(entries: LedgerEntry[], id: string): LedgerEntry | null {
  return (
    entries.find(
      (entry) => entry.id === id || entry.episode?.id === id || entry.decisions.some((d) => d.id === id),
    ) ?? null
  );
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

const CLOCK = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });

/** "16:38", on the phone's clock: fits the 40 pt time column. */
export function clockTime(epochSeconds: number): string {
  return CLOCK.format(new Date(epochSeconds * 1000));
}

/** Sentence case for backend labels ("caffeine in frame" → "Caffeine in frame"). */
export function sentence(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** The wearer's answer in plain words: "yes, 2, rice bowl". Empty when nothing was parsed. */
export function reportedText(reported: Reported): string {
  const parts: string[] = [];
  if (reported.confirmed === true) parts.push("yes");
  if (reported.confirmed === false) parts.push("no");
  if (reported.count != null) parts.push(String(reported.count));
  if (reported.food_type) parts.push(reported.food_type.replace(/_/g, " "));
  if (reported.note) parts.push(reported.note);
  return parts.join(", ");
}
