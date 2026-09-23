/**
 * Today as the page for the day: the two ceilings with their top reason, and
 * "Your day" in three to five plain sentences built from the rules and the
 * peptide schedule. Nothing here is invented; every clause comes from a
 * finding, a contribution or an event on the day.
 */
import { BAR_TITLE } from "./calendar";
import type { Day } from "./month/types";
import { reasonLabel, type Contribution, type Operating } from "./operating";
import { clock, hm, peptideSchedule, WINDOWS, type Finding, type RuleId } from "./rules";

export interface CeilingLine {
  label: "Cognition" | "Body";
  value: number;
  reason: string;
}

/** A rule's contributions from one day are one reason (three drinks, not three). */
function merged(contributions: Contribution[], key: "cognition" | "body"): { finding: Finding; points: number }[] {
  const byRule = new Map<string, { finding: Finding; points: number }>();
  for (const c of contributions) {
    if (c[key] <= 0) continue;
    const id = `${c.finding.rule}:${c.finding.date}`;
    const existing = byRule.get(id);
    if (existing) existing.points += c[key];
    else byRule.set(id, { finding: c.finding, points: c[key] });
  }
  return [...byRule.values()].sort((a, b) => b.points - a.points);
}

function off(points: number): string {
  return points < 0.5 ? "under 1% off" : `${Math.round(points)}% off`;
}

export function ceilingLines(op: Operating): CeilingLine[] {
  return (["cognition", "body"] as const).map((key) => {
    const top = merged(op.contributions, key)[0];
    return {
      label: key === "cognition" ? "Cognition" : "Body",
      value: op[key],
      reason: top ? `${reasonLabel(top.finding, op.date)}, ${off(top.points)}` : "Nothing is costing it today.",
    };
  });
}

/** Rules whose cost lands on tomorrow through tonight's sleep. */
const THROUGH_SLEEP = new Set<RuleId>(["caffeine", "exercise_timing", "last_meal", "alcohol", "screens", "phone_in_bed", "nap"]);

/** Reds the Calendar draws as bars (calendar.ts `barsFor`); day-level reds such as sleep debt have no bar. */
const onCalendar = (rule: RuleId): boolean => rule === "alcohol" || rule in BAR_TITLE;

export function yourDay(days: readonly Day[], index: number, findings: Finding[][], operating: Operating[]): string[] {
  const day = days[index];
  const today = findings[index];
  const out: string[] = [];

  // The peptide first: taken or not, and the run it belongs to.
  const { onSchedule, of } = peptideSchedule(days, index);
  const run = `on schedule ${onSchedule} of ${of} days`;
  const dose = (d: "AM" | "PM") => day.events.find((e): e is Extract<Day["events"][number], { kind: "peptide" }> => e.kind === "peptide" && e.dose === d);
  const am = dose("AM");
  const pm = dose("PM");
  let peptide: string;
  if (!am) peptide = `No morning peptide seen; ${run}.`;
  else if (!am.taken) peptide = `Morning peptide missed; ${run}.`;
  else if (am.start < WINDOWS.peptideAm[0] || am.start > WINDOWS.peptideAm[1]) peptide = `Peptide taken ${clock(am.start)}, outside its window; ${run}.`;
  else peptide = `Peptide taken ${clock(am.start)}, ${run}.`;
  if (pm && !pm.taken) peptide += " Evening dose missed.";
  else if (pm) peptide += ` Evening dose taken ${clock(pm.start)}.`;
  else if (day.until < WINDOWS.peptidePm[1]) peptide += ` Evening dose due by ${clock(WINDOWS.peptidePm[1])}.`;
  out.push(peptide);

  // Last night, plainly.
  const s = day.sleep;
  const baby = s.wakings.filter((w) => w.baby).length;
  const woken = baby ? `, woken ${baby} ${baby === 1 ? "time" : "times"} by the baby, not your decision` : s.fragmented ? `, woken ${s.wakings.length} times` : "";
  out.push(`Slept ${hm(s.minutes)} with ${s.deep} min deep${woken}.`);
  if (day.type === "sick") out.push("Sick day: the protocol is relaxed and nothing is marked red.");

  // Today's reds, grouped by rule, biggest first; at most two named.
  const reds = new Map<RuleId, Finding[]>();
  for (const f of today) if (f.tone === "violation") reds.set(f.rule, [...(reds.get(f.rule) ?? []), f]);
  const named = [...reds.values()]
    .sort((a, b) => sum(b) - sum(a))
    .slice(0, 2);
  for (const group of named) {
    const f = group[0];
    const drinks = day.events.reduce((n, e) => (e.kind === "alcohol" ? n + e.drinks : n), 0);
    const what = f.rule === "alcohol" ? `${drinks} ${drinks === 1 ? "drink" : "drinks"} from ${clock(f.time)}` : reasonLabel(f, day.date);
    const cog = sum(group, "cognition") * 100;
    if (THROUGH_SLEEP.has(f.rule)) out.push(`${what} cost tonight's sleep; tomorrow's cognition about ${Math.max(1, Math.round(cog))}% lower.`);
    else out.push(`${what}: ${firstClause(f.line)}.`);
  }
  // The rest, split by whether the Calendar has a bar to point at.
  const rest = [...reds.keys()].filter((rule) => !named.some((group) => group[0].rule === rule));
  const barred = rest.filter(onCalendar).length;
  const unbarred = rest.length - barred;
  const more = (n: number) => `${n} more ${n === 1 ? "red" : "reds"}`;
  if (barred && unbarred) out.push(`${more(barred)} on the calendar and ${unbarred} more today.`);
  else if (barred) out.push(`${more(barred)} on the calendar.`);
  else if (unbarred) out.push(`${more(unbarred)} today.`);

  // Anything from an earlier day still weighing on today.
  const carried = merged(operating[index].contributions.filter((c) => c.finding.date !== day.date && c.finding.tone === "violation"), "cognition")[0];
  if (carried && carried.points >= 0.5 && out.length < 5) {
    const label = reasonLabel(carried.finding, day.date);
    out.push(`Still carried today: ${label.charAt(0).toLowerCase()}${label.slice(1)}, about ${Math.round(carried.points)}% off cognition.`);
  }

  // Close: what else moved, or the one lever.
  if (out.length < 5) {
    const ambers = today.filter((f) => f.tone === "watch").length;
    if (!reds.size && !carried && !ambers) out.push("Nothing else moved.");
    else if (ambers) out.push(`${ambers} ${ambers === 1 ? "thing" : "things"} to watch on the calendar; nothing else moved.`);
    else out.push("Nothing else moved.");
  }
  return out.slice(0, 5);
}

function sum(fs: Finding[], key?: "cognition" | "body"): number {
  return fs.reduce((n, f) => n + (key ? f[key] : f.cognition + f.body), 0);
}

function firstClause(line: string): string {
  const end = line.indexOf(". ");
  const first = end > 0 ? line.slice(0, end) : line.replace(/\.$/, "");
  return first.charAt(0).toLowerCase() + first.slice(1);
}
