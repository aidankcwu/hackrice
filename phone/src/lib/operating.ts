/**
 * The two ceilings. For any day, Cognition and Body are the percent of the
 * wearer's ceiling they operated at: 100 times the product of (1 - cost) over
 * every finding still active that day. A finding lands on the day it happened
 * or the next (`Rule.lands`), then fades linearly to nothing over its
 * `decayDays`, so costs compound across days and a clean streak recovers
 * toward 100.
 */
import type { Day } from "./month/types";
import { evaluateDay, RULES, type Finding, type RuleId } from "./rules";

export interface Contribution {
  finding: Finding;
  /** 0..1: how much of the finding's effect is still active on this day. */
  weight: number;
  /** Points of ceiling lost on this day. */
  cognition: number;
  body: number;
}

export interface Operating {
  date: string;
  cognition: number;
  body: number;
  /** Active costs on this day, biggest first. */
  contributions: Contribution[];
  /** The two biggest, in plain words. */
  top: string[];
  /** The one change that would lift tomorrow most. */
  lever: { rule: RuleId; text: string } | null;
}

/** How much of a finding from `sourceIndex` is active on `dayIndex`. */
export function weightOn(finding: Finding, sourceIndex: number, dayIndex: number): number {
  const rule = RULES[finding.rule];
  const offset = dayIndex - sourceIndex - rule.lands;
  if (offset < 0) return 0;
  if (offset === 0) return 1;
  if (rule.decayDays <= 0) return 0;
  return Math.max(0, 1 - offset / rule.decayDays);
}

/** Findings for every day of the month, computed once. */
export function monthFindings(days: readonly Day[]): Finding[][] {
  return days.map((_, i) => evaluateDay(days, i));
}

const LOOKBACK = 4; // no rule decays over more than 3 days

/** Cognition and Body for `days[index]`, with what moved them. */
export function operatingFor(days: readonly Day[], index: number, findings: Finding[][] = monthFindings(days)): Operating {
  const contributions: Contribution[] = [];
  let cognition = 1;
  let body = 1;
  for (let j = Math.max(0, index - LOOKBACK); j <= index; j++) {
    for (const f of findings[j]) {
      if (!f.cognition && !f.body) continue;
      const weight = weightOn(f, j, index);
      if (weight <= 0) continue;
      const c = f.cognition * weight;
      const b = f.body * weight;
      cognition *= 1 - c;
      body *= 1 - b;
      contributions.push({ finding: f, weight, cognition: c * 100, body: b * 100 });
    }
  }
  contributions.sort((a, b) => b.cognition + b.body - (a.cognition + a.body));
  const date = days[index].date;
  return {
    date,
    cognition: round1(cognition * 100),
    body: round1(body * 100),
    contributions,
    top: topReasons(contributions, date),
    lever: leverFor(days, index, findings),
  };
}

/** Every day of the month. */
export function operatingMonth(days: readonly Day[]): Operating[] {
  const findings = monthFindings(days);
  return days.map((_, i) => operatingFor(days, i, findings));
}

/** Merge a rule's contributions (three drinks are one reason), then name the two biggest. */
function topReasons(contributions: Contribution[], date: string): string[] {
  const byRule = new Map<string, { label: string; total: number }>();
  for (const c of contributions) {
    const key = `${c.finding.rule}:${c.finding.date}`;
    const existing = byRule.get(key);
    const total = c.cognition + c.body;
    if (existing) existing.total += total;
    else byRule.set(key, { label: reasonLabel(c.finding, date), total });
  }
  return [...byRule.values()]
    .sort((a, b) => b.total - a.total)
    .slice(0, 2)
    .map((r) => r.label);
}

/** "Coffee at 16:10 yesterday", "Broken sleep last night". */
export function reasonLabel(f: Finding, onDate: string): string {
  const when = f.date === onDate ? "" : ` ${relativeDay(f.date, onDate)}`;
  const at = f.eventId ? ` at ${clockOf(f.time)}` : "";
  const name = SHORT[f.rule];
  if (f.rule === "sleep_fragmented" || f.rule === "sleep_short" || f.rule === "sleep_deep") {
    return f.date === onDate ? `${name} last night` : `${name}${when}`;
  }
  return `${name}${at}${when}`;
}

const SHORT: Record<RuleId, string> = {
  caffeine: "Coffee",
  movement: "Late workout",
  last_meal: "Late dinner",
  eating_window: "Early first meal",
  food_quality: "Processed food",
  skipped_meal: "Skipped meal",
  alcohol: "Drinks",
  nicotine: "Nicotine",
  screens: "Screens after 21:30",
  phone_in_bed: "Phone in bed",
  nap: "Late nap",
  sedentary: "Long seated block",
  sauna_cold: "Cold close to bed",
  stress: "Stress spike",
  air: "Bad air",
  peptide: "Missed dose",
  sleep_window: "Late bedtime",
  sleep_short: "Short sleep",
  sleep_fragmented: "Broken sleep",
  sleep_deep: "Little deep sleep",
  wake_anchor: "Wake time off",
  morning_light: "No morning light",
  daylight: "Little daylight",
  people: "Little time with people",
  water: "Under 2 L of water",
  sick: "Sick day",
};

/**
 * The biggest lever for tomorrow: today's cost that still weighs on tomorrow,
 * else the rule that cost most over the last seven days, else null.
 */
function leverFor(days: readonly Day[], index: number, findings: Finding[][]): Operating["lever"] {
  const tomorrow = new Map<RuleId, number>();
  for (const f of findings[index]) {
    const w = weightOn(f, index, index + 1);
    if (w > 0 && f.tone === "violation") tomorrow.set(f.rule, (tomorrow.get(f.rule) ?? 0) + (f.cognition + f.body) * w);
  }
  const pick = (m: Map<RuleId, number>) => [...m.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  let rule = pick(tomorrow);
  if (!rule) {
    const week = new Map<RuleId, number>();
    for (let j = Math.max(0, index - 6); j <= index; j++) {
      for (const f of findings[j]) {
        if (f.tone !== "violation" && f.tone !== "watch") continue;
        week.set(f.rule, (week.get(f.rule) ?? 0) + f.cognition + f.body);
      }
    }
    rule = pick(week);
  }
  return rule ? { rule, text: RULES[rule].lever } : null;
}

function relativeDay(date: string, onDate: string): string {
  const diff = Math.round((Date.parse(`${onDate}T12:00:00Z`) - Date.parse(`${date}T12:00:00Z`)) / 86_400_000);
  if (diff === 1) return "yesterday";
  if (diff === 2) return "two days ago";
  return `${diff} days ago`;
}

function clockOf(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}
