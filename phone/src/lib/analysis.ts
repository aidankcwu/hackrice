/**
 * What a week or two of reds did. Every red bar in range gets a number, the
 * same rule on several days sharing one, numbered by what it cost (1 is the
 * biggest). Each number becomes a card: the rule, the days, one sentence on
 * the body and one on the mind, with figures computed from the nights that
 * actually followed and the ceilings of the days after. A clause the data
 * cannot support is left out.
 */
import { BAR_TITLE, planFor, type DayPlan } from "./calendar";
import type { Day } from "./month/types";
import type { Operating } from "./operating";
import { RULES, WINDOWS, clock, peakUv, sleepCause, type Finding, type RuleId } from "./rules";

export interface AnalysisColumn {
  index: number;
  date: string;
  plan: DayPlan;
  cognition: number;
  body: number;
}

export interface Cluster {
  number: number;
  rule: RuleId;
  title: string;
  /** "Wed 16, Sat 19 and Tue 22". */
  days: string;
  body: string;
  mind: string;
}

export interface Analysis {
  columns: AnalysisColumn[];
  numbers: Partial<Record<RuleId, number>>;
  clusters: Cluster[];
  /** "This week: cognition 94% of ceiling, body 96%". */
  summary: string;
  /** "Biggest lever: caffeine before 12:30". */
  lever: string | null;
  /** What was not red but moved the ceilings: broken nights, sick days. */
  notes: string[];
  /** The whole month, one line. */
  month: string;
}

/** Where each rule shows in the night after it: less deep or REM sleep, or a later bedtime. */
type Metric = "deep" | "rem" | "bed";

const NIGHT: Partial<Record<RuleId, Metric>> = {
  caffeine: "deep",
  alcohol: "rem",
  last_meal: "deep",
  nicotine: "deep",
  movement: "bed",
  nap: "bed",
  screens: "bed",
  phone_in_bed: "bed",
  sleep_window: "bed",
};

/** How the day after tends to feel, in the rules' own terms (to verify, like their effects). */
const FEEL: Partial<Record<RuleId, string>> = {
  caffeine: "slower recall, shorter patience",
  last_meal: "heavier, slower mornings",
  nicotine: "a flatter, edgier morning",
  movement: "a slower start",
  nap: "a groggy start",
  screens: "a later body clock and a slower start",
  phone_in_bed: "a later body clock and a slower start",
  sleep_window: "slower reactions and a thinner mood",
  uv: "the heat load shows by evening",
};

const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen"];
const word = (n: number): string => WORDS[n] ?? String(n);
const capital = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
const mean = (xs: number[]): number | null => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const weekday = (date: string, style: "long" | "short" = "long") => new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: style });

function listDays(dates: string[]): string {
  const names = dates.map((d) => `${weekday(d, "short")} ${Number(d.slice(8))}`);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
}

/** The typical night: every night in the month with nothing on the day before to blame and no baby. */
function quietNight(days: readonly Day[], metric: Metric): number | null {
  const xs: number[] = [];
  for (let j = 0; j + 1 < days.length; j++) {
    const [d, n] = [days[j], days[j + 1]];
    if (sleepCause(d) || n.sleep.fragmented || [d.type, n.type].some((t) => t === "sick" || t === "travel")) continue;
    xs.push(n.sleep[metric]);
  }
  return mean(xs);
}

function sentences(days: readonly Day[], operating: Operating[], rule: RuleId, idx: number[]): { body: string; mind: string } {
  const feel = FEEL[rule] ?? "a slower day";

  if (rule === "uv") {
    const outs = idx
      .flatMap((i) => days[i].events.flatMap((e) => (e.kind === "outdoor" && e.sunlight && e.minutes >= WINDOWS.uvMaxMinutes ? [{ minutes: e.minutes, uv: peakUv(days[i].date, e.start, e.start + e.minutes) }] : [])))
      .filter((o) => o.uv >= WINDOWS.uvHigh);
    const minutes = outs.reduce((s, o) => s + o.minutes, 0);
    const uv = Math.max(...outs.map((o) => o.uv));
    const body = Math.round(mean(idx.map((i) => operating[i].body)) ?? 100);
    const cog = Math.round(mean(idx.map((i) => operating[i].cognition)) ?? 100);
    return {
      body: `${minutes} min in direct sun at UV ${uv}, against a 30-min limit; body at ${body}% of ceiling ${idx.length === 1 ? "that day" : "on those days"}.`,
      mind: `Cognition held at about ${cog}% of ceiling; ${feel}.`,
    };
  }

  const metric = NIGHT[rule];
  if (metric) {
    // The nights after that can be measured: recorded, not before a sick day, and
    // for deep and REM sleep and the mind, not broken by the baby.
    const recorded = idx.filter((i) => i + 1 < days.length && days[i + 1].type !== "sick");
    const pending = idx.filter((i) => i + 1 >= days.length).length;
    const sick = idx.length - recorded.length - pending;
    const clean = recorded.filter((i) => !days[i + 1].sleep.fragmented);
    const measured = metric === "bed" ? recorded : clean;
    const baby = recorded.length - clean.length;
    const nights = measured.length === 1 ? "that night" : `on those ${word(measured.length)} nights`;
    const base = quietNight(days, metric);
    const after = mean(measured.map((i) => days[i + 1].sleep[metric]));
    let body: string;
    if (after === null || base === null) body = pending ? "Tonight's sleep will show it" : "No night after it that can be measured";
    else if (metric === "bed") {
      body = after - base >= 5 ? `In bed about ${Math.round((after - base) / 5) * 5} min later than usual ${nights}` : `Bedtime ${nights} stayed near the usual ${clock(1440 + base)}`;
    } else {
      const words = metric === "deep" ? "deep sleep" : "REM sleep";
      body = base - after >= 5 ? `About ${Math.round((base - after) / 5) * 5} min less ${words} ${nights}` : `${capital(words)} ${nights} stayed near the usual ${Math.round(base)} min`;
    }
    if (after !== null) {
      const aside: string[] = [];
      if (baby && metric !== "bed") aside.push(baby === 1 ? "the night with the baby is left out" : "the nights with the baby are left out");
      if (sick) aside.push(sick === 1 ? "the night before the sick day is left out" : "the nights before sick days are left out");
      if (pending) aside.push("tonight's is still to come");
      if (aside.length) body += `; ${aside.length > 1 ? `${aside.slice(0, -1).join(", ")} and ${aside[aside.length - 1]}` : aside[0]}`;
    }
    body += ".";

    const next = mean(clean.map((i) => operating[i + 1].cognition));
    let mind: string;
    if (next === null) mind = pending ? `Tomorrow's cognition carries it: ${feel}.` : "No morning after it that can be measured.";
    else if (rule === "alcohol") {
      const twoOn = clean.filter((i) => i + 2 < days.length);
      const second = mean(twoOn.map((i) => operating[i + 2].cognition));
      mind =
        second !== null && twoOn.length === 1
          ? `The next day ran at about ${Math.round(next)}% of ceiling and ${weekday(days[twoOn[0] + 2].date)} at ${Math.round(second)}%: fog through ${weekday(days[twoOn[0] + 2].date)} morning.`
          : `The days after ran at about ${Math.round(next)}% of ceiling: fog and slower reactions.`;
    } else {
      mind = `${clean.length === 1 ? "The morning after ran" : "The mornings after ran"} at about ${Math.round(next)}% of ceiling: ${feel}.`;
    }
    return { body, mind };
  }

  const cog = Math.round(mean(idx.map((i) => operating[i].cognition)) ?? 100);
  const body = Math.round(mean(idx.map((i) => operating[i].body)) ?? 100);
  const when = idx.length === 1 ? "that day" : `on those ${word(idx.length)} days`;
  return { body: `Body ran at about ${body}% of ceiling ${when}.`, mind: `Cognition ran at about ${cog}% of ceiling ${when}: ${feel}.` };
}

export function analyze(days: readonly Day[], findings: Finding[][], operating: Operating[], from: number, to: number, label: string): Analysis {
  const range = Array.from({ length: to - from + 1 }, (_, n) => from + n);
  const columns = range.map((i) => ({ index: i, date: days[i].date, plan: planFor(days, i, findings[i]), cognition: operating[i].cognition, body: operating[i].body }));

  // One group per rule with a bar in range: its days, and what its reds cost.
  const groups = new Map<RuleId, { idx: number[]; cost: number }>();
  for (const column of columns) {
    for (const bar of column.plan.bars) {
      const g = groups.get(bar.rule) ?? { idx: [], cost: 0 };
      if (!g.idx.includes(column.index)) {
        g.idx.push(column.index);
        g.cost += findings[column.index].filter((f) => f.rule === bar.rule && f.tone === "violation").reduce((s, f) => s + f.cognition + f.body, 0);
      }
      groups.set(bar.rule, g);
    }
  }
  const ordered = [...groups.entries()].sort((a, b) => b[1].cost - a[1].cost || a[1].idx[0] - b[1].idx[0]);
  const numbers: Partial<Record<RuleId, number>> = {};
  const clusters: Cluster[] = ordered.map(([rule, g], n) => {
    numbers[rule] = n + 1;
    return { number: n + 1, rule, title: BAR_TITLE[rule] ?? RULES[rule].name, days: listDays(g.idx.map((i) => days[i].date)), ...sentences(days, operating, rule, g.idx) };
  });

  const notes: string[] = [];
  const broken = range.filter((i) => days[i].sleep.fragmented);
  if (broken.length) {
    const cog = Math.round(mean(broken.map((i) => operating[i].cognition)) ?? 100);
    const one = broken.length === 1;
    notes.push(`${capital(word(broken.length))} broken ${one ? "night" : "nights"} with the baby, not your decision: cognition about ${cog}% on ${one ? "that day" : "those days"}.`);
  }
  const sick = range.filter((i) => days[i].type === "sick");
  if (sick.length) {
    notes.push(`${capital(word(sick.length))} sick ${sick.length === 1 ? "day" : "days"}, protocol relaxed: body at ${Math.round(operating[sick[0]].body)}% of ceiling.`);
  }

  const avg = (key: "cognition" | "body", is: number[]) => Math.round(mean(is.map((i) => operating[i][key])) ?? 100);
  const all = days.map((_, i) => i);
  const top = ordered[0]?.[0];
  const lever = top ? RULES[top].lever : null;

  return {
    columns,
    numbers,
    clusters,
    summary: `${label}: cognition ${avg("cognition", range)}% of ceiling, body ${avg("body", range)}%`,
    lever: lever ? `Biggest lever: ${lever.charAt(0).toLowerCase()}${lever.slice(1)}` : null,
    notes,
    month: `${days.length} days: cognition ${avg("cognition", all)}% of ceiling, body ${avg("body", all)}%`,
  };
}
