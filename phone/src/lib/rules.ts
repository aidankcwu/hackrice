/**
 * The protocol's rules: one entry per rule, each with its window, what breaking
 * it does in plain words, its effect on the two ceilings, the sleep it costs
 * that night, when the effect lands, how fast it fades, and the studies behind
 * it (`sources.ts`). `evaluateDay` turns a day's events into findings, one per
 * thing worth drawing: green inside a window, red when outside, amber for
 * "watch this", grey for what was not the wearer's decision.
 *
 * Effect sizes are fractions of the ceiling lost at full strength (0.02 = 2 %).
 * Where a study gives the number the rule's `claim` is "measured"; where we
 * scale or convert it, "assumed", with the reasoning in `assumed`; protocol
 * defaults with no study are "none" and graded C.
 */
import type { Day, MonthEvent } from "./month/types";
import { sunTimes, uvIndex } from "./month/sun";
import type { Grade } from "./sources";

export type { Grade, Source } from "./sources";

export type RuleId =
  | "caffeine"
  | "sleep_debt"
  | "alcohol"
  | "exercise_timing"
  | "last_meal"
  | "screens"
  | "phone_in_bed"
  | "co2"
  | "sleep_regularity"
  | "social_jetlag"
  | "morning_light"
  | "nature"
  | "conversation"
  | "hydration"
  | "nap"
  | "sauna"
  | "postpartum"
  | "skipped_meal"
  | "sick"
  | "peptide"
  | "uv"
  | "eating_window"
  | "food_quality"
  | "sedentary"
  | "stress"
  | "air"
  | "sleep_deep"
  | "wake_anchor";

export interface Rule {
  id: RuleId;
  name: string;
  /** The window or target, in words. */
  window: string;
  /** What breaking it does, in plain words. */
  consequence: string;
  /** Fraction of each ceiling lost at full strength. */
  effect: { cognition: number; body: number };
  /** Minutes of sleep the rule costs that night when broken. */
  sleepMinutes: number;
  /** 0: the day it happens. 1: the next day (through tonight's sleep). */
  lands: 0 | 1;
  /** Days the effect takes to fade after it lands; 0 means that day only. */
  decayDays: number;
  /** The best source's grade. */
  grade: Grade;
  /** Keys into `SOURCES`. */
  sources: string[];
  /** How a number was scaled or converted, when it was. */
  assumed?: string;
  /** measured: a study gives the number. assumed: we scaled it. none: a protocol default. */
  claim: "measured" | "assumed" | "none";
  /** The one-line lever when this rule is the biggest cost. */
  lever: string;
}

/** Windows and targets, minutes after midnight. */
export const WINDOWS = {
  sleepStart: 22 * 60 + 30,
  wake: 6 * 60 + 30,
  windDown: 21 * 60 + 30,
  /** One coffee: 9 h before bed (Gardiner 2023). */
  caffeineEnd: 22 * 60 + 30 - 9 * 60,
  /** A double or an energy drink: 13 h before bed. */
  caffeineEndStrong: 22 * 60 + 30 - 13 * 60,
  eatingStart: 10 * 60,
  /** Last meal 3.5 h before bed (Gu 2020). */
  eatingEnd: 22 * 60 + 30 - 210,
  /** Vigorous exercise done 4 h before bed: green (Leota 2025). */
  moveBy: 22 * 60 + 30 - 4 * 60,
  /** Vigorous exercise ending 2 to 4 h before bed: amber. */
  moveAmberUntil: 22 * 60 + 30 - 2 * 60,
  /** Red from here; kept for old readers, equal to moveAmberUntil. */
  lateWorkout: 22 * 60 + 30 - 2 * 60,
  napStart: 13 * 60,
  napEnd: 15 * 60,
  napLate: 16 * 60,
  napMax: 20,
  saunaStart: 16 * 60,
  saunaEnd: 20 * 60 + 30,
  screensOff: 21 * 60 + 30,
  peptideAm: [7 * 60, 10 * 60] as const,
  peptidePm: [19 * 60, 22 * 60] as const,
  peopleBy: 21 * 60,
  waterBy: 18 * 60,
  daylightTarget: 60,
  peopleTarget: 30,
  waterTarget: 2000,
  sedentaryMax: 90,
  uvHigh: 8,
  uvMaxMinutes: 30,
  /** Indoor CO2, ppm (Allen 2016). */
  co2Amber: 900,
  co2Red: 1200,
  /** Bed within this many minutes of the target is green (Windred 2024). */
  regularityTolerance: 30,
  /** Social jetlag over this many minutes is amber (Roenneberg 2012). */
  socialJetlagAmber: 60,
} as const;

export const HOUSTON = { lat: 29.7604, lon: -95.3698, utcOffset: -5 } as const;

/** "8:42", "16:10". */
export function clock(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

const r = (rule: Rule): Rule => rule;

export const RULES: Record<RuleId, Rule> = {
  caffeine: r({
    id: "caffeine", name: "Caffeine cutoff",
    window: `One coffee by ${clock(WINDOWS.caffeineEnd)}, 9 h before bed; a double or an energy drink by ${clock(WINDOWS.caffeineEndStrong)}, 13 h before`,
    consequence: "Half of it is still in you at bedtime: about 45 minutes less sleep, and you won't feel it.",
    effect: { cognition: 0.03, body: 0.01 }, sleepMinutes: 45, lands: 1, decayDays: 1, grade: "A",
    sources: ["gardiner2023", "drake2013"], claim: "measured",
    assumed: "3% of cognition assumed from 45 min less sleep; Drake's unnoticed hour sets the tone",
    lever: `Caffeine before ${clock(WINDOWS.caffeineEnd)}`,
  }),
  sleep_debt: r({
    id: "sleep_debt", name: "Sleep debt",
    window: "7 h or more a night; debt is the hours under 8 summed over the last 14 nights",
    consequence: "You feel fine. Your reaction time says day N of short sleep.",
    effect: { cognition: 0.004, body: 0.002 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "A",
    sources: ["vandongen2003", "lim2010"], claim: "measured",
    assumed: "0.4% of cognition per hour of debt from the first hour, capped at 20 h: Van Dongen's 14 nights at 6 h (28 h of debt) match 1–2 nights of no sleep, Lim & Dinges' g≈0.7; grey under 2 h, amber to 6 h, red past it",
    lever: `In bed by ${clock(WINDOWS.sleepStart)}`,
  }),
  alcohol: r({
    id: "alcohol", name: "Alcohol", window: "None",
    consequence: "REM cut in the first half of tonight, the second half broken. Tomorrow, sober: attention, speed and memory all down; resting heart rate up about 2.6 bpm and HRV down about 3.5 ms per drink.",
    effect: { cognition: 0.02, body: 0.015 }, sleepMinutes: 0, lands: 1, decayDays: 2, grade: "A",
    sources: ["ebrahim2013", "gunn2018", "grosicki2026"], claim: "measured",
    assumed: "2% of cognition per drink assumed from Gunn's g≈0.5; the next day is measured, the day 2 residual is assumed; 2.6 bpm and 3.5 ms are the midpoints of Grosicki's 2.4–2.8 and 3.3–3.8, assumed",
    lever: "No drinks",
  }),
  exercise_timing: r({
    id: "exercise_timing", name: "Exercise timing",
    window: `Vigorous done by ${clock(WINDOWS.moveBy)}, 4 h before bed; ending ${clock(WINDOWS.moveBy)} to ${clock(WINDOWS.moveAmberUntil)} is watch; moderate any time`,
    consequence: "Vigorous exercise ending under 2 h before bed delays sleep onset about 36 min. Moderate evening exercise does not hurt sleep.",
    effect: { cognition: 0.02, body: 0.01 }, sleepMinutes: 36, lands: 1, decayDays: 1, grade: "A",
    sources: ["stutz2019", "leota2025", "chang2012"], claim: "measured",
    assumed: "2% of cognition assumed from 36 min later onset; the 2 to 4 h band counts half (18 min)",
    lever: `Vigorous training done by ${clock(WINDOWS.moveBy)}`,
  }),
  last_meal: r({
    id: "last_meal", name: "Last meal",
    window: `Last meal by ${clock(WINDOWS.eatingEnd)}, 3 to 4 h before bed`,
    consequence: "Digesting at bedtime: glucose runs about 18% higher through the night.",
    effect: { cognition: 0.01, body: 0.02 }, sleepMinutes: 0, lands: 1, decayDays: 1, grade: "B",
    sources: ["gu2020"], claim: "measured",
    assumed: "2% of body assumed from an 18% higher 4 h glucose exposure; fasting glucose was unchanged next morning, so nothing carries past the day",
    lever: `Last meal by ${clock(WINDOWS.eatingEnd)}`,
  }),
  screens: r({
    id: "screens", name: "Screens off",
    window: `Screens off from ${clock(WINDOWS.screensOff)}; daylight buys screen tolerance`,
    consequence: "Melatonin delayed about 1.5 h: sleep onset 10 min later, REM 12 min shorter.",
    effect: { cognition: 0.01, body: 0 }, sleepMinutes: 22, lands: 1, decayDays: 1, grade: "A",
    sources: ["chang2015", "han2024", "rangtell2016"], claim: "measured",
    assumed: "1% of cognition assumed from 22 min of sleep lost (onset +10, REM −12); 6.5 h of daytime light erased the effect",
    lever: `Screens off at ${clock(WINDOWS.screensOff)}`,
  }),
  phone_in_bed: r({
    id: "phone_in_bed", name: "Phone in bed", window: "Phone out of the bedroom",
    consequence: "Light at the eyes in bed. Sleep starts later, about 45 minutes less tonight.",
    effect: { cognition: 0.02, body: 0 }, sleepMinutes: 45, lands: 1, decayDays: 1, grade: "B",
    sources: ["chang2015"], claim: "assumed",
    assumed: "45 min assumed: an e-reader 4 h before bed cost 22 min; a lit phone in bed is taken as twice that",
    lever: "Phone out of the bedroom",
  }),
  co2: r({
    id: "co2", name: "Indoor CO2",
    window: `Room under ${WINDOWS.co2Amber} ppm; over ${WINDOWS.co2Red.toLocaleString("en-US")} red`,
    consequence: "Decisions run about 15% lower in this air. Open a window.",
    effect: { cognition: 0.02, body: 0 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "B",
    sources: ["allen2016", "satish2012"], claim: "measured",
    assumed: "2% of cognition assumed: Allen's −15% is a decision-test score for the hours in the room, not the day; red counts double",
    lever: "Open a window in the afternoon room",
  }),
  sleep_regularity: r({
    id: "sleep_regularity", name: "Sleep regularity",
    window: `In bed within ${WINDOWS.regularityTolerance} min of ${clock(WINDOWS.sleepStart)}; a weekly regularity index out of 100`,
    consequence: "Bedtime drifted. Irregular sleepers' melatonin onset runs 2.2 h later, and regularity predicts mortality better than duration.",
    effect: { cognition: 0.005, body: 0.005 }, sleepMinutes: 0, lands: 1, decayDays: 0, grade: "A",
    sources: ["windred2024", "phillips2017"], claim: "assumed",
    assumed: "0.5% assumed: the studies are long-run, no same-night number; the weekly index is our own bed/wake drift score against 240 min, not Windred's SRI",
    lever: `In bed by ${clock(WINDOWS.sleepStart)}`,
  }),
  social_jetlag: r({
    id: "social_jetlag", name: "Social jetlag",
    window: "Weekday and free-day midsleep within 1 h",
    consequence: "Each hour between weekday and free-day midsleep: 33% higher odds of overweight.",
    effect: { cognition: 0.005, body: 0.005 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "B",
    sources: ["roenneberg2012"], claim: "assumed",
    assumed: "0.5% assumed: the odds ratio is long-run, no same-day number",
    lever: "Same bed and wake time on free days",
  }),
  morning_light: r({
    id: "morning_light", name: "Morning light", window: "10 min outside within an hour of waking",
    consequence: "No light in the first hour. A week of natural light moves the body clock 2 h earlier; morning light shortens sleep onset.",
    effect: { cognition: 0.01, body: 0 }, sleepMinutes: 0, lands: 1, decayDays: 1, grade: "B",
    sources: ["wright2013", "figueiro2017"], claim: "assumed",
    assumed: "1% of cognition assumed from a 2 h clock shift over a week",
    lever: "Ten minutes outside after waking",
  }),
  nature: r({
    id: "nature", name: "Daylight and nature", window: "60 min outside by sunset (protocol default)",
    consequence: "Under an hour outside. 20 to 30 min outdoors drops cortisol about 21% an hour beyond its normal decline.",
    effect: { cognition: 0.01, body: 0.005 }, sleepMinutes: 0, lands: 1, decayDays: 1, grade: "B",
    sources: ["hunter2019", "rangtell2016"], claim: "assumed",
    assumed: "1% assumed: the 60 min target is a protocol default; Hunter's cortisol drop and Rångtell's screen tolerance are the reasons",
    lever: "An hour outside by sunset",
  }),
  conversation: r({
    id: "conversation", name: "People", window: `30 min face to face by ${clock(WINDOWS.peopleBy)}`,
    consequence: "Little time with people today. 10 min of talking lifts processing speed and working memory as much as a puzzle session.",
    effect: { cognition: 0.01, body: 0 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "B",
    sources: ["ybarra2008"], claim: "assumed",
    assumed: "1% of cognition assumed; Ybarra measured the lift, not the cost of its absence",
    lever: "Thirty minutes with someone",
  }),
  hydration: r({
    id: "hydration", name: "Water", window: `2 L by ${clock(WINDOWS.waterBy)}`,
    consequence: "Under 2 L by 18:00. At 1.4 to 1.6% body-water loss, fatigue, vigilance and mood all drop.",
    effect: { cognition: 0.01, body: 0.01 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "B",
    sources: ["armstrong2012", "ganio2011"], claim: "assumed",
    assumed: "1% assumed: 2 L is a protocol default, the 1.4–1.6% loss studies set the direction",
    lever: `2 L of water by ${clock(WINDOWS.waterBy)}`,
  }),
  nap: r({
    id: "nap", name: "Nap", window: `Nap ${clock(WINDOWS.napStart)} to ${clock(WINDOWS.napEnd)}, 20 min at most`,
    consequence: "After 16:00 it spends tonight's sleep pressure: about 30 min less sleep, assumed. Over 30 min at any hour: about 41% worse on waking, for 35–95 min.",
    effect: { cognition: 0.02, body: 0 }, sleepMinutes: 30, lands: 1, decayDays: 1, grade: "B",
    sources: ["brooks2006"], claim: "assumed",
    assumed: "30 min of sleep lost after a 16:00 nap is assumed; the 41% cost of a long nap is measured",
    lever: `Nap before ${clock(WINDOWS.napEnd)}, 20 min`,
  }),
  sauna: r({
    id: "sauna", name: "Sauna", window: `Sauna ${clock(WINDOWS.saunaStart)} to ${clock(WINDOWS.saunaEnd)} (protocol default); 4 to 7 sessions a week`,
    consequence: "Long-term only: 4 to 7 sessions a week vs 1, dementia hazard 0.34 over 20 years. No same-day claim.",
    effect: { cognition: 0, body: 0 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "B",
    sources: ["laukkanen2017"], claim: "measured",
    lever: "Sauna most days",
  }),
  postpartum: r({
    id: "postpartum", name: "Postpartum nights", window: "Not your decision",
    consequence: "Not your decision. Slowest reaction times stay worse for all 12 weeks and worsen from week 2 to 12 even as sleep improves.",
    effect: { cognition: 0.06, body: 0.03 }, sleepMinutes: 0, lands: 0, decayDays: 2, grade: "B",
    sources: ["insana2013"], claim: "measured",
    assumed: "6% of cognition per broken night assumed from Insana's slowest reaction times; fades over two days",
    lever: `Nap ${clock(WINDOWS.napStart)} to ${clock(WINDOWS.napEnd)} to make some of it back`,
  }),
  skipped_meal: r({
    id: "skipped_meal", name: "Skipped meal", window: "A meal seen inside the eating window",
    consequence: "Post-lunch dip is circadian and happens without lunch.",
    effect: { cognition: 0, body: 0 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: ["monk2005"], claim: "none",
    lever: "Eat lunch inside the window",
  }),
  sick: r({
    id: "sick", name: "Sick day", window: "Recovery day, protocol relaxed",
    consequence: "Recovery day. The protocol is relaxed and nothing is marked red.",
    effect: { cognition: 0.15, body: 0.25 }, sleepMinutes: 0, lands: 0, decayDays: 1, grade: "C",
    sources: [], claim: "none",
    lever: "Rest",
  }),
  peptide: r({
    id: "peptide", name: "Peptide doses", window: `AM ${clock(WINDOWS.peptideAm[0])} to ${clock(WINDOWS.peptideAm[1])}, PM ${clock(WINDOWS.peptidePm[0])} to ${clock(WINDOWS.peptidePm[1])} (protocol default)`,
    consequence: "A dose outside its window.",
    effect: { cognition: 0, body: 0.01 }, sleepMinutes: 0, lands: 0, decayDays: 1, grade: "C",
    sources: [], claim: "none",
    lever: "Take the dose inside its window",
  }),
  uv: r({
    id: "uv", name: "Midday sun", window: `Under ${WINDOWS.uvMaxMinutes} min in direct sun while the UV index is ${WINDOWS.uvHigh} or more (protocol default)`,
    consequence: "Long direct sun at a very high UV: skin damage adds up, and the heat load tires you by evening.",
    effect: { cognition: 0.01, body: 0.02 }, sleepMinutes: 0, lands: 0, decayDays: 1, grade: "C",
    sources: [], claim: "none",
    lever: "Shade between 11:00 and 16:00",
  }),
  eating_window: r({
    id: "eating_window", name: "Eating window", window: `First meal after ${clock(WINDOWS.eatingStart)} (protocol default)`,
    consequence: "The eating window opened early, so the overnight fast was short.",
    effect: { cognition: 0, body: 0.005 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: [], claim: "none",
    lever: `First meal after ${clock(WINDOWS.eatingStart)}`,
  }),
  food_quality: r({
    id: "food_quality", name: "Food quality", window: "Whole food, no fast food, sweets or ultra-processed (protocol default)",
    consequence: "Glucose spike, energy dip in about 2 h.",
    effect: { cognition: 0.01, body: 0.005 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: [], claim: "none",
    lever: "Swap the processed meal",
  }),
  sedentary: r({
    id: "sedentary", name: "Seated time", window: `Stand at least every ${WINDOWS.sedentaryMax} min (protocol default)`,
    consequence: "90 min without standing.",
    effect: { cognition: 0, body: 0.005 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: [], claim: "none",
    lever: `Stand every ${WINDOWS.sedentaryMax} min`,
  }),
  stress: r({
    id: "stress", name: "Stress while seated", window: "Heart rate under 1.4 times resting while seated (protocol default)",
    consequence: "Heart rate well above resting while seated.",
    effect: { cognition: 0.005, body: 0 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: [], claim: "none",
    lever: "Five slow breaths in the meeting",
  }),
  air: r({
    id: "air", name: "Air quality", window: "Outdoor time with AQI under 100 (protocol default)",
    consequence: "Bad air outside. Easier on the lungs indoors.",
    effect: { cognition: 0, body: 0.005 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: [], claim: "none",
    lever: "Move the walk indoors on bad-air days",
  }),
  sleep_deep: r({
    id: "sleep_deep", name: "Deep sleep", window: "75 min of deep sleep or more (protocol default)",
    consequence: "Little deep sleep: the body repairs less.",
    effect: { cognition: 0.005, body: 0.015 }, sleepMinutes: 0, lands: 0, decayDays: 1, grade: "C",
    sources: [], claim: "none",
    lever: `Caffeine before ${clock(WINDOWS.caffeineEnd)}`,
  }),
  wake_anchor: r({
    id: "wake_anchor", name: "Wake anchor", window: `Up within 30 min of ${clock(WINDOWS.wake)} (protocol default)`,
    consequence: "Wake time drifted. Tonight's sleep comes later.",
    effect: { cognition: 0.005, body: 0 }, sleepMinutes: 0, lands: 0, decayDays: 0, grade: "C",
    sources: [], claim: "none",
    lever: `Up at ${clock(WINDOWS.wake)}`,
  }),
};

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

/** inside: green. violation: red. watch: amber. neutral: grey, never scored red. */
export type Tone = "inside" | "violation" | "watch" | "neutral";

export interface Finding {
  rule: RuleId;
  /** The day the finding happened on. */
  date: string;
  /** The event it is about; absent for day-level findings (a target missed, last night). */
  eventId?: string;
  /** Minutes after midnight the marker sits at. */
  time: number;
  tone: Tone;
  /** The consequence line drawn beside the marker. */
  line: string;
  /** Scaled effect at full strength (drinks multiply alcohol). Zero for green. */
  cognition: number;
  body: number;
}

type Drink = "coffee" | "tea" | "energy_drink";
type Strength = "single" | "double";

const end = (e: { start: number; minutes: number }): number => e.start + e.minutes;
const DRINK_WORD: Record<Drink, string> = { coffee: "coffee", tea: "tea", energy_drink: "energy drink" };
const DOSE_WINDOW = { AM: WINDOWS.peptideAm, PM: WINDOWS.peptidePm } as const;
const SHORT_NIGHT = 7 * 60;
const FULL_NIGHT = 8 * 60;
const DEBT_CAP_HOURS = 20;
const mean = (xs: number[]): number => xs.reduce((a, b) => a + b, 0) / xs.length;

/** The last minute a drink can start: 9 h before bed, 13 h for an energy drink or a double. */
export function caffeineCutoff(drink: Drink, strength: Strength = "single"): number {
  return drink === "energy_drink" || strength === "double" ? WINDOWS.caffeineEndStrong : WINDOWS.caffeineEnd;
}

/** Hours under 8 h a night summed over the 14 nights ending this morning; never negative. */
export function sleepDebtHours(days: readonly Day[], index: number): number {
  let debt = 0;
  // An unrecorded night (minutes 0) is not a night of no sleep: it is skipped.
  for (let i = Math.max(0, index - 13); i <= index; i++) {
    if (days[i].sleep.minutes > 0) debt += Math.max(0, FULL_NIGHT - days[i].sleep.minutes);
  }
  return debt / 60;
}

/** N: consecutive recorded nights under 7 h ending this morning; an unrecorded night ends the run. */
export function shortSleepDay(days: readonly Day[], index: number): number {
  let n = 0;
  for (let i = index; i >= 0 && days[i].sleep.minutes > 0 && days[i].sleep.minutes < SHORT_NIGHT; i--) n += 1;
  return n;
}

/**
 * 0 to 100 over the last 7 nights: 100 when bed and wake never move. The mean
 * of |Δbed| + |Δwake| between consecutive nights, taken against 240 min.
 */
export function sleepRegularityIndex(days: readonly Day[], index: number): number {
  const from = Math.max(0, index - 6);
  if (index - from < 1) return 100;
  let drift = 0;
  for (let i = from + 1; i <= index; i++) {
    drift += Math.abs(days[i].sleep.bed - days[i - 1].sleep.bed) + Math.abs(days[i].sleep.wake - days[i - 1].sleep.wake);
  }
  return Math.round(100 * Math.max(0, Math.min(1, 1 - drift / (index - from) / 240)));
}

/** |midsleep on weekday nights − midsleep on free nights| over the last 7 nights; 0 until both kinds are seen. */
export function socialJetlagMinutes(days: readonly Day[], index: number): number {
  const week: number[] = [];
  const free: number[] = [];
  for (let i = Math.max(0, index - 6); i <= index; i++) {
    const { bed, wake } = days[i].sleep;
    const weekday = new Date(`${days[i].date}T12:00:00Z`).getUTCDay();
    (weekday === 0 || weekday === 6 ? free : week).push((bed + wake) / 2);
  }
  if (!week.length || !free.length) return 0;
  return Math.round(Math.abs(mean(week) - mean(free)));
}

/** Peptide doses taken inside their windows over the `span` days ending at `index`. */
export function peptideSchedule(days: readonly Day[], index: number, span = 14): { onSchedule: number; of: number } {
  const from = Math.max(0, index - span + 1);
  let onSchedule = 0;
  let of = 0;
  for (let i = from; i <= index; i++) {
    of += 1;
    const doses = days[i].events.filter((e) => e.kind === "peptide");
    const ok = (["AM", "PM"] as const).every((dose) =>
      doses.some((e) => e.kind === "peptide" && e.dose === dose && e.taken && e.start >= DOSE_WINDOW[dose][0] && e.start <= DOSE_WINDOW[dose][1]),
    );
    // Today's PM window may still be open: only the doses already due count.
    const today = i === index && days[i].until < DOSE_WINDOW.PM[1];
    const amOnly = doses.some((e) => e.kind === "peptide" && e.dose === "AM" && e.taken && e.start >= DOSE_WINDOW.AM[0] && e.start <= DOSE_WINDOW.AM[1]);
    if (today ? amOnly : ok) onSchedule += 1;
  }
  return { onSchedule, of };
}

function finding(rule: RuleId, date: string, time: number, tone: Tone, line: string, scale = 1, eventId?: string): Finding {
  // Grey is unscored except where the cost is real and not the wearer's call
  // (postpartum, sick) or continuous from zero (sleep debt).
  const scored = tone === "violation" || tone === "watch" || (tone === "neutral" && (rule === "postpartum" || rule === "sick" || rule === "sleep_debt"));
  const { cognition, body } = RULES[rule].effect;
  return {
    rule,
    date,
    eventId,
    time,
    tone,
    line,
    cognition: scored ? cognition * scale : 0,
    body: scored ? body * scale : 0,
  };
}

/**
 * Every finding for `days[index]`: one per event worth colouring, plus day-level
 * findings for last night's sleep, the debt behind it, and the targets
 * (daylight, people, water). On a sick day the protocol is relaxed: nothing is
 * red, one recovery finding carries the cost.
 */
export function evaluateDay(days: readonly Day[], index: number): Finding[] {
  const day = days[index];
  const next = days[index + 1];
  const { date, events, sleep } = day;
  const out: Finding[] = [];
  const add = (f: Finding) => out.push(f);
  const finished = day.until >= 1440;

  // Last night, landing on this day. When yesterday already has a red that
  // works through tonight's sleep (a late coffee, drinks, the phone in bed),
  // that red carries the cost; the thin night is drawn as its result. Ambers
  // (screens after 21:30, a workout in the 2 to 4 h band) do not count.
  const sleepEnd = sleep.wake;
  const cause = index > 0 ? sleepCause(days[index - 1]) : null;
  const sleepTone: Tone = cause ? "neutral" : "violation";
  const because = cause ? ` After ${cause}.` : "";
  const babyWakings = sleep.wakings.filter((w) => w.baby);
  if (babyWakings.length) {
    const awake = babyWakings.reduce((s, w) => s + w.minutes, 0);
    add(finding("postpartum", date, sleepEnd, "neutral", `Woken ${babyWakings.length} ${babyWakings.length === 1 ? "time" : "times"} by the baby, ${awake} min awake. Not your decision.`));
  }
  {
    const debt = sleepDebtHours(days, index);
    const n = shortSleepDay(days, index);
    // A sick day carries its own cost and marks nothing red. Otherwise the
    // debt is charged from the first hour, grey under 2 h.
    const tone: Tone = day.type === "sick" ? "neutral" : debt > 6 ? "violation" : debt >= 2 ? "watch" : "neutral";
    let line = `${hm(sleep.minutes)} of sleep; ${debt >= 1 / 60 ? `${hm(Math.round(debt * 60))} under 8 h over 14 nights` : "no debt over 14 nights"}.`;
    if (n >= 1 && cause) line += because;
    if (n >= 1) line += ` ${RULES.sleep_debt.consequence.replace(/\bN\b/, String(n))}`;
    add(finding("sleep_debt", date, sleepEnd, tone, line, day.type === "sick" ? 0 : Math.min(DEBT_CAP_HOURS, debt)));
  }

  if (day.type === "sick") {
    add(finding("sick", date, sleepEnd, "neutral", RULES.sick.consequence));
    return out.sort((a, b) => a.time - b.time);
  }

  if (sleep.deep < 75 && !sleep.fragmented) {
    add(finding("sleep_deep", date, sleepEnd, sleepTone, `${sleep.deep} min of deep sleep.${because} ${RULES.sleep_deep.consequence}`));
  }

  if (Math.abs(sleep.wake - WINDOWS.wake) > 30) {
    const drift = sleep.wake - WINDOWS.wake;
    add(finding("wake_anchor", date, sleep.wake, "watch", `Up at ${clock(sleep.wake)}, ${Math.abs(drift)} min ${drift < 0 ? "early" : "late"}. ${RULES.wake_anchor.consequence}`));
  }
  const jetlag = socialJetlagMinutes(days, index);
  if (jetlag > WINDOWS.socialJetlagAmber) {
    add(finding("social_jetlag", date, sleep.wake, "watch", `Midsleep ${hm(jetlag)} apart between weekdays and free days this week. ${RULES.social_jetlag.consequence}`));
  }

  // Tonight's bedtime, known from the next day's sleep.
  if (next) {
    const bed = 1440 + next.sleep.bed;
    const off = bed - WINDOWS.sleepStart;
    const regularity = sleepRegularityIndex(days, index + 1);
    const time = Math.min(1439, bed);
    if (Math.abs(off) <= WINDOWS.regularityTolerance) {
      add(finding("sleep_regularity", date, time, "inside", `In bed at ${clock(bed)}, within ${WINDOWS.regularityTolerance} min of ${clock(WINDOWS.sleepStart)}. Regularity this week ${regularity} of 100 (our own drift score).`));
    } else {
      add(finding("sleep_regularity", date, time, "watch", `In bed at ${clock(bed)}, ${hm(Math.abs(off))} ${off < 0 ? "early" : "late"}. ${RULES.sleep_regularity.consequence} Regularity this week ${regularity} of 100 (our own drift score).`));
    }
  }

  const meals = events.filter((e): e is Extract<MonthEvent, { kind: "meal" }> => e.kind === "meal");
  const firstMeal = meals[0];
  const sun = sunTimes(date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset);

  for (const e of events) {
    switch (e.kind) {
      case "caffeine": {
        const word = e.strength === "double" ? `double ${DRINK_WORD[e.drink]}` : DRINK_WORD[e.drink];
        const cutoff = caffeineCutoff(e.drink, e.strength);
        if (e.start <= cutoff) add(finding("caffeine", date, e.start, "inside", `${capital(word)} inside the window, ${beforeBed(e.start)} before bed.`, 1, e.id));
        else add(finding("caffeine", date, e.start, "violation", `${capital(word)} at ${clock(e.start)}, ${beforeBed(e.start)} before bed. ${RULES.caffeine.consequence}`, 1, e.id));
        break;
      }
      case "meal": {
        if (e.start > WINDOWS.eatingEnd) add(finding("last_meal", date, e.start, "violation", `Meal at ${clock(e.start)}, ${beforeBed(e.start)} before bed. ${RULES.last_meal.consequence}`, 1, e.id));
        else if (e === firstMeal && e.start < WINDOWS.eatingStart) add(finding("eating_window", date, e.start, "watch", `First meal at ${clock(e.start)}. ${RULES.eating_window.consequence}`, 1, e.id));
        else add(finding("last_meal", date, e.start, "inside", "Inside the eating window.", 1, e.id));
        if (e.food !== "whole") add(finding("food_quality", date, e.start, "watch", RULES.food_quality.consequence, 1, e.id));
        break;
      }
      case "skipped_meal":
        add(finding("skipped_meal", date, e.start, "neutral", `No ${e.meal} seen in the window. ${RULES.skipped_meal.consequence}`, 1, e.id));
        break;
      case "alcohol":
        add(finding("alcohol", date, e.start, "violation", RULES.alcohol.consequence, e.drinks, e.id));
        break;
      case "workout": {
        const before = beforeBed(end(e));
        if (!e.vigorous) add(finding("exercise_timing", date, e.start, "inside", `${e.minutes} min of moderate exercise; evening or not, it does not hurt sleep. A small lift in executive function after.`, 1, e.id));
        else if (end(e) <= WINDOWS.moveBy) add(finding("exercise_timing", date, e.start, "inside", `Vigorous, done ${before} before bed. A small lift in executive function after.`, 1, e.id));
        else if (end(e) <= WINDOWS.moveAmberUntil) add(finding("exercise_timing", date, e.start, "watch", `Vigorous, done ${before} before bed. Inside 4 h of bed: sleep may start up to 18 min later, assumed.`, 0.5, e.id));
        else add(finding("exercise_timing", date, e.start, "violation", `Vigorous, done ${before} before bed. ${RULES.exercise_timing.consequence}`, 1, e.id));
        break;
      }
      case "outdoor": {
        const uv = e.sunlight ? peakUv(date, e.start, end(e)) : 0;
        if (e.minutes >= WINDOWS.uvMaxMinutes && uv >= WINDOWS.uvHigh) {
          add(finding("uv", date, e.start, "violation", `${e.minutes} min in direct sun at UV ${uv}, a clear-sky estimate. ${RULES.uv.consequence}`, 1, e.id));
        }
        if (day.aqi > 100) add(finding("air", date, e.start, "watch", `Air at AQI ${day.aqi}. ${RULES.air.consequence}`, 1, e.id));
        else add(finding("nature", date, e.start, "inside", e.sunlight ? `${e.minutes} min of daylight.` : `${e.minutes} min outside, no direct sun.`, 1, e.id));
        break;
      }
      case "sedentary":
        if (e.minutes >= WINDOWS.sedentaryMax) add(finding("sedentary", date, e.start, "watch", `${e.minutes} min without standing.`, 1, e.id));
        break;
      case "screen":
        if (end(e) > WINDOWS.screensOff) add(finding("screens", date, Math.max(e.start, WINDOWS.screensOff), "watch", `${capital(e.device)} after ${clock(WINDOWS.screensOff)}. ${RULES.screens.consequence} Daylight buys screen tolerance.`, 1, e.id));
        break;
      case "phone_in_bed":
        add(finding("phone_in_bed", date, e.start, "violation", `Phone in hand in bed, ${e.minutes} min. ${RULES.phone_in_bed.consequence}`, 1, e.id));
        break;
      case "co2": {
        const ppm = e.ppm.toLocaleString("en-US");
        if (e.ppm > WINDOWS.co2Red) add(finding("co2", date, e.start, "violation", `${e.label}, ${ppm} ppm for ${hm(e.minutes)}. Decisions run 15 to 50% lower in this air (−15% at 945 ppm, −50% at 1,400). Open a window.`, 2, e.id));
        else if (e.ppm > WINDOWS.co2Amber) add(finding("co2", date, e.start, "watch", `${e.label}, ${ppm} ppm for ${hm(e.minutes)}. ${RULES.co2.consequence}`, 1, e.id));
        else add(finding("co2", date, e.start, "inside", `${e.label}, ${ppm} ppm. Fresh enough.`, 1, e.id));
        break;
      }
      case "nap":
        if (e.start >= WINDOWS.napLate) add(finding("nap", date, e.start, "violation", `${e.minutes} min nap at ${clock(e.start)}. After 16:00 it spends tonight's sleep pressure: about 30 min less sleep, assumed.`, 1, e.id));
        else if (e.minutes > 30) add(finding("nap", date, e.start, "watch", `${e.minutes} min nap at ${clock(e.start)}. Over 30 min: about 41% worse on waking, for 35–95 min.`, 0.25, e.id));
        else if (e.start >= WINDOWS.napStart && end(e) <= WINDOWS.napEnd && e.minutes <= WINDOWS.napMax) add(finding("nap", date, e.start, "inside", `${e.minutes} min, inside the nap window. Good for about 2.5 h, no grogginess.`, 1, e.id));
        else add(finding("nap", date, e.start, "inside", `${e.minutes} min nap at ${clock(e.start)}, under 30 min.`, 1, e.id));
        break;
      case "sauna":
        add(finding("sauna", date, e.start, e.start >= WINDOWS.saunaStart && e.start <= WINDOWS.saunaEnd ? "inside" : "watch", `Sauna, ${e.minutes} min. Long-term only: no same-day claim.`, 1, e.id));
        break;
      case "stress":
        add(finding("stress", date, e.start, "watch", `Heart rate ${e.hr} in a ${e.scene.toLowerCase()}, ${(e.hr / e.resting).toFixed(1)} times resting.`, 1, e.id));
        break;
      case "peptide": {
        const [lo, hi] = DOSE_WINDOW[e.dose];
        const { onSchedule, of } = peptideSchedule(days, index);
        if (e.taken && e.start >= lo && e.start <= hi) add(finding("peptide", date, e.start, "inside", `${e.dose} dose taken ${clock(e.start)}. ${onSchedule} of ${of} days on schedule.`, 1, e.id));
        else if (e.taken) add(finding("peptide", date, e.start, "watch", `${e.dose} dose at ${clock(e.start)}, outside its window.`, 0.5, e.id));
        else add(finding("peptide", date, e.start, "violation", `Missed ${e.dose} dose. ${onSchedule} of ${of} days on schedule.`, 1, e.id));
        break;
      }
      case "supplements": {
        const withMeal = firstMeal && Math.abs(e.start - firstMeal.start) <= 30;
        add(finding("eating_window", date, e.start, withMeal ? "inside" : "watch", withMeal ? "With the first meal." : "Not with a meal.", 0, e.id));
        break;
      }
      default:
        break;
    }
  }

  // Targets for the day. Today is only judged on what is already due.
  const wakeHour = sleep.wake + 60;
  const morning = events.some((e) => e.kind === "outdoor" && e.sunlight && e.start <= wakeHour && e.minutes >= 10);
  if (!morning && day.until > wakeHour) {
    add(finding("morning_light", date, wakeHour, "watch", RULES.morning_light.consequence));
  }
  const daylight = daylightMinutes(day, sun.sunset);
  if ((finished || day.until >= sun.sunset) && daylight < WINDOWS.daylightTarget) {
    add(finding("nature", date, sun.sunset, "watch", `${daylight} of ${WINDOWS.daylightTarget} min outside by sunset. ${RULES.nature.consequence}`));
  }
  const people = peopleMinutes(day);
  if (day.until >= WINDOWS.peopleBy && people < WINDOWS.peopleTarget) {
    add(finding("conversation", date, WINDOWS.peopleBy, "watch", `${people} of ${WINDOWS.peopleTarget} min face to face. ${RULES.conversation.consequence}`));
  }
  const water = waterMl(day);
  if (day.until >= WINDOWS.waterBy && water < WINDOWS.waterTarget) {
    add(finding("hydration", date, WINDOWS.waterBy, "watch", `${(water / 1000).toFixed(1)} of 2 L by ${clock(WINDOWS.waterBy)}. ${RULES.hydration.consequence}`));
  }

  return out.sort((a, b) => a.time - b.time);
}

/**
 * The red on `day` that works through the following night's sleep, in words
 * ("yesterday's 16:10 coffee"), or null when the day had none. Reds only:
 * screens after 21:30 and a workout in the 2 to 4 h band are ambers and do
 * not count, so the night after them is judged on its own.
 */
export function sleepCause(day: Day): string | null {
  for (const e of [...day.events].reverse()) {
    if (e.kind === "alcohol") return "yesterday's drinks";
    if (e.kind === "phone_in_bed") return "the phone in bed";
    if (e.kind === "caffeine" && e.start > caffeineCutoff(e.drink, e.strength)) return `yesterday's ${clock(e.start)} ${DRINK_WORD[e.drink]}`;
    if (e.kind === "meal" && e.start > WINDOWS.eatingEnd) return `yesterday's ${clock(e.start)} dinner`;
    if (e.kind === "workout" && e.vigorous && end(e) > WINDOWS.moveAmberUntil) return `yesterday's ${clock(e.start)} workout`;
    if (e.kind === "nap" && e.start >= WINDOWS.napLate) return `yesterday's ${clock(e.start)} nap`;
  }
  return null;
}

/** The highest clear-sky UV index over [from, to), rounded; checked every 5 min. */
export function peakUv(date: string, from: number, to: number): number {
  let peak = 0;
  for (let t = from; t < to; t += 5) peak = Math.max(peak, uvIndex(date, t, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset));
  return Math.round(peak);
}

/** Minutes of outdoor sunlight before sunset. */
export function daylightMinutes(day: Day, sunset: number): number {
  let total = 0;
  for (const e of day.events) {
    if (e.kind !== "outdoor" || !e.sunlight) continue;
    total += Math.max(0, Math.min(end(e), sunset) - e.start);
  }
  return total;
}

/** Minutes face to face by 21:00. */
export function peopleMinutes(day: Day): number {
  let total = 0;
  for (const e of day.events) {
    if (e.kind !== "conversation") continue;
    total += Math.max(0, Math.min(end(e), WINDOWS.peopleBy) - e.start);
  }
  return total;
}

/** Water seen by 18:00, ml. */
export function waterMl(day: Day): number {
  return day.events.reduce((sum, e) => (e.kind === "water" && e.start <= WINDOWS.waterBy ? sum + e.ml : sum), 0);
}

/** "7 h 10 min". */
export function hm(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "9 h 15 min" before the bed target; never negative. */
function beforeBed(minute: number): string {
  return hm(Math.max(0, WINDOWS.sleepStart - minute));
}
