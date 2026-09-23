/**
 * The protocol's rules: one entry per rule, each with its window, what breaking
 * it does in plain words, its effect on the two ceilings, when the effect lands
 * and how fast it fades. `evaluateDay` turns a day's events into findings, one
 * per thing worth drawing: green inside a window, red when outside, amber for
 * "watch this", grey for what was not the wearer's decision.
 *
 * Effect sizes are fractions of the ceiling lost at full strength (0.04 = 4 %).
 * They are working estimates, not measurements: every rule's `source` is
 * "to verify" until each number is traced to a study.
 */
import type { Day, MonthEvent } from "./month/types";
import { sunTimes } from "./month/sun";

export type RuleId =
  | "caffeine"
  | "movement"
  | "last_meal"
  | "eating_window"
  | "food_quality"
  | "skipped_meal"
  | "alcohol"
  | "nicotine"
  | "screens"
  | "phone_in_bed"
  | "nap"
  | "sedentary"
  | "sauna_cold"
  | "stress"
  | "air"
  | "peptide"
  | "sleep_window"
  | "sleep_short"
  | "sleep_fragmented"
  | "sleep_deep"
  | "wake_anchor"
  | "morning_light"
  | "daylight"
  | "people"
  | "water"
  | "sick";

export interface Rule {
  id: RuleId;
  name: string;
  /** The window or target, in words. */
  window: string;
  /** What breaking it does, in plain words. */
  consequence: string;
  /** Fraction of each ceiling lost at full strength. */
  effect: { cognition: number; body: number };
  /** 0: the day it happens. 1: the next day (through tonight's sleep). */
  lands: 0 | 1;
  /** Days the effect takes to fade after it lands; 0 means that day only. */
  decayDays: number;
  /** The one-line lever when this rule is the biggest cost. */
  lever: string;
  source: "to verify";
}

const r = (rule: Rule): Rule => rule;

export const RULES: Record<RuleId, Rule> = {
  caffeine: r({
    id: "caffeine", name: "Caffeine cutoff", window: "Caffeine ok 6:30 to 12:30, 10 hours before bed",
    consequence: "About half of an afternoon coffee is still in you at bedtime. Lighter sleep tonight, about 4% off tomorrow's cognition.",
    effect: { cognition: 0.04, body: 0.01 }, lands: 1, decayDays: 1, lever: "Caffeine before 12:30", source: "to verify",
  }),
  movement: r({
    id: "movement", name: "Movement cutoff", window: "30 to 60 min of movement, anything vigorous done by 16:30",
    consequence: "Core temperature and adrenaline still up at bedtime. Later sleep onset.",
    effect: { cognition: 0.02, body: 0.02 }, lands: 1, decayDays: 1, lever: "Train before 16:30", source: "to verify",
  }),
  last_meal: r({
    id: "last_meal", name: "Last meal", window: "Last meal by 18:30, 4 hours before bed",
    consequence: "Digesting at bedtime: warmer core, less deep sleep.",
    effect: { cognition: 0.02, body: 0.02 }, lands: 1, decayDays: 1, lever: "Last meal by 18:30", source: "to verify",
  }),
  eating_window: r({
    id: "eating_window", name: "Eating window", window: "First meal after 10:00",
    consequence: "The eating window opened early, so the overnight fast was short.",
    effect: { cognition: 0, body: 0.01 }, lands: 0, decayDays: 0, lever: "First meal after 10:00", source: "to verify",
  }),
  food_quality: r({
    id: "food_quality", name: "Food quality", window: "Whole food, no fast food, sweets or ultra-processed",
    consequence: "Glucose spike, energy dip in about 2 h.",
    effect: { cognition: 0.015, body: 0.005 }, lands: 0, decayDays: 0, lever: "Swap the processed meal", source: "to verify",
  }),
  skipped_meal: r({
    id: "skipped_meal", name: "Skipped meal", window: "A meal seen inside the eating window",
    consequence: "Attention and mood dip mid-afternoon; bigger dinner tonight.",
    effect: { cognition: 0.05, body: 0.02 }, lands: 0, decayDays: 0, lever: "Eat lunch inside the window", source: "to verify",
  }),
  alcohol: r({
    id: "alcohol", name: "Alcohol", window: "None",
    consequence: "REM cut by about a third tonight. Fog tomorrow, some the day after.",
    effect: { cognition: 0.03, body: 0.02 }, lands: 1, decayDays: 2, lever: "No drinks", source: "to verify",
  }),
  nicotine: r({
    id: "nicotine", name: "Nicotine", window: "None",
    consequence: "Heart rate up and deep sleep cut for hours.",
    effect: { cognition: 0.01, body: 0.02 }, lands: 0, decayDays: 1, lever: "No nicotine", source: "to verify",
  }),
  screens: r({
    id: "screens", name: "Screens off", window: "Screens off from 21:30",
    consequence: "Melatonin delayed about 30 min.",
    effect: { cognition: 0.02, body: 0 }, lands: 1, decayDays: 1, lever: "Screens off at 21:30", source: "to verify",
  }),
  phone_in_bed: r({
    id: "phone_in_bed", name: "Phone in bed", window: "Phone out of the bedroom",
    consequence: "Light at the eyes in bed. Sleep starts later.",
    effect: { cognition: 0.02, body: 0 }, lands: 1, decayDays: 1, lever: "Phone out of the bedroom", source: "to verify",
  }),
  nap: r({
    id: "nap", name: "Nap", window: "Nap ok 13:00 to 15:00, 20 min at most",
    consequence: "Sleep pressure spent. Harder to fall asleep tonight.",
    effect: { cognition: 0.02, body: 0 }, lands: 1, decayDays: 1, lever: "Nap before 15:00, 20 min", source: "to verify",
  }),
  sedentary: r({
    id: "sedentary", name: "Seated time", window: "Stand at least every 90 min",
    consequence: "90 min without standing.",
    effect: { cognition: 0, body: 0.01 }, lands: 0, decayDays: 0, lever: "Stand every 90 min", source: "to verify",
  }),
  sauna_cold: r({
    id: "sauna_cold", name: "Sauna and cold", window: "Sauna or cold 16:00 to 20:30",
    consequence: "Cold this close to bed keeps you alert.",
    effect: { cognition: 0.005, body: 0 }, lands: 1, decayDays: 1, lever: "Cold before 20:30", source: "to verify",
  }),
  stress: r({
    id: "stress", name: "Stress while seated", window: "Heart rate under 1.4 times resting while seated",
    consequence: "Heart rate well above resting while seated.",
    effect: { cognition: 0.005, body: 0 }, lands: 0, decayDays: 0, lever: "Five slow breaths in the meeting", source: "to verify",
  }),
  air: r({
    id: "air", name: "Air quality", window: "Outdoor time with AQI under 100",
    consequence: "Bad air outside. Easier on the lungs indoors.",
    effect: { cognition: 0, body: 0.005 }, lands: 0, decayDays: 0, lever: "Move the walk indoors on bad-air days", source: "to verify",
  }),
  peptide: r({
    id: "peptide", name: "Peptide doses", window: "AM 7:00 to 10:00, PM 19:00 to 22:00",
    consequence: "A dose outside its window.",
    effect: { cognition: 0, body: 0.015 }, lands: 0, decayDays: 1, lever: "Take the dose inside its window", source: "to verify",
  }),
  sleep_window: r({
    id: "sleep_window", name: "Sleep window", window: "In bed by 22:30, up by 6:30",
    consequence: "In bed after the window: less deep sleep before the alarm.",
    effect: { cognition: 0, body: 0 }, lands: 1, decayDays: 0, lever: "In bed by 22:30", source: "to verify",
  }),
  sleep_short: r({
    id: "sleep_short", name: "Sleep length", window: "7 hours or more",
    consequence: "Short sleep: slower reactions and a thinner mood today.",
    effect: { cognition: 0.015, body: 0.01 }, lands: 0, decayDays: 1, lever: "In bed by 22:30", source: "to verify",
  }),
  sleep_fragmented: r({
    id: "sleep_fragmented", name: "Broken sleep", window: "Unbroken sleep",
    consequence: "Broken sleep: not your decision, still a cost today and some tomorrow.",
    effect: { cognition: 0.06, body: 0.03 }, lands: 0, decayDays: 2, lever: "Nap 13:00 to 15:00 to make some of it back", source: "to verify",
  }),
  sleep_deep: r({
    id: "sleep_deep", name: "Deep sleep", window: "75 min of deep sleep or more",
    consequence: "Little deep sleep: the body repairs less.",
    effect: { cognition: 0.01, body: 0.03 }, lands: 0, decayDays: 1, lever: "Caffeine before 12:30", source: "to verify",
  }),
  wake_anchor: r({
    id: "wake_anchor", name: "Wake anchor", window: "Up within 30 min of 6:30",
    consequence: "Wake time drifted. Tonight's sleep comes later.",
    effect: { cognition: 0.01, body: 0 }, lands: 0, decayDays: 0, lever: "Up at 6:30", source: "to verify",
  }),
  morning_light: r({
    id: "morning_light", name: "Morning light", window: "10 min outside within an hour of waking",
    consequence: "No light in the first hour. The body clock starts late.",
    effect: { cognition: 0.01, body: 0 }, lands: 1, decayDays: 1, lever: "Ten minutes outside after waking", source: "to verify",
  }),
  daylight: r({
    id: "daylight", name: "Daylight", window: "60 min of daylight by sunset",
    consequence: "Too little daylight: a weaker body clock tonight.",
    effect: { cognition: 0.01, body: 0.005 }, lands: 1, decayDays: 1, lever: "An hour outside by sunset", source: "to verify",
  }),
  people: r({
    id: "people", name: "People", window: "30 min face to face by 21:00",
    consequence: "Little time with people today.",
    effect: { cognition: 0.01, body: 0 }, lands: 0, decayDays: 0, lever: "Thirty minutes with someone", source: "to verify",
  }),
  water: r({
    id: "water", name: "Water", window: "2 L by 18:00",
    consequence: "Under 2 L by 18:00: headaches and a slower afternoon.",
    effect: { cognition: 0.01, body: 0.01 }, lands: 0, decayDays: 0, lever: "2 L of water by 18:00", source: "to verify",
  }),
  sick: r({
    id: "sick", name: "Sick day", window: "Recovery day, protocol relaxed",
    consequence: "Recovery day. The protocol is relaxed and nothing is marked red.",
    effect: { cognition: 0.15, body: 0.25 }, lands: 0, decayDays: 1, lever: "Rest", source: "to verify",
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

/** Windows and targets, minutes after midnight. */
export const WINDOWS = {
  sleepStart: 22 * 60 + 30,
  wake: 6 * 60 + 30,
  windDown: 21 * 60 + 30,
  caffeineEnd: 12 * 60 + 30,
  eatingStart: 10 * 60,
  eatingEnd: 18 * 60 + 30,
  moveBy: 16 * 60 + 30,
  lateWorkout: 19 * 60,
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
} as const;

export const HOUSTON = { lat: 29.7604, lon: -95.3698, utcOffset: -5 } as const;

/** "8:42", "16:10". */
export function clock(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

const pct = (x: number): string => `${Math.round(x * 100)}%`;
const end = (e: { start: number; minutes: number }): number => e.start + e.minutes;
const DRINK_WORD: Record<"coffee" | "tea" | "energy_drink", string> = { coffee: "coffee", tea: "tea", energy_drink: "energy drink" };
const DOSE_WINDOW = { AM: WINDOWS.peptideAm, PM: WINDOWS.peptidePm } as const;

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
  const scored = tone === "violation" || tone === "watch" || (tone === "neutral" && (rule === "sleep_fragmented" || rule === "sick"));
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
 * findings for last night's sleep and the targets (daylight, people, water).
 * On a sick day the protocol is relaxed: nothing is red, one recovery finding
 * carries the cost.
 */
export function evaluateDay(days: readonly Day[], index: number): Finding[] {
  const day = days[index];
  const next = days[index + 1];
  const { date, events, sleep } = day;
  const out: Finding[] = [];
  const add = (f: Finding) => out.push(f);
  const finished = day.until >= 1440;

  // Last night, landing on this day. When yesterday already has a red that
  // works through tonight's sleep (a late coffee, drinks, screens in bed), that
  // red carries the cost; the thin night is drawn as its result, not scored again.
  const sleepEnd = sleep.wake;
  const cause = index > 0 ? sleepCause(days[index - 1]) : null;
  const sleepTone: Tone = cause ? "neutral" : "violation";
  const because = cause ? ` After ${cause}.` : "";
  if (sleep.fragmented) {
    const awake = sleep.wakings.reduce((s, w) => s + w.minutes, 0);
    add(finding("sleep_fragmented", date, sleepEnd, "neutral", `Woken ${sleep.wakings.length} times, ${awake} min awake. Not your decision.`));
  } else if (sleep.minutes < 7 * 60) {
    const short = 7 * 60 - sleep.minutes;
    add(finding("sleep_short", date, sleepEnd, sleepTone, `${hm(sleep.minutes)} of sleep.${because} ${RULES.sleep_short.consequence}`, Math.min(6, short / 30)));
  }
  if (sleep.deep < 75 && !sleep.fragmented) {
    add(finding("sleep_deep", date, sleepEnd, sleepTone, `${sleep.deep} min of deep sleep.${because} ${RULES.sleep_deep.consequence}`));
  }

  if (day.type === "sick") {
    add(finding("sick", date, sleepEnd, "neutral", RULES.sick.consequence));
    return out.sort((a, b) => a.time - b.time);
  }

  if (Math.abs(sleep.wake - WINDOWS.wake) > 30) {
    const drift = sleep.wake - WINDOWS.wake;
    add(finding("wake_anchor", date, sleep.wake, "watch", `Up at ${clock(sleep.wake)}, ${Math.abs(drift)} min ${drift < 0 ? "early" : "late"}. ${RULES.wake_anchor.consequence}`));
  }

  // Tonight's bedtime, known from the next day's sleep.
  if (next && next.sleep.bed > WINDOWS.sleepStart - 1440 + 30) {
    const bed = next.sleep.bed;
    add(finding("sleep_window", date, Math.min(1439, 1440 + bed), "violation", `In bed at ${clock(1440 + bed)}. ${RULES.sleep_window.consequence}`));
  }

  const meals = events.filter((e): e is Extract<MonthEvent, { kind: "meal" }> => e.kind === "meal");
  const firstMeal = meals[0];
  const sun = sunTimes(date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset);

  for (const e of events) {
    switch (e.kind) {
      case "caffeine": {
        const word = DRINK_WORD[e.drink];
        if (e.start <= WINDOWS.caffeineEnd) add(finding("caffeine", date, e.start, "inside", `${capital(word)} inside the window.`, 1, e.id));
        else add(finding("caffeine", date, e.start, "violation", `About half of a ${clock(e.start)} ${word} is still in you at 22:30. Lighter sleep tonight, about ${pct(RULES.caffeine.effect.cognition)} off tomorrow's cognition.`, 1, e.id));
        break;
      }
      case "meal": {
        if (e.start > WINDOWS.eatingEnd) add(finding("last_meal", date, e.start, "violation", RULES.last_meal.consequence, 1, e.id));
        else if (e === firstMeal && e.start < WINDOWS.eatingStart) add(finding("eating_window", date, e.start, "watch", `First meal at ${clock(e.start)}. ${RULES.eating_window.consequence}`, 1, e.id));
        else add(finding("last_meal", date, e.start, "inside", "Inside the eating window.", 1, e.id));
        if (e.food !== "whole") add(finding("food_quality", date, e.start, "watch", RULES.food_quality.consequence, 1, e.id));
        break;
      }
      case "skipped_meal":
        add(finding("skipped_meal", date, e.start, "violation", `No ${e.meal} seen in the window. ${RULES.skipped_meal.consequence}`, 1, e.id));
        break;
      case "alcohol":
        add(finding("alcohol", date, e.start, "violation", RULES.alcohol.consequence, e.drinks, e.id));
        break;
      case "nicotine":
        add(finding("nicotine", date, e.start, "violation", RULES.nicotine.consequence, 1, e.id));
        break;
      case "workout": {
        if (end(e) <= WINDOWS.moveBy || !e.vigorous) add(finding("movement", date, e.start, "inside", "Moved inside the window.", 1, e.id));
        else if (e.start >= WINDOWS.lateWorkout) add(finding("movement", date, e.start, "violation", RULES.movement.consequence, 1, e.id));
        else add(finding("movement", date, e.start, "watch", "Ends after 16:30. Sleep may come a little later.", 0.5, e.id));
        break;
      }
      case "outdoor":
        if (day.aqi > 100) add(finding("air", date, e.start, "watch", `Air at AQI ${day.aqi}. ${RULES.air.consequence}`, 1, e.id));
        else add(finding("daylight", date, e.start, "inside", e.sunlight ? `${e.minutes} min of daylight.` : `${e.minutes} min outside, no direct sun.`, 1, e.id));
        break;
      case "sedentary":
        if (e.minutes >= WINDOWS.sedentaryMax) add(finding("sedentary", date, e.start, "watch", `${e.minutes} min without standing.`, 1, e.id));
        break;
      case "screen":
        if (end(e) > WINDOWS.screensOff) add(finding("screens", date, Math.max(e.start, WINDOWS.screensOff), "violation", `${capital(e.device)} after 21:30. ${RULES.screens.consequence}`, 1, e.id));
        break;
      case "phone_in_bed":
        add(finding("phone_in_bed", date, e.start, "violation", `Phone in hand in bed, ${e.minutes} min. ${RULES.phone_in_bed.consequence}`, 1, e.id));
        break;
      case "nap":
        if (e.start >= WINDOWS.napLate) add(finding("nap", date, e.start, "violation", `${e.minutes} min nap at ${clock(e.start)}. ${RULES.nap.consequence}`, 1, e.id));
        else if (e.start >= WINDOWS.napStart && end(e) <= WINDOWS.napEnd && e.minutes <= WINDOWS.napMax) add(finding("nap", date, e.start, "inside", `${e.minutes} min, inside the nap window.`, 1, e.id));
        else add(finding("nap", date, e.start, "watch", `${e.minutes} min nap. Longer than 20 min leaves you groggy.`, 0.25, e.id));
        break;
      case "sauna":
        add(finding("sauna_cold", date, e.start, e.start >= WINDOWS.saunaStart && e.start <= WINDOWS.saunaEnd ? "inside" : "watch", `Sauna, ${e.minutes} min.`, 1, e.id));
        break;
      case "cold": {
        const bed = next ? 1440 + next.sleep.bed : WINDOWS.sleepStart;
        if (bed - e.start < 120) add(finding("sauna_cold", date, e.start, "watch", RULES.sauna_cold.consequence, 1, e.id));
        else add(finding("sauna_cold", date, e.start, "inside", `Cold plunge, ${e.minutes} min.`, 1, e.id));
        break;
      }
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
    add(finding("daylight", date, sun.sunset, "watch", `${daylight} of 60 min of daylight by sunset. ${RULES.daylight.consequence}`));
  }
  const people = peopleMinutes(day);
  if (day.until >= WINDOWS.peopleBy && people < WINDOWS.peopleTarget) {
    add(finding("people", date, WINDOWS.peopleBy, "watch", `${people} of 30 min face to face. ${RULES.people.consequence}`));
  }
  const water = waterMl(day);
  if (day.until >= WINDOWS.waterBy && water < WINDOWS.waterTarget) {
    add(finding("water", date, WINDOWS.waterBy, "watch", `${(water / 1000).toFixed(1)} of 2 L by 18:00. ${RULES.water.consequence}`));
  }

  return out.sort((a, b) => a.time - b.time);
}

/**
 * The red on `day` that works through the following night's sleep, in words
 * ("yesterday's 16:10 coffee"), or null when the day had none.
 */
export function sleepCause(day: Day): string | null {
  for (const e of [...day.events].reverse()) {
    if (e.kind === "alcohol") return "yesterday's drinks";
    if (e.kind === "phone_in_bed") return "the phone in bed";
    if (e.kind === "caffeine" && e.start > WINDOWS.caffeineEnd) return `yesterday's ${clock(e.start)} ${DRINK_WORD[e.drink]}`;
    if (e.kind === "screen" && end(e) > WINDOWS.screensOff) return "screens after 21:30";
    if (e.kind === "meal" && e.start > WINDOWS.eatingEnd) return `yesterday's ${clock(e.start)} dinner`;
    if (e.kind === "workout" && e.vigorous && end(e) > WINDOWS.moveBy) return `yesterday's ${clock(e.start)} workout`;
    if (e.kind === "nap" && e.start >= WINDOWS.napLate) return `yesterday's ${clock(e.start)} nap`;
  }
  return null;
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
  return m ? `${h} h ${m} min` : `${h} h`;
}

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
