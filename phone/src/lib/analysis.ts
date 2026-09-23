/**
 * What a week or a month of reds did, in sentences with numbers. Every number
 * is computed from the days in range: deep sleep from the nights that actually
 * followed, cognition from the two ceilings. A clause is dropped when the data
 * cannot support it (no comparable night, no following day).
 */
import type { Day } from "./month/types";
import type { Operating } from "./operating";
import { RULES, sleepCause, type Finding, type RuleId } from "./rules";

export interface Analysis {
  sentences: string[];
  summary: string;
  lever: string | null;
  /** One cell per day: the day's operating level, the mean of the two ceilings. */
  strip: { date: string; level: number; sick: boolean }[];
}

const mean = (xs: number[]): number | null => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const weekdayName = (date: string): string => new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "long" });

/** Rules whose reds get their own sentence here; the rest are grouped. */
const OWN_SENTENCE = new Set<RuleId>(["caffeine", "alcohol", "sleep_fragmented", "sick"]);

export function analyze(days: readonly Day[], findings: Finding[][], operating: Operating[], from: number, to: number, label: string): Analysis {
  const range = Array.from({ length: to - from + 1 }, (_, n) => from + n);
  const has = (i: number, rule: RuleId, tone: Finding["tone"] = "violation") => findings[i].some((f) => f.rule === rule && f.tone === tone);
  const sentences: string[] = [];

  // Late caffeine: the nights after, against nights with nothing to blame.
  const lateDays = range.filter((i) => has(i, "caffeine"));
  if (lateDays.length) {
    const after = lateDays.filter((i) => i + 1 < days.length);
    const quiet = range.filter(
      (i) => i + 1 < days.length && !sleepCause(days[i]) && !days[i + 1].sleep.fragmented && days[i].type !== "sick" && days[i + 1].type !== "sick",
    );
    const deepAfter = mean(after.map((i) => days[i + 1].sleep.deep));
    const deepQuiet = mean(quiet.map((i) => days[i + 1].sleep.deep));
    const cogAfter = mean(after.map((i) => operating[i + 1].cognition));
    const cogOther = mean(range.filter((i) => !lateDays.includes(i - 1)).map((i) => operating[i].cognition));
    const parts = [`${lateDays.length} late ${lateDays.length === 1 ? "caffeine" : "caffeines"}`];
    if (deepAfter !== null && deepQuiet !== null && deepQuiet - deepAfter >= 5) {
      parts.push(`about ${Math.round((deepQuiet - deepAfter) / 5) * 5} min less deep sleep on those nights`);
    }
    if (cogAfter !== null && cogOther !== null && cogOther - cogAfter >= 1) {
      parts.push(`next-day cognition about ${Math.round(cogOther - cogAfter)}% lower`);
    } else if (!after.length) {
      parts.push("tonight's sleep pays for it");
    }
    sentences.push(parts.join(" → "));
  }

  // Each drinking night, and how long the fog lasted.
  for (const i of range.filter((j) => has(j, "alcohol"))) {
    const drinks = days[i].events.reduce((n, e) => (e.kind === "alcohol" ? n + e.drinks : n), 0);
    const fogDays = [1, 2].filter((k) => i + k < days.length);
    const through = fogDays.length ? weekdayName(days[i + fogDays[fogDays.length - 1]].date) : null;
    const lost = i + 1 < days.length ? Math.round(100 - operating[i + 1].cognition) : null;
    const tail = through ? `fog through ${through} morning` : "fog tomorrow";
    sentences.push(`${weekdayName(days[i].date)}'s ${drinks} ${drinks === 1 ? "drink" : "drinks"} → ${tail}${lost ? `, the morning after at ${100 - lost}% of ceiling` : ""}`);
  }

  // Broken nights: named, never blamed.
  const broken = range.filter((i) => days[i].sleep.fragmented);
  if (broken.length) {
    const cog = mean(broken.map((i) => operating[i].cognition));
    sentences.push(
      `${broken.length} broken ${broken.length === 1 ? "night" : "nights"} with the baby, not your decision → cognition ${cog !== null ? `about ${Math.round(cog)}%` : "lower"} on those days`,
    );
  }

  const sick = range.filter((i) => days[i].type === "sick");
  if (sick.length) sentences.push(`${sick.length} sick ${sick.length === 1 ? "day" : "days"}, protocol relaxed → body ${Math.round(operating[sick[0]].body)}% that day`);

  // Every other red, grouped by rule, most costly first.
  const grouped = new Map<RuleId, { count: number; cost: number }>();
  for (const i of range) {
    for (const f of findings[i]) {
      if (f.tone !== "violation" || OWN_SENTENCE.has(f.rule)) continue;
      const g = grouped.get(f.rule) ?? { count: 0, cost: 0 };
      g.count += 1;
      g.cost += f.cognition + f.body;
      grouped.set(f.rule, g);
    }
  }
  const others = [...grouped.entries()].sort((a, b) => b[1].cost - a[1].cost).slice(0, 3);
  for (const [rule, g] of others) {
    const consequence = firstSentence(RULES[rule].consequence);
    sentences.push(`${RULES[rule].name}, ${g.count} ${g.count === 1 ? "red" : "reds"} → ${consequence.charAt(0).toLowerCase()}${consequence.slice(1)}`);
  }

  // The averages, and the one lever.
  const cog = mean(range.map((i) => operating[i].cognition)) ?? 100;
  const body = mean(range.map((i) => operating[i].body)) ?? 100;
  const cost = new Map<RuleId, number>();
  for (const i of range) {
    for (const f of findings[i]) {
      if (f.tone !== "violation" && f.tone !== "watch") continue;
      cost.set(f.rule, (cost.get(f.rule) ?? 0) + f.cognition + f.body);
    }
  }
  const top = [...cost.entries()].sort((a, b) => b[1] - a[1])[0];
  const lever = top ? RULES[top[0]].lever : null;

  return {
    sentences,
    summary: `${label}: cognition ${Math.round(cog)}% of ceiling, body ${Math.round(body)}%`,
    lever: lever ? `Biggest lever: ${lever.charAt(0).toLowerCase()}${lever.slice(1)}` : null,
    strip: range.map((i) => ({ date: days[i].date, level: (operating[i].cognition + operating[i].body) / 2, sick: days[i].type === "sick" })),
  };
}

function firstSentence(text: string): string {
  const end = text.indexOf(". ");
  return end > 0 ? text.slice(0, end) : text.replace(/\.$/, "");
}
