/**
 * Pure helpers for the decision stream's annotation view. Nothing here invents
 * a number: every value returned is read off a `Decision` row or a `PinRow`
 * that the server payload already contains (product rule R1). When a fact is
 * missing the helper returns null and the UI prints an em dash.
 */
import type { Decision, DecisionAction } from "@/lib/types";
import type { PinRow } from "./types";

/** `HH:MM:SS` on the viewer's clock, from the decision's epoch seconds. */
export const clockTime = (t: number): string =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

/** `HH:MM`, the form evidence pins are stamped with. */
export const clockHhmm = (t: number): string =>
  new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

/** Minutes past local midnight for an `HH:MM` pin stamp; null when unparseable. */
export function minutesOfHhmm(hhmm: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  return h * 60 + min;
}

/** Minutes past local midnight for an epoch-second instant. */
export function minutesOfInstant(t: number): number {
  const d = new Date(t * 1000);
  return d.getHours() * 60 + d.getMinutes();
}

/** How close a pin's stamp must sit to a decision before the two are called the same event. */
export const LINK_WINDOW_MIN = 10;

/**
 * The evidence pin a decision most plausibly annotated, or null.
 *
 * Pins are built from the same glasses episodes the reasoner escalated on
 * (`pins_from_episodes`), but the engine stamps a pin with `HH:MM` only — it
 * does not carry the `episode_id` the decision names. So the link is made on
 * time, and only inside `LINK_WINDOW_MIN`; outside it the row says nothing
 * rather than guessing. A decision with no `episode_id` never links: it was
 * not about anything the glasses pinned.
 */
export function linkedPin(decision: Decision, pins: readonly PinRow[]): PinRow | null {
  if (!decision.episode_id) return null;
  const at = minutesOfInstant(decision.t);
  let best: PinRow | null = null;
  let bestGap = Number.POSITIVE_INFINITY;
  for (const pin of pins) {
    const mins = minutesOfHhmm(pin.time);
    if (mins === null) continue;
    const gap = Math.abs(mins - at);
    if (gap <= LINK_WINDOW_MIN && gap < bestGap) {
      best = pin;
      bestGap = gap;
    }
  }
  return best;
}

/** A decision's state, in the one word the row is coloured by. */
export type DecisionState = "dropped" | "spoke" | "silent";

export const decisionState = (d: Decision): DecisionState => (d.dropped ? "dropped" : d.spoke ? "spoke" : "silent");

/** The `annotate` line the reasoner wrote, or null when it wrote none. */
export function annotateLine(actions: readonly DecisionAction[]): string | null {
  for (const a of actions) if (a.type === "annotate" && a.line.trim().length > 0) return a.line;
  return null;
}

export interface InsightLine {
  category: string;
  text: string;
}

/** Every `log_insight` the decision filed, in order. */
export function insightLines(actions: readonly DecisionAction[]): InsightLine[] {
  const out: InsightLine[] = [];
  for (const a of actions) if (a.type === "log_insight" && a.text.trim().length > 0) out.push({ category: a.category, text: a.text });
  return out;
}

/** The sentence the glasses spoke (or proposed and the limiter suppressed), or null. */
export function spokenText(actions: readonly DecisionAction[]): string | null {
  for (const a of actions) if (a.type === "speak" && a.text.trim().length > 0) return a.text;
  return null;
}

/** Action types present, deduplicated, in first-seen order — the chip row. */
export function actionTypes(actions: readonly DecisionAction[]): Array<DecisionAction["type"]> {
  const seen = new Set<DecisionAction["type"]>();
  const out: Array<DecisionAction["type"]> = [];
  for (const a of actions) {
    if (seen.has(a.type)) continue;
    seen.add(a.type);
    out.push(a.type);
  }
  return out;
}

/** `log_insight` is the only type whose key is not the word to print. */
export const actionLabel = (type: DecisionAction["type"]): string => (type === "log_insight" ? "insight" : type);

/**
 * The signed healthy-life hours a pin's `effect` sentence states, as the
 * engine wrote it (`… · +0.4 h today`, `~−1 h of sleep tonight`). Returned
 * only when the string genuinely carries an hours figure; null otherwise, so
 * the row shows the sentence without a number of its own invention.
 */
export function effectHours(effect: string): number | null {
  const m = /([+−-])\s*(\d+(?:\.\d+)?)\s*h\b/.exec(effect);
  if (!m) return null;
  const value = Number(m[2]);
  if (!Number.isFinite(value)) return null;
  return m[1] === "+" ? value : -value;
}

/** Confidence clamped to 0…1 for the bar; the printed figure stays the raw one. */
export const confidenceWidth = (confidence: number): number =>
  Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : 0;

/** Newest first, the order the stream reads in. Never mutates the input. */
export const newestFirst = (decisions: readonly Decision[]): Decision[] => [...decisions].sort((a, b) => b.t - a.t);

/** One-line summary in the docs/API.md feed format, for the collapsed row's accessible name. */
export function feedLine(d: Decision): string {
  const types = actionTypes(d.actions).map(actionLabel).join(", ") || "no action";
  return `${clockHhmm(d.t)} · ${d.trigger} · ${d.interpretation || "no interpretation"} · ${types} · ${decisionState(d)}`;
}
