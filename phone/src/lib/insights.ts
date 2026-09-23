/**
 * Insight cards for Analysis: one per study-backed pattern, shown only when its
 * condition holds in the chosen range, most recent first, at most six. Every
 * sentence carries the wearer's own numbers from the month; the study's number
 * is quoted as "(study: N)" where the wearer's differs or where the study sets
 * the claim. Nothing here is invented: a card whose data is missing is left
 * out rather than filled in.
 */
import type { Day, MonthEvent } from "./month/types";
import { sunTimes } from "./month/sun";
import type { Operating } from "./operating";
import {
  HOUSTON,
  WINDOWS,
  caffeineCutoff,
  clock,
  daylightMinutes,
  hm,
  shortSleepDay,
  sleepRegularityIndex,
  socialJetlagMinutes,
  type Finding,
} from "./rules";

/** The photo-panel tints, as `PhotoPanel` names them. */
export type InsightTint = "stone" | "sage" | "sand" | "slate" | "bluegrey";

/** The card's icon, by meaning (`MEANING_ICONS`). */
export type InsightIcon = "mind" | "caffeine" | "light" | "air" | "sleep" | "alcohol" | "people" | "movement";

export interface Insight {
  id: string;
  title: string;
  line: string;
  tint: InsightTint;
  /** Keys into `SOURCES`. */
  sourceKeys: string[];
  /** The day the card is about; cards sort by it, newest first. */
  date: string;
  icon: InsightIcon;
}

const MAX_CARDS = 6;
const SHORT_NIGHT = 7 * 60;
/** Nights looked back for a wearer's own HRV and resting-HR baseline, and the fewest that make one. */
const BASELINE_NIGHTS = 14;
const MIN_BASELINE_NIGHTS = 3;
/** The regularity index at or above which the week counts as regular. */
const REGULAR = 70;
/** Sleep debt over this many hours counts as short on duration. */
const DEBT_HOURS = 2;
/** Rångtell: 6.5 h of daytime light erased the screen effect; 2 h of tolerance per hour of daylight is our assumption. */
const SCREEN_TOLERANCE_HOURS = 2;
/** Allen 2016: −15% at 945 ppm, −50% at 1,400 ppm. */
const CO2_STUDY = "−15% at 945 ppm and −50% at 1,400 ppm, against 550";
/** Grosicki 2026 midpoints, per drink that night. */
const RHR_PER_DRINK = 2.6;
const HRV_PER_DRINK = 3.5;

type Of<K extends MonthEvent["kind"]> = Extract<MonthEvent, { kind: K }>;

const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const word = (n: number): string => WORDS[n] ?? String(n);
const capital = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
const minus = "−";

/** "Tue 22". */
function dayName(date: string): string {
  const d = new Date(`${date}T12:00:00`);
  return `${d.toLocaleDateString("en-US", { weekday: "short" })} ${d.getDate()}`;
}

/** "6.2", "58", "41.5": one decimal at most. */
function fmt(x: number): string {
  return String(Math.round(x * 10) / 10);
}

/** "+2.3", "−4.2". */
function signed(x: number): string {
  const v = Math.round(x * 10) / 10;
  return v < 0 ? `${minus}${fmt(-v)}` : `+${fmt(v)}`;
}

function median(xs: readonly number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function of<K extends MonthEvent["kind"]>(day: Day, kind: K): Of<K>[] {
  return day.events.filter((e): e is Of<K> => e.kind === kind);
}

function drinksOn(day: Day): number {
  return day.events.reduce((n, e) => (e.kind === "alcohol" ? n + e.drinks : n), 0);
}

/** The first mind check before noon, ms; null when none. */
function morningPvt(day: Day): number | null {
  for (const e of day.events) if (e.kind === "mind_check" && e.start < 12 * 60) return e.ms;
  return null;
}

/** The plain-words name of an outdoor or workout event, without a trailing ", 30 min". */
function shortLabel(label: string): string {
  return label.replace(/,\s*\d+\s*min$/i, "").split(",")[0].trim();
}

// ---------------------------------------------------------------------------
// The cards
// ---------------------------------------------------------------------------

/** Van Dongen: three or more short nights in a row ending on the last day, with this morning's PVT against the best. */
function shortSleep(days: readonly Day[], operating: Operating[], to: number): Insight | null {
  const n = shortSleepDay(days, to);
  if (n < 3) return null;
  const day = days[to];
  const op = operating[to];
  const pvt = morningPvt(day);
  const best = op.calibration.ready ? op.calibration.pvtBestMs : null;
  let measured: string;
  if (pvt !== null && best !== null && op.measured.cognition !== null) {
    measured = `This morning's reaction time ${pvt} ms against your best ${Math.round(best)} ms: ${Math.round(op.measured.cognition)}% of your 100.`;
  } else if (pvt !== null) {
    measured = `This morning's reaction time ${pvt} ms; ${op.calibration.label}, so there is no best to hold it against yet.`;
  } else {
    measured = "No mind check this morning to measure it.";
  }
  return {
    id: "short-sleep",
    title: `You feel fine; your reaction time says day ${n}`,
    line: `${capital(word(n))} nights under 7 h in a row ending ${dayName(day.date)}, last night ${hm(day.sleep.minutes)}. ${measured} (study: 6 h a night for 14 nights performs like 1 to 2 nights of no sleep, and sleepiness ratings plateau by day 2 to 3 while performance keeps falling)`,
    tint: "slate",
    sourceKeys: ["vandongen2003", "lim2010"],
    date: day.date,
    icon: "mind",
  };
}

/** Gardiner: the cutoff from the wearer's usual dose in the range, the modal cups a day and their strength. */
function caffeine(days: readonly Day[], from: number, to: number): Insight | null {
  const cups: { day: Day; e: Of<"caffeine"> }[] = [];
  const perDay: number[] = [];
  for (let i = from; i <= to; i++) {
    const es = of(days[i], "caffeine");
    perDay.push(es.length);
    for (const e of es) cups.push({ day: days[i], e });
  }
  if (!cups.length) return null;

  const strong = cups.filter(({ e }) => e.drink === "energy_drink" || e.strength === "double").length;
  const usualStrong = strong > cups.length - strong;
  const cutoff = usualStrong ? WINDOWS.caffeineEndStrong : WINDOWS.caffeineEnd;
  const hours = Math.round((WINDOWS.sleepStart - cutoff) / 60);

  const counts = new Map<number, number>();
  for (const n of perDay) if (n > 0) counts.set(n, (counts.get(n) ?? 0) + 1);
  const modal = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0];

  const late = cups.filter(({ e }) => e.start > caffeineCutoff(e.drink, e.strength));
  const latest = late[late.length - 1];
  const daysWith = perDay.filter((n) => n > 0).length;
  const dose = usualStrong ? "a double or an energy drink" : "one coffee or tea";
  const lateLine = latest
    ? ` ${capital(word(late.length))} ${late.length === 1 ? "was" : "were"} after it, the latest at ${clock(latest.e.start)} on ${dayName(latest.day.date)}.`
    : " None was after it.";

  return {
    id: "caffeine-cutoff",
    title: `Your caffeine cutoff is ${clock(cutoff)}, not noon`,
    line: `${cups.length} ${cups.length === 1 ? "cup" : "cups"} over ${daysWith} ${daysWith === 1 ? "day" : "days"}, usually ${modal} a day and ${dose}: ${hours} h before a ${clock(WINDOWS.sleepStart)} bed is ${clock(cutoff)} (study: about 9 h for ~100 mg, 13 h for ~200 mg).${lateLine}`,
    tint: "sand",
    sourceKeys: ["gardiner2023", "drake2013"],
    date: cups[cups.length - 1].day.date,
    icon: "caffeine",
  };
}

/** Rångtell: a day with an hour of daylight and screens past 21:30 whose night was still not short. */
function daylightTolerance(days: readonly Day[], findings: Finding[][], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const day = days[i];
    const next = days[i + 1];
    if (!next || next.sleep.minutes <= 0 || next.sleep.minutes < SHORT_NIGHT) continue;
    const screens = (findings[i] ?? []).find((f) => f.rule === "screens" && f.tone === "watch");
    if (!screens) continue;
    const daylight = daylightMinutes(day, sunTimes(day.date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset).sunset);
    if (daylight < WINDOWS.daylightTarget) continue;
    const e = day.events.find((x) => x.id === screens.eventId);
    const device = e?.kind === "screen" ? e.device : "screen";
    const until = e?.kind === "screen" ? clock(e.start + e.minutes) : clock(screens.time);
    return {
      id: "daylight-tolerance",
      title: `Daylight bought you ${SCREEN_TOLERANCE_HOURS} h of screen tolerance`,
      line: `${daylight} min of daylight on ${dayName(day.date)}, then the ${device} until ${until}, past ${clock(WINDOWS.screensOff)}; that night ${hm(next.sleep.minutes)}, not short. The ${SCREEN_TOLERANCE_HOURS} h of tolerance is assumed (study: 6.5 h of daytime light erased the evening screen effect).`,
      tint: "sage",
      sourceKeys: ["rangtell2016", "chang2015"],
      date: day.date,
      icon: "light",
    };
  }
  return null;
}

/** Allen: the most recent room over 900 ppm, with its ppm and minutes. */
function staleRoom(days: readonly Day[], findings: Finding[][], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const found = [...(findings[i] ?? [])].reverse().find((f) => f.rule === "co2" && (f.tone === "watch" || f.tone === "violation"));
    if (!found) continue;
    const e = days[i].events.find((x) => x.id === found.eventId);
    if (!e || e.kind !== "co2") continue;
    const red = found.tone === "violation";
    const share = red ? "15 to 50%" : "about 15%";
    return {
      id: "stale-room",
      title: red ? "This room cost up to 50% of your decisions" : "This room cost 15% of your decisions",
      line: `${e.label}, ${e.ppm.toLocaleString("en-US")} ppm for ${hm(e.minutes)} on ${dayName(days[i].date)}: decisions ran ${share} lower in that air for those minutes (study: ${CO2_STUDY}). Open a window.`,
      tint: "bluegrey",
      sourceKeys: ["allen2016", "satish2012"],
      date: days[i].date,
      icon: "air",
    };
  }
  return null;
}

/** Windred: regularity against duration over the week ending on the last day, whichever won. */
function regularity(days: readonly Day[], operating: Operating[], to: number): Insight | null {
  if (to < 1) return null;
  const sri = sleepRegularityIndex(days, to);
  const debt = operating[to].inputs.sleepDebtHours;
  const regular = sri >= REGULAR;
  const short = debt > DEBT_HOURS;
  if (regular === !short) return null;
  const score = `${sri} of 100 (our own bed and wake drift score over 7 nights, not Windred's SRI)`;
  const study = "(study: regularity predicts mortality better than duration; top against bottom quintile, 30% lower)";
  return {
    id: "regularity",
    title: regular ? `Regularity beat duration this week: ${sri} of 100` : `Duration beat regularity this week: ${sri} of 100`,
    line: regular
      ? `Bed and wake times held to ${score} while sleep debt reached ${fmt(debt)} h over 14 nights, ending ${dayName(days[to].date)} ${study}.`
      : `Sleep debt only ${fmt(debt)} h over 14 nights ending ${dayName(days[to].date)}, but bed and wake times drifted: ${score} ${study}.`,
    tint: "slate",
    sourceKeys: ["windred2024", "phillips2017"],
    date: days[to].date,
    icon: "sleep",
  };
}

/** Grosicki: the most recent drinking night with the watch on, the shift per drink against the wearer's own baseline. */
function drinks(days: readonly Day[], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const n = drinksOn(days[i]);
    const next = days[i + 1];
    if (!n || !next || next.sleep.hrv_ms === null || next.sleep.rhr_bpm === null) continue;
    const hrv: number[] = [];
    const rhr: number[] = [];
    for (let j = Math.max(0, i - BASELINE_NIGHTS + 1); j <= i; j++) {
      const s = days[j].sleep;
      if (s.hrv_ms === null || s.rhr_bpm === null || s.fragmented || days[j].type === "sick") continue;
      if (j > 0 && drinksOn(days[j - 1]) > 0) continue;
      hrv.push(s.hrv_ms);
      rhr.push(s.rhr_bpm);
    }
    if (hrv.length < MIN_BASELINE_NIGHTS) continue;
    const baseHrv = median(hrv);
    const baseRhr = median(rhr);
    const dHrv = (next.sleep.hrv_ms - baseHrv) / n;
    const dRhr = (next.sleep.rhr_bpm - baseRhr) / n;
    const es = of(days[i], "alcohol");
    const when = es.length > 1 ? `${clock(es[0].start)} to ${clock(es[es.length - 1].start)}` : clock(es[0].start);
    const count = n === 1 ? "One drink" : `${capital(word(n))} drinks`;
    const dir = (x: number) => (x < 0 ? "down" : "up");
    return {
      id: "drinks",
      title: `${count}: ${signed(dRhr)} bpm and ${signed(dHrv)} ms ${n === 1 ? "that night" : "per drink"}`,
      line: `${count} on ${dayName(days[i].date)}, ${when}. That night resting HR ${fmt(next.sleep.rhr_bpm)} bpm against your ${fmt(baseRhr)} baseline and HRV ${fmt(next.sleep.hrv_ms)} ms against ${fmt(baseHrv)}: ${dir(dRhr)} ${fmt(Math.abs(dRhr))} bpm and ${dir(dHrv)} ${fmt(Math.abs(dHrv))} ms per drink (study: +${RHR_PER_DRINK} bpm and ${minus}${HRV_PER_DRINK} ms per drink, midpoints over 5.1M nights). The baseline is the median of your last ${hrv.length} quiet nights.`,
      tint: "stone",
      sourceKeys: ["grosicki2026", "gunn2018"],
      date: days[i].date,
      icon: "alcohol",
    };
  }
  return null;
}

/** Roenneberg: the most recent week in range whose free-night midsleep sat over an hour from weekdays. */
function socialJetlag(days: readonly Day[], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const jet = socialJetlagMinutes(days, i);
    if (jet <= WINDOWS.socialJetlagAmber) continue;
    return {
      id: "social-jetlag",
      title: `Social jetlag ${fmt(jet / 60)} h this weekend`,
      line: `Over the 7 nights ending ${dayName(days[i].date)}, midsleep on free nights sat ${hm(jet)} from weekday nights (study: each hour of social jetlag, 33% higher odds of overweight).`,
      tint: "bluegrey",
      sourceKeys: ["roenneberg2012"],
      date: days[i].date,
      icon: "sleep",
    };
  }
  return null;
}

/** Hunter: the most recent outdoor block of 20 min or more. */
function outdoors(days: readonly Day[], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const blocks = of(days[i], "outdoor").filter((e) => e.minutes >= 20);
    if (!blocks.length) continue;
    const e = blocks[blocks.length - 1];
    const total = of(days[i], "outdoor").reduce((s, x) => s + x.minutes, 0);
    const rest = total > e.minutes ? ` ${total} min outside in all that day.` : "";
    return {
      id: "outdoors",
      title: `${e.minutes} minutes outside drops cortisol faster than sitting`,
      line: `${shortLabel(e.label)}, ${e.minutes} min at ${clock(e.start)} on ${dayName(days[i].date)}${e.sunlight ? ", in daylight" : ", no direct sun"}.${rest} (study: 20 to 30 min outdoors drops cortisol about 21% an hour beyond its normal decline)`,
      tint: "sage",
      sourceKeys: ["hunter2019"],
      date: days[i].date,
      icon: "light",
    };
  }
  return null;
}

/** Ybarra: the most recent day with ten minutes of talking whose mind check sat at or under the wearer's best median. */
function talking(days: readonly Day[], operating: Operating[], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const op = operating[i];
    const best = op.calibration.ready ? op.calibration.pvtBestMs : null;
    const pvt = morningPvt(days[i]);
    if (best === null || pvt === null || pvt > best) continue;
    const talks = of(days[i], "conversation").filter((e) => e.minutes >= 10);
    if (!talks.length) continue;
    const e = talks.sort((a, b) => b.minutes - a.minutes)[0];
    return {
      id: "talking",
      title: `${e.minutes} minutes talking was worth a puzzle`,
      line: `${shortLabel(e.label)}, ${e.minutes} min at ${clock(e.start)} on ${dayName(days[i].date)}; that morning's reaction time ${pvt} ms, at or under your best median of ${Math.round(best)} ms (study: 10 min of talking lifted processing speed and working memory as much as a puzzle session).`,
      tint: "sand",
      sourceKeys: ["ybarra2008"],
      date: days[i].date,
      icon: "people",
    };
  }
  return null;
}

/** Stutz: the most recent moderate workout, or an outdoor walk after 18:30, whose night was not short. */
function moderateEvening(days: readonly Day[], from: number, to: number): Insight | null {
  for (let i = to; i >= from; i--) {
    const next = days[i + 1];
    if (!next || next.sleep.minutes <= 0 || next.sleep.minutes < SHORT_NIGHT) continue;
    const moves: { label: string; start: number; minutes: number; walk: boolean }[] = [
      ...of(days[i], "workout").filter((e) => !e.vigorous).map((e) => ({ label: shortLabel(e.label), start: e.start, minutes: e.minutes, walk: false })),
      ...of(days[i], "outdoor").filter((e) => e.start >= WINDOWS.moveBy).map((e) => ({ label: shortLabel(e.label), start: e.start, minutes: e.minutes, walk: true })),
    ].sort((a, b) => a.start - b.start);
    const e = moves[moves.length - 1];
    if (!e) continue;
    const what = e.walk ? "late walk" : e.start >= WINDOWS.moveBy ? "evening session" : "moderate session";
    return {
      id: "moderate-evening",
      title: `Your ${clock(e.start)} ${what} was fine: ${hm(next.sleep.minutes)} that night`,
      line: `${e.label}, ${e.minutes} min at ${clock(e.start)} on ${dayName(days[i].date)}, ${e.walk ? "outside" : "moderate"}; in bed at ${clock(1440 + next.sleep.bed)}, ${hm(next.sleep.minutes)} of sleep, not short (study: moderate evening exercise does not hurt sleep; only vigorous exercise ending 2 h before bed delays onset, about 36 min).`,
      tint: "stone",
      sourceKeys: ["stutz2019", "leota2025"],
      date: days[i].date,
      icon: "movement",
    };
  }
  return null;
}

/**
 * The cards for `days[from..to]`, newest first, at most six. Each card is
 * computed from the month; a card whose condition does not hold in the range
 * is not returned.
 */
export function insightsFor(days: readonly Day[], findings: Finding[][], operating: Operating[], from: number, to: number): Insight[] {
  if (!days.length || to < from || to >= days.length) return [];
  const cards = [
    shortSleep(days, operating, to),
    caffeine(days, from, to),
    daylightTolerance(days, findings, from, to),
    staleRoom(days, findings, from, to),
    regularity(days, operating, to),
    drinks(days, from, to),
    socialJetlag(days, from, to),
    outdoors(days, from, to),
    talking(days, operating, from, to),
    moderateEvening(days, from, to),
  ].filter((c): c is Insight => c !== null);
  // Newest first; the list order above breaks ties, so the same day reads in the same order every time.
  return cards
    .map((card, n) => ({ card, n }))
    .sort((a, b) => (a.card.date < b.card.date ? 1 : a.card.date > b.card.date ? -1 : a.n - b.n))
    .map(({ card }) => card)
    .slice(0, MAX_CARDS);
}
