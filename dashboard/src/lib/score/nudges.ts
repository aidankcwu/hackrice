/**
 * Nudges — the ear log behind "What Bryan said" (screens.md §1.3,
 * references/nudges.md).
 *
 * Pure functions. Every field returned is read off a `Decision` row or a
 * `PinRow` the server payload already carries; nothing here invents a time, a
 * number, a reason or an outcome (product rule R1). Where a fact cannot be
 * derived the field is null and the panel prints the honest string instead.
 *
 * Two things this module deliberately will not do:
 *
 *  - It never guesses a held-back reason. The pipeline's speech limiter
 *    suppresses an utterance without writing a per-decision reason onto the
 *    row (backend/pipeline/actions/handlers.py:131 and actions/speech.py:81
 *    only log it), so the six exact reasons in nudges.md are derived from what
 *    the row *does* carry — the actions on it, the clock, and the spoken rows
 *    around it — and `nothing worth saying` is the honest default for a
 *    decision that simply chose not to speak.
 *  - It never reports `Did it`. Only a rule from the bottom of nudges.md that
 *    the payload can actually evaluate produces an outcome; everything else is
 *    `Pending`.
 */
import type { Decision, DecisionAction } from "@/lib/types";
import type { PinRow } from "./types";
import { minutesOfHhmm, minutesOfInstant, spokenText } from "./narrative";

// ---------------------------------------------------------------------------
// Categories
// ---------------------------------------------------------------------------

/** The nudges.md category table, in its own order. */
export type NudgeCategory =
  | "Fuel"
  | "Caffeine"
  | "Alcohol"
  | "Nicotine"
  | "Light"
  | "Screens"
  | "Body"
  | "People"
  | "Outside"
  | "Noise"
  | "Space"
  | "Sleep"
  | "Mind"
  | "Recovery"
  | "Work"
  | "Stress";

/**
 * Gate trigger name → category. The names are the pipeline's own
 * (backend/pipeline/gate/triggers.py `specs`), plus the `watch:` re-entry the
 * reasoner files when a pending check fires.
 */
const TRIGGER_CATEGORIES: Readonly<Record<string, NudgeCategory>> = {
  food_in_frame: "Fuel",
  caffeine_seen: "Caffeine",
  alcohol_seen: "Alcohol",
  screen_sustained: "Screens",
  people_sustained: "People",
  outdoor_sustained: "Outside",
  stillness: "Body",
  biometric_anomaly: "Stress",
  gym_session: "Recovery",
};

/**
 * `log_insight.category` → category. The reasoner writes these as free text
 * (reasoner/schema.py defaults to "general"), so only the spellings it is
 * actually prompted for are mapped; anything else leaves the category unknown
 * rather than being filed under a guess.
 */
const INSIGHT_CATEGORIES: Readonly<Record<string, NudgeCategory>> = {
  diet: "Fuel",
  food: "Fuel",
  fuel: "Fuel",
  caffeine: "Caffeine",
  alcohol: "Alcohol",
  nicotine: "Nicotine",
  smoking: "Nicotine",
  light: "Light",
  screen: "Screens",
  screens: "Screens",
  movement: "Body",
  body: "Body",
  social: "People",
  people: "People",
  nature: "Outside",
  outside: "Outside",
  noise: "Noise",
  space: "Space",
  sleep: "Sleep",
  mind: "Mind",
  recovery: "Recovery",
  work: "Work",
  stress: "Stress",
};

/** Grade C never speaks (nudges.md global rule 4) — it only annotates. */
const GRADE_C: ReadonlySet<NudgeCategory> = new Set<NudgeCategory>(["Space"]);

/** The first `log_insight` category the decision filed, lowercased; null when it filed none. */
function insightCategory(actions: readonly DecisionAction[]): NudgeCategory | null {
  for (const a of actions) {
    if (a.type !== "log_insight") continue;
    const mapped = INSIGHT_CATEGORIES[a.category.trim().toLowerCase()];
    if (mapped) return mapped;
  }
  return null;
}

/**
 * The category a decision belongs to: its trigger first (the gate named the
 * thing the camera saw), then the insight it filed. A keyword trigger the
 * operator added at runtime matches neither, and gets null — the row still
 * renders, it just carries no category-specific rule.
 */
export function categoryOf(d: Decision): NudgeCategory | null {
  const trigger = d.trigger.startsWith("watch:") ? d.trigger.slice("watch:".length) : d.trigger;
  return TRIGGER_CATEGORIES[trigger] ?? insightCategory(d.actions);
}

// ---------------------------------------------------------------------------
// Held-back reasons
// ---------------------------------------------------------------------------

/** The six exact strings from nudges.md. No others may be shown. */
export const HELD_BACK_REASONS = [
  "nothing worth saying",
  "you were mid-conversation",
  "quiet hours",
  "spoke in the last hour",
  "you did it before I asked",
  "not enough evidence to speak",
] as const;

export type HeldBackReason = (typeof HELD_BACK_REASONS)[number];

/** Default quiet hours (nudges.md global rule 2): 22:30 to 07:00. */
export const QUIET_FROM_MIN = 22 * 60 + 30;
export const QUIET_TO_MIN = 7 * 60;

/** One spoken line per 60 minutes (nudges.md global rule 2). */
export const SPEECH_GAP_MIN = 60;

export const inQuietHours = (minuteOfDay: number): boolean =>
  minuteOfDay >= QUIET_FROM_MIN || minuteOfDay < QUIET_TO_MIN;

const hasSpeakAction = (actions: readonly DecisionAction[]): boolean => actions.some((a) => a.type === "speak");
const hasAskAction = (actions: readonly DecisionAction[]): boolean => actions.some((a) => a.type === "ask");

/**
 * Why a decision did not speak, in the exact words nudges.md allows.
 *
 * The derivation, in the order it is tried — each branch reads a fact the row
 * carries, never a reason the backend did not record:
 *
 *  1. No `speak` action at all → `nothing worth saying`. The reasoner stopped
 *     at `annotate` / `log_insight` / `watch` / `nothing`, which is the
 *     silence-by-default ladder working as designed (global rule 1). This is
 *     the honest default the work order asks for.
 *  2. No speak action *and* the category is grade C (Space) →
 *     `not enough evidence to speak`, because grade C is barred from speech by
 *     rule 4 rather than by having nothing to report.
 *  3. A speak action that did not reach the ear, alongside an `ask` on the same
 *     decision → `you were mid-conversation`. handlers.py drops the utterance
 *     for the question (`speak_dropped_for_ask`) and again while the mic is
 *     listening for the answer, so a question on the row is the observable form
 *     of "Bryan was already talking with you".
 *  4. A speak action that did not reach the ear, inside quiet hours →
 *     `quiet hours`.
 *  5. A speak action that did not reach the ear with a spoken line in the
 *     previous 60 minutes → `spoke in the last hour` (the limiter's min gap).
 *  6. Anything else → null. The limiter's hourly cap and its suppression log
 *     leave nothing on the row to read, and `you did it before I asked` needs
 *     an outcome the decision cannot see, so no reason is claimed.
 *
 * `spokenBefore` is the minute-of-day of every decision that actually spoke,
 * so the 60-minute gap is measured against utterances, not proposals.
 */
export function heldBackReason(
  d: Decision,
  minuteOfDay: number,
  spokenBefore: readonly number[],
  category: NudgeCategory | null,
): HeldBackReason | null {
  if (!hasSpeakAction(d.actions)) {
    if (category !== null && GRADE_C.has(category)) return "not enough evidence to speak";
    return "nothing worth saying";
  }
  if (hasAskAction(d.actions)) return "you were mid-conversation";
  if (inQuietHours(minuteOfDay)) return "quiet hours";
  if (spokenBefore.some((m) => minuteOfDay - m >= 0 && minuteOfDay - m < SPEECH_GAP_MIN)) return "spoke in the last hour";
  return null;
}

// ---------------------------------------------------------------------------
// Outcome detection (nudges.md, bottom section)
// ---------------------------------------------------------------------------

export type Outcome = "did" | "didnt" | "pending";

/** The chip label for an outcome — the exact strings from screens.md §1.3. */
export const OUTCOME_LABELS: Readonly<Record<Outcome, string>> = {
  did: "Did it",
  didnt: "Didn't",
  pending: "Pending",
};

/** Minutes each rule allows (nudges.md "Outcome detection"). */
const WINDOWS: Readonly<Partial<Record<NudgeCategory, number>>> = {
  Fuel: 180,
  Caffeine: 180,
  Alcohol: 180,
  Nicotine: 180,
  Light: 90,
  Outside: 90,
  Body: 90,
  People: 180,
};

/**
 * Pin icons that stand in for the thing a rule looks for. `PinRow.icon` is
 * derived from the engine's own `seen` prefix (shape.ts `PIN_ICONS`), so it is
 * the one type tag a pin reliably carries.
 */
const SIGHTING_ICONS: Readonly<Partial<Record<NudgeCategory, readonly PinRow["icon"][]>>> = {
  Fuel: ["utensils"],
  Caffeine: ["coffee"],
  Alcohol: ["wine"],
};

/** What "Did it" looks like for the rules that want an action, not an absence. */
const ACTION_ICONS: Readonly<Partial<Record<NudgeCategory, readonly PinRow["icon"][]>>> = {
  Light: ["sun"],
  Outside: ["sun", "trees"],
  Body: ["footprints", "dumbbell"],
  People: ["users"],
};

/** Minutes a conversation pin states, from the engine's `Conversation, 41 min`; null when it states none. */
export function conversationMinutes(seen: string): number | null {
  const m = /(\d+(?:\.\d+)?)\s*min/.exec(seen);
  if (!m) return null;
  const value = Number(m[1]);
  return Number.isFinite(value) ? value : null;
}

/** The People rule's threshold: a conversation of at least 10 minutes. */
export const CONVERSATION_MIN = 10;

/** Pins whose stamp falls in `(at, at + window]`, nearest first. */
function pinsAfter(pins: readonly PinRow[], at: number, window: number): PinRow[] {
  const out: Array<{ pin: PinRow; gap: number }> = [];
  for (const pin of pins) {
    const mins = minutesOfHhmm(pin.time);
    if (mins === null) continue;
    const gap = mins - at;
    if (gap > 0 && gap <= window) out.push({ pin, gap });
  }
  return out.sort((a, b) => a.gap - b.gap).map((x) => x.pin);
}

/**
 * The 24-hour outcome check (nudges.md global rule 7), for the rules the
 * payload can evaluate:
 *
 *  - Fuel / Caffeine / Alcohol / Nicotine — no further sighting of that thing
 *    within 3 h is `Did it`; another sighting is `Didn't`.
 *  - Light / Outside / Body — an outdoor, nature or walking pin within 90 min
 *    is `Did it`. Its absence is not `Didn't`: the glasses only pin what they
 *    saw, so a missing pin is missing evidence, not a refusal.
 *  - People — a conversation pin of 10 min or more within 3 h is `Did it`.
 *
 * Screens, Sleep, Noise and Stress need a frame leaving, a bedtime, a dB trace
 * or a heart rate the pins do not carry, so they stay `Pending`. Everything
 * un-evaluable is `Pending`, and `Did it` is never assumed.
 *
 * `now` is the minute of day the day was scored at; a window that has not
 * elapsed yet is `Pending` rather than a premature verdict.
 */
export function outcomeOf(
  category: NudgeCategory | null,
  minuteOfDay: number,
  pins: readonly PinRow[],
  now: number,
): Outcome {
  if (category === null) return "pending";
  const window = WINDOWS[category];
  if (window === undefined) return "pending";
  const later = pinsAfter(pins, minuteOfDay, window);

  const sightings = SIGHTING_ICONS[category];
  if (sightings) {
    if (later.some((p) => sightings.includes(p.icon))) return "didnt";
    // The absence only counts once the 3 h have actually passed.
    return now - minuteOfDay >= window ? "did" : "pending";
  }

  const actions = ACTION_ICONS[category];
  if (actions) {
    const hit = later.filter((p) => actions.includes(p.icon));
    if (category === "People") {
      return hit.some((p) => {
        const mins = conversationMinutes(p.seen);
        return mins !== null && mins >= CONVERSATION_MIN;
      })
        ? "did"
        : "pending";
    }
    return hit.length > 0 ? "did" : "pending";
  }
  return "pending";
}

// ---------------------------------------------------------------------------
// The nudge object
// ---------------------------------------------------------------------------

/**
 * One line Bryan said, or held back. The shape is the one nudges.md names:
 * `{time, category, trigger, frame, said, evidence, escalation, outcome}`.
 */
export interface Nudge {
  id: string;
  /** `HH:MM` on the viewer's clock. */
  time: string;
  /** Minutes past local midnight — the ordering key and every rule's input. */
  minuteOfDay: number;
  category: NudgeCategory | null;
  /** The gate trigger that fired, verbatim. */
  trigger: string;
  /** The frame the glasses kept for this minute, or null when none survived. */
  frame: string | null;
  /** What the sighting was, from the linked pin; null when nothing pinned. */
  seen: string | null;
  /** The sentence that reached the ear, or the one that was proposed and held. */
  said: string | null;
  /** The citation, ≤ 5 words, from the pin's grade line or the engine. Null when none. */
  evidence: string | null;
  /** The ladder step this decision reached: nudges.md global rule 1. */
  escalation: "annotate" | "dashboard" | "speak";
  /** Spoken out the glasses. */
  spoke: boolean;
  /** Exactly one of the six nudges.md strings, or null when none is derivable. */
  reason: HeldBackReason | null;
  outcome: Outcome;
}

/** Escalation ladder: `speak` > `dashboard` (a pin or an insight) > `annotate`. */
function escalationOf(d: Decision, pin: PinRow | null): Nudge["escalation"] {
  if (d.spoke) return "speak";
  if (pin !== null || d.actions.some((a) => a.type === "log_insight")) return "dashboard";
  return "annotate";
}

/**
 * Evidence in five words or fewer (voice.md). The engine grades each pin, so
 * `Evidence grade A` is the citation the payload actually proves. A decision
 * with no pin carries no citation rather than a borrowed one.
 */
const evidenceOf = (pin: PinRow | null): string | null => (pin === null ? null : `Evidence grade ${pin.grade}`);

/** How close a pin's stamp must sit to a decision before the two are the same event. */
export const PIN_WINDOW_MIN = 10;

/** The pin nearest this minute inside `PIN_WINDOW_MIN`, or null. */
export function nearestPin(minuteOfDay: number, pins: readonly PinRow[]): PinRow | null {
  let best: PinRow | null = null;
  let bestGap = Number.POSITIVE_INFINITY;
  for (const pin of pins) {
    const mins = minutesOfHhmm(pin.time);
    if (mins === null) continue;
    const gap = Math.abs(mins - minuteOfDay);
    if (gap <= PIN_WINDOW_MIN && gap < bestGap) {
      best = pin;
      bestGap = gap;
    }
  }
  return best;
}

export interface NudgeFeed {
  /** Decisions that reached the ear, newest first. */
  spoken: Nudge[];
  /** Decisions that chose silence, newest first. */
  held: Nudge[];
  /** Counts per reason, in nudges.md order, omitting reasons with no rows. */
  heldCounts: Array<{ reason: HeldBackReason; n: number }>;
  /** Held-back rows whose reason the backend does not record. Never presented as a reason. */
  heldUnexplained: number;
  /**
   * Escalations the reasoner never ran (`dropped`, with a `drop_reason` from
   * the pipeline: `t1_busy`, `t1_timeout`, …). They are not held-back lines —
   * nothing decided against speaking — so they are counted here and shown only
   * in the Pipeline drawer.
   */
  notReached: number;
}

export interface NudgeFeedOptions {
  /** Minute of day the payload was built at; rules never read past it. */
  now: number;
}

const hhmm = (t: number): string =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

/**
 * The day's decisions → the ear log. Oldest-first internally (the 60-minute
 * gap and the outcome windows both read forward in time), then reversed so the
 * panel shows the most recent line first.
 */
export function buildNudgeFeed(
  decisions: readonly Decision[],
  pins: readonly PinRow[],
  { now }: NudgeFeedOptions,
): NudgeFeed {
  const ordered = [...decisions].sort((a, b) => a.t - b.t);
  const spoken: Nudge[] = [];
  const held: Nudge[] = [];
  const counts = new Map<HeldBackReason, number>();
  let heldUnexplained = 0;
  let notReached = 0;
  const spokenMinutes: number[] = [];

  for (const d of ordered) {
    if (d.dropped) {
      notReached += 1;
      continue;
    }
    const minuteOfDay = minutesOfInstant(d.t);
    const category = categoryOf(d);
    const pin = nearestPin(minuteOfDay, pins);
    const reason = d.spoke ? null : heldBackReason(d, minuteOfDay, spokenMinutes, category);
    const nudge: Nudge = {
      id: d.id,
      time: hhmm(d.t),
      minuteOfDay,
      category,
      trigger: d.trigger,
      frame: pin?.img ?? null,
      seen: pin?.seen ?? null,
      said: spokenText(d.actions),
      evidence: evidenceOf(pin),
      escalation: escalationOf(d, pin),
      spoke: d.spoke,
      reason,
      outcome: outcomeOf(category, minuteOfDay, pins, now),
    };
    if (d.spoke) {
      spokenMinutes.push(minuteOfDay);
      spoken.push(nudge);
    } else {
      held.push(nudge);
      if (reason === null) heldUnexplained += 1;
      else counts.set(reason, (counts.get(reason) ?? 0) + 1);
    }
  }

  return {
    spoken: spoken.reverse(),
    held: held.reverse(),
    heldCounts: HELD_BACK_REASONS.flatMap((reason) => {
      const n = counts.get(reason) ?? 0;
      return n > 0 ? [{ reason, n }] : [];
    }),
    heldUnexplained,
    notReached,
  };
}

/**
 * The muted line under the list: `Held back 14: 9 nothing worth saying · 3 you
 * were mid-conversation · 2 quiet hours.` (screens.md §1.3). Rows whose reason
 * the backend never recorded are named as such rather than folded into one of
 * the six.
 */
export function heldBackSummary(feed: NudgeFeed): string {
  const total = feed.held.length;
  if (total === 0) return "";
  const parts = feed.heldCounts.map(({ reason, n }) => `${n} ${reason}`);
  if (feed.heldUnexplained > 0) parts.push(`${feed.heldUnexplained} reason not recorded`);
  return `Held back ${total}: ${parts.join(" · ")}.`;
}
