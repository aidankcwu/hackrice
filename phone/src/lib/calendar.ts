/**
 * What the Calendar draws for a day, as data. Seven lanes, each carrying its
 * protocol windows as segments: green when the rule was met, grey when not (or
 * not yet), with a red X at the window's end when it was missed without a
 * violation. A few ticks for what happened inside the lanes (meals, outdoor
 * minutes, doses, night wakings), and every violation as one red bar across
 * all lanes at its time. Big things only. No React here.
 */
import type { Day, MonthEvent } from "./month/types";
import { sunTimes } from "./month/sun";
import { HOUSTON, WINDOWS, caffeineCutoff, clock, daylightMinutes, hm, peakUv, type Finding, type RuleId } from "./rules";

export type LaneId = "sleep" | "light" | "caffeine" | "food" | "move" | "screens" | "peptide";

export const LANES: readonly { id: LaneId; name: string }[] = [
  { id: "sleep", name: "Sleep" },
  { id: "light", name: "Light" },
  { id: "caffeine", name: "Caffeine" },
  { id: "food", name: "Food" },
  { id: "move", name: "Move" },
  { id: "screens", name: "Screens" },
  { id: "peptide", name: "Peptide" },
];

export type SegmentState = "met" | "open" | "missed";

export interface Segment {
  lane: LaneId;
  start: number;
  end: number;
  state: SegmentState;
  /** The window in words, for screen readers. */
  label: string;
}

export interface Tick {
  lane: LaneId;
  time: number;
  /** Night wakings are drawn quieter than what the wearer did. */
  kind: "event" | "waking";
  /** Outdoor time shows its minutes beside the tick. */
  minutes?: number;
  label: string;
}

export interface Miss {
  lane: LaneId;
  time: number;
  label: string;
}

export interface Bar {
  id: string;
  rule: RuleId;
  time: number;
  /** "Coffee 16:10". */
  label: string;
  /** What broke the rule: "Caffeine after 13:30." */
  what: string;
  /** The rule itself, no effect: "last caffeine 9 h before bed." */
  ruleText: string;
}

const T = {
  wake: clock(WINDOWS.wake),
  bed: clock(WINDOWS.sleepStart),
  caffeine: clock(WINDOWS.caffeineEnd),
  caffeineStrong: clock(WINDOWS.caffeineEndStrong),
  eatFrom: clock(WINDOWS.eatingStart),
  eatTo: clock(WINDOWS.eatingEnd),
  moveBy: clock(WINDOWS.moveBy),
  moveRed: clock(WINDOWS.moveAmberUntil),
  napFrom: clock(WINDOWS.napStart),
  napTo: clock(WINDOWS.napEnd),
  napLate: clock(WINDOWS.napLate),
  screensOff: clock(WINDOWS.screensOff),
} as const;

export interface DayPlan {
  date: string;
  sick: boolean;
  segments: Segment[];
  ticks: Tick[];
  misses: Miss[];
  bars: Bar[];
}

// ---------------------------------------------------------------------------
// Geometry: a compressed night cap (00:00 to 06:00) above 06:00 to 24:00
// ---------------------------------------------------------------------------

const NIGHT_END = 6 * 60;

export const HOUR_MARKS = [6, 9, 12, 15, 18, 21, 24] as const;

/** y in px for a minute of the day, on a strip `height` tall whose top `cap` px hold the night. */
export function yAt(minutes: number, height: number, cap: number): number {
  const m = Math.max(0, Math.min(1440, minutes));
  if (m <= NIGHT_END) return (m / NIGHT_END) * cap;
  return cap + ((m - NIGHT_END) / (1440 - NIGHT_END)) * (height - cap);
}

export function shortDate(date: string): string {
  return new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

// ---------------------------------------------------------------------------
// A day's plan
// ---------------------------------------------------------------------------

const DRINK: Record<"coffee" | "tea" | "energy_drink", string> = { coffee: "Coffee", tea: "Tea", energy_drink: "Energy drink" };

type Of<K extends MonthEvent["kind"]> = Extract<MonthEvent, { kind: K }>;

function mealWord(start: number): string {
  if (start >= 16 * 60) return "Dinner";
  if (start >= 11 * 60) return "Lunch";
  return "Breakfast";
}

export function planFor(days: readonly Day[], index: number, findings: Finding[]): DayPlan {
  const day = days[index];
  const next = days[index + 1];
  const sick = day.type === "sick";
  const { events } = day;
  const passed = (t: number) => day.until >= t;
  const violated = (rule: RuleId) => findings.some((f) => f.rule === rule && f.tone === "violation");
  const watched = (rule: RuleId) => findings.some((f) => f.rule === rule && f.tone === "watch");
  const sun = sunTimes(day.date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset);
  const of = <K extends MonthEvent["kind"]>(kind: K) => events.filter((e): e is Of<K> => e.kind === kind);

  const segments: Segment[] = [];
  const ticks: Tick[] = [];
  const misses: Miss[] = [];

  /** Green when met; a red X at the end when missed without a violation and the window has closed. */
  const judge = (lane: LaneId, start: number, end: number, label: string, met: boolean, missed: boolean) => {
    const state: SegmentState = sick ? "open" : met ? "met" : missed && passed(end) ? "missed" : "open";
    segments.push({ lane, start, end, state, label });
    if (state === "missed") misses.push({ lane, time: end, label: `${label}, missed` });
  };

  // Sleep: up by 6:30 (within the half hour), in bed within 30 min of 22:30 (known once the night is recorded; a late bed is missed).
  const wakeOk = Math.abs(day.sleep.wake - WINDOWS.wake) <= 30;
  judge("sleep", 0, WINDOWS.wake, `Asleep until ${T.wake}, up by then`, wakeOk, !wakeOk);
  const bedKnown = !!next && passed(1440);
  const bedLate = !!next && 1440 + next.sleep.bed > WINDOWS.sleepStart + WINDOWS.regularityTolerance;
  judge("sleep", WINDOWS.sleepStart, 1440, `In bed by ${T.bed}`, bedKnown && !bedLate && !watched("sleep_regularity"), bedKnown && bedLate);
  for (const w of day.sleep.wakings) {
    if (w.start >= 0) ticks.push({ lane: "sleep", time: w.start, kind: "waking", label: `Awake ${w.minutes} min at ${clock(w.start)}${w.baby ? ", the baby" : ""}` });
  }
  for (const w of next?.sleep.wakings ?? []) {
    if (w.start < 0) ticks.push({ lane: "sleep", time: 1440 + w.start, kind: "waking", label: `Awake ${w.minutes} min at ${clock(1440 + w.start)}` });
  }

  // Light: morning light in the first hour, then 60 min of daylight by sunset.
  const outdoor = of("outdoor");
  const morning = outdoor.some((e) => e.sunlight && e.minutes >= 10 && e.start <= day.sleep.wake + 60);
  judge("light", WINDOWS.wake, WINDOWS.wake + 60, `Morning light, ${T.wake} to ${clock(WINDOWS.wake + 60)}`, morning, !morning);
  const daylight = daylightMinutes(day, sun.sunset);
  const enough = daylight >= WINDOWS.daylightTarget;
  judge("light", WINDOWS.wake + 60, sun.sunset, `${WINDOWS.daylightTarget} min of daylight by sunset ${clock(sun.sunset)}, ${daylight} min`, enough, !enough);
  for (const e of outdoor) ticks.push({ lane: "light", time: e.start, kind: "event", minutes: e.minutes, label: `Outside ${e.minutes} min at ${clock(e.start)}` });

  // Caffeine: every cup inside 6:30 to 13:30, 9 h before bed (an energy drink or a double by 9:30).
  const caffeine = of("caffeine");
  judge("caffeine", WINDOWS.wake, WINDOWS.caffeineEnd, `Caffeine ${T.wake} to ${T.caffeine}`, !violated("caffeine") && passed(WINDOWS.caffeineEnd), false);
  for (const e of caffeine) {
    if (e.start <= caffeineCutoff(e.drink, e.strength)) ticks.push({ lane: "caffeine", time: e.start, kind: "event", label: `${DRINK[e.drink]} at ${clock(e.start)}` });
  }

  // Food: every meal inside 10:00 to 19:00; a skipped meal is a miss, not a bar.
  const meals = of("meal");
  const skipped = of("skipped_meal").length > 0;
  const early = meals.some((m) => m.start < WINDOWS.eatingStart);
  judge(
    "food",
    WINDOWS.eatingStart,
    WINDOWS.eatingEnd,
    `Meals ${T.eatFrom} to ${T.eatTo}`,
    meals.length > 0 && !skipped && !early && !violated("last_meal") && passed(WINDOWS.eatingEnd),
    skipped,
  );
  for (const e of meals) {
    if (e.start <= WINDOWS.eatingEnd) ticks.push({ lane: "food", time: e.start, kind: "event", label: `Meal at ${clock(e.start)}` });
  }

  // Move: anything vigorous finished by 18:30, 4 h before bed; moderate any time. Sauna and cold are ticks here too.
  const workouts = of("workout");
  judge("move", WINDOWS.wake, WINDOWS.moveBy, `Vigorous done by ${T.moveBy}`, workouts.some((w) => !w.vigorous || w.start + w.minutes <= WINDOWS.moveBy), workouts.length === 0);
  for (const e of workouts) {
    if (!e.vigorous || e.start + e.minutes <= WINDOWS.moveAmberUntil) ticks.push({ lane: "move", time: e.start, kind: "event", label: `${e.label.split(",")[0]}, ${e.minutes} min at ${clock(e.start)}` });
  }
  for (const e of [...of("sauna"), ...of("cold")]) {
    ticks.push({ lane: "move", time: e.start, kind: "event", label: `${e.kind === "sauna" ? "Sauna" : "Cold plunge"}, ${e.minutes} min at ${clock(e.start)}` });
  }

  // Screens: off from 21:30.
  judge("screens", WINDOWS.screensOff, 1440, `Screens off from ${T.screensOff}`, !watched("screens") && !violated("phone_in_bed") && passed(1440), false);

  // Peptide: each dose inside its window.
  for (const dose of ["AM", "PM"] as const) {
    const [lo, hi] = dose === "AM" ? WINDOWS.peptideAm : WINDOWS.peptidePm;
    const e = of("peptide").find((p) => p.dose === dose);
    const inside = !!e && e.taken && e.start >= lo && e.start <= hi;
    judge("peptide", lo, hi, `Peptide ${dose}, ${clock(lo)} to ${clock(hi)}`, inside, e ? !e.taken : true);
    if (e?.taken) ticks.push({ lane: "peptide", time: e.start, kind: "event", label: `${dose} dose at ${clock(e.start)}` });
  }

  return { date: day.date, sick, segments, ticks, misses: sick ? [] : misses, bars: sick ? [] : barsFor(day, next, findings) };
}

/**
 * Violations as bars, with short strip labels; the sentence goes in the tap
 * box. Drinks are one bar at the last drink. A late bed (a watch, not a red) is
 * a bar too, at the bed time, once the night is known.
 */
function barsFor(day: Day, next: Day | undefined, findings: Finding[]): Bar[] {
  const byId = new Map(day.events.map((e) => [e.id, e]));
  const bars: Bar[] = [];
  const add = (f: Finding, label: string, what: string, ruleText: string) =>
    bars.push({ id: `${f.rule}-${f.eventId ?? f.time}`, rule: f.rule, time: f.time, label, what, ruleText });

  for (const f of findings) {
    if (f.rule === "sleep_regularity") {
      if (f.tone !== "watch" || !next) continue;
      const bed = 1440 + next.sleep.bed;
      const off = bed - WINDOWS.sleepStart;
      if (off > WINDOWS.regularityTolerance) {
        add(f, `Bed ${clock(bed)}`, `In bed at ${clock(bed)}, ${hm(off)} late.`, `in bed within ${WINDOWS.regularityTolerance} min of ${T.bed}.`);
      }
      continue;
    }
    if (f.tone !== "violation") continue;
    const e = f.eventId ? byId.get(f.eventId) : undefined;
    const at = clock(f.time);
    switch (f.rule) {
      case "caffeine":
        if (e?.kind === "caffeine") {
          const strong = e.drink === "energy_drink" || e.strength === "double";
          add(f, `${DRINK[e.drink]} ${at}`, `Caffeine after ${strong ? T.caffeineStrong : T.caffeine}.`, strong ? "an energy drink or a double 13 h before bed." : "last caffeine 9 h before bed.");
        }
        break;
      case "last_meal":
        if (e?.kind === "meal") add(f, `${mealWord(e.start)} ${at}`, `${mealWord(e.start)} after ${T.eatTo}.`, "last meal 3 to 4 h before bed.");
        break;
      case "exercise_timing":
        if (e?.kind === "workout") add(f, `${e.label.split(",")[0]} ${at}`, `Vigorous exercise ending after ${T.moveRed}.`, `vigorous exercise done by ${T.moveBy}, 4 h before bed.`);
        break;
      case "phone_in_bed":
        add(f, `Phone in bed ${at}`, `Phone in bed at ${at}.`, "the phone stays out of the bedroom.");
        break;
      case "nap":
        add(f, `Nap ${at}`, `Nap at ${at}.`, `naps between ${T.napFrom} and ${T.napTo}, ${WINDOWS.napMax} min at most.`);
        break;
      case "co2":
        if (e?.kind === "co2") {
          const ppm = e.ppm.toLocaleString("en-US");
          add(f, `${ppm} ppm ${at}`, `${e.label}: ${ppm} ppm of CO2 for ${hm(e.minutes)}.`, `rooms under ${WINDOWS.co2Amber} ppm; over ${WINDOWS.co2Red.toLocaleString("en-US")} is red.`);
        }
        break;
      case "uv":
        if (e?.kind === "outdoor") {
          const uv = peakUv(day.date, e.start, e.start + e.minutes);
          add(f, `UV ${uv}, ${at}`, `${e.minutes} min in direct sun at UV ${uv}, a clear-sky estimate.`, "under 30 min of direct sun when the UV index is 8 or more.");
        }
        break;
      default:
        break;
    }
  }

  const drinks = day.events.filter((e): e is Of<"alcohol"> => e.kind === "alcohol");
  if (drinks.length) {
    const first = drinks[0];
    const last = drinks[drinks.length - 1];
    const n = drinks.reduce((s, d) => s + d.drinks, 0);
    bars.push({
      id: "alcohol",
      rule: "alcohol",
      time: last.start,
      label: `Drinks ${clock(last.start)}`,
      what: n === 1 ? `1 drink at ${clock(last.start)}.` : `${n} drinks, ${clock(first.start)} to ${clock(last.start)}.`,
      ruleText: "no alcohol.",
    });
  }
  return bars.sort((a, b) => a.time - b.time);
}

/** Bar titles for the Analysis cards, one per rule. */
export const BAR_TITLE: Partial<Record<RuleId, string>> = {
  caffeine: `Caffeine after ${T.caffeine}`,
  alcohol: "Alcohol",
  last_meal: `Last meal after ${T.eatTo}`,
  exercise_timing: `Vigorous exercise ending after ${T.moveRed}`,
  screens: `Screens after ${T.screensOff}`,
  phone_in_bed: "Phone in bed",
  nap: `Nap after ${T.napLate}`,
  co2: `Room over ${WINDOWS.co2Red.toLocaleString("en-US")} ppm`,
  uv: `Direct sun at UV ${WINDOWS.uvHigh} or more`,
  sleep_regularity: `Bed after ${clock(WINDOWS.sleepStart + WINDOWS.regularityTolerance)}`,
};
