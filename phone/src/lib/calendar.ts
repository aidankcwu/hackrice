/**
 * What the Calendar draws for a day, as data: the protocol's bands (soft,
 * labelled, one lane each), reality's items (events and day-level findings,
 * each with its tone and consequence line), the grey frame of work and travel,
 * and the day header's numbers. No React here.
 */
import type { Day, MonthEvent } from "./month/types";
import { sunTimes } from "./month/sun";
import type { Operating } from "./operating";
import {
  HOUSTON,
  WINDOWS,
  clock,
  daylightMinutes,
  hm,
  peopleMinutes,
  peptideSchedule,
  waterMl,
  type Finding,
  type Tone,
} from "./rules";

// ---------------------------------------------------------------------------
// Bands
// ---------------------------------------------------------------------------

export interface Band {
  id: string;
  lane: number;
  start: number;
  end: number;
  label: string;
  /** Drawn dimmer (wind-down, and every band on a sick day). */
  dim?: boolean;
  /** 0..1 of the target met, drawn as the filled part of the band. */
  progress?: number;
  /** Minutes where a sighting fed the progress (water). */
  ticks?: number[];
}

/** Lanes, left to right: rest, food, caffeine and heat, doses, movement, daylight, people, water. */
export const LANE_COUNT = 8;

export function sunFor(date: string): { sunrise: number; sunset: number } {
  return sunTimes(date, HOUSTON.lat, HOUSTON.lon, HOUSTON.utcOffset);
}

export function bandsFor(day: Day): Band[] {
  const sick = day.type === "sick";
  const sun = sunFor(day.date);
  const daylight = daylightMinutes(day, sun.sunset);
  const people = peopleMinutes(day);
  const water = waterMl(day);
  const heat = day.events.some((e) => e.kind === "sauna" || e.kind === "cold");
  const bands: Band[] = [
    { id: "sleep-am", lane: 0, start: 0, end: WINDOWS.wake, label: "Sleep" },
    { id: "nap", lane: 0, start: WINDOWS.napStart, end: WINDOWS.napEnd, label: "Nap ok" },
    { id: "wind", lane: 0, start: WINDOWS.windDown, end: WINDOWS.sleepStart, label: "Wind-down", dim: true },
    { id: "sleep-pm", lane: 0, start: WINDOWS.sleepStart, end: 1440, label: "Sleep" },
    { id: "light", lane: 1, start: WINDOWS.wake, end: WINDOWS.wake + 60, label: "Light" },
    { id: "eating", lane: 1, start: WINDOWS.eatingStart, end: WINDOWS.eatingEnd, label: "Eating window, last meal 18:30" },
    { id: "screens", lane: 1, start: WINDOWS.screensOff, end: 1440, label: "Screens off 21:30" },
    { id: "caffeine", lane: 2, start: WINDOWS.wake, end: WINDOWS.caffeineEnd, label: "Caffeine ok until 12:30" },
    ...(heat ? [{ id: "heat", lane: 2, start: WINDOWS.saunaStart, end: WINDOWS.saunaEnd, label: "Sauna or cold" }] : []),
    { id: "pep-am", lane: 3, start: WINDOWS.peptideAm[0], end: WINDOWS.peptideAm[1], label: "Peptide AM" },
    { id: "pep-pm", lane: 3, start: WINDOWS.peptidePm[0], end: WINDOWS.peptidePm[1], label: "Peptide PM" },
    { id: "move", lane: 4, start: WINDOWS.wake, end: WINDOWS.moveBy, label: "Move by 16:30" },
    {
      id: "daylight", lane: 5, start: sun.sunrise, end: sun.sunset,
      label: `Daylight ${daylight} of 60 min`, progress: Math.min(1, daylight / WINDOWS.daylightTarget),
    },
    {
      id: "people", lane: 6, start: WINDOWS.wake, end: WINDOWS.peopleBy,
      label: `People ${people} of 30 min`, progress: Math.min(1, people / WINDOWS.peopleTarget),
    },
    {
      id: "water", lane: 7, start: WINDOWS.wake, end: WINDOWS.waterBy,
      label: `Water ${(water / 1000).toFixed(1)} of 2 L`, progress: Math.min(1, water / WINDOWS.waterTarget),
      ticks: day.events.filter((e) => e.kind === "water" && e.start <= WINDOWS.waterBy).map((e) => e.start),
    },
  ];
  return sick ? bands.map((b) => ({ ...b, dim: true })) : bands;
}

// ---------------------------------------------------------------------------
// Items: what happened, on top of the bands
// ---------------------------------------------------------------------------

export type Glyph =
  | "coffee" | "tea" | "energy" | "meal" | "no-meal" | "wine" | "nicotine" | "workout" | "sun" | "shade"
  | "seated" | "phone" | "computer" | "bed" | "people" | "nap" | "sauna" | "cold" | "peptide" | "pill"
  | "car" | "plane" | "stress" | "mind" | "whisper" | "ask" | "act" | "baby" | "moon" | "sunrise" | "sunset"
  | "target";

export interface Item {
  id: string;
  time: number;
  glyph: Glyph;
  title: string;
  /** Quiet second line (minutes, what was seen). */
  detail?: string;
  tone: Tone;
  /** Consequence lines, drawn beside a red or amber marker. */
  lines: string[];
  /** A frame the glasses kept (seeded: a placeholder tile). */
  thumb?: string | null;
  /** Peptide: taken (check) or missed (cross). */
  mark?: "taken" | "missed";
  /** Thin, quiet rows: system markers, sun lines, night wakings. */
  quiet?: boolean;
  /** A hairline across the row (sunrise, sunset). */
  line?: boolean;
}

/** Grey frame of the day: work and travel blocks behind the items. */
export interface Frame {
  id: string;
  start: number;
  end: number;
  label: string;
}

const RANK: Record<Tone, number> = { violation: 3, watch: 2, inside: 1, neutral: 0 };

function worst(tones: Tone[]): Tone {
  return tones.reduce<Tone>((a, b) => (RANK[b] > RANK[a] ? b : a), "neutral");
}

const DRINK: Record<"coffee" | "tea" | "energy_drink", { title: string; glyph: Glyph }> = {
  coffee: { title: "Coffee", glyph: "coffee" },
  tea: { title: "Tea", glyph: "tea" },
  energy_drink: { title: "Energy drink", glyph: "energy" },
};

const FOOD_WORD: Record<string, string> = {
  fast_food: "fast food",
  sweets: "sweets",
  ultra_processed: "ultra-processed",
};

function spanOverlaps(e: { start: number; minutes: number }, frames: Frame[]): boolean {
  return frames.some((f) => e.start < f.end && e.start + e.minutes > f.start);
}

/**
 * Everything drawn on the day's timeline, in time order. `next` supplies
 * tonight's bedtime. Water is drawn as ticks on its band, not as rows, and
 * computer screens inside a work block are part of the grey frame.
 */
export function itemsFor(day: Day, findings: Finding[], next?: Day): { items: Item[]; frames: Frame[] } {
  const byEvent = new Map<string, Finding[]>();
  for (const f of findings) {
    if (!f.eventId) continue;
    const list = byEvent.get(f.eventId) ?? [];
    list.push(f);
    byEvent.set(f.eventId, list);
  }

  const frames: Frame[] = day.events
    .filter((e): e is Extract<MonthEvent, { kind: "work" }> => e.kind === "work")
    .map((e) => ({ id: e.id, start: e.start, end: e.start + e.minutes, label: `${e.label}, ${hm(e.minutes)}` }));

  const items: Item[] = [];
  let drinkNumber = 0;
  const sick = day.type === "sick";

  for (const e of day.events) {
    const fs = byEvent.get(e.id) ?? [];
    const tone = sick ? "neutral" : fs.length ? worst(fs.map((f) => f.tone)) : "neutral";
    const lines = sick ? [] : fs.filter((f) => f.tone === "violation" || f.tone === "watch").map((f) => f.line);
    const base = { id: e.id, time: e.start, tone, lines };
    switch (e.kind) {
      case "caffeine":
        items.push({ ...base, ...DRINK[e.drink] });
        break;
      case "meal":
        items.push({ ...base, glyph: "meal", title: e.label, detail: FOOD_WORD[e.food], thumb: e.thumb });
        break;
      case "skipped_meal":
        items.push({ ...base, glyph: "no-meal", title: `No ${e.meal} seen` });
        break;
      case "alcohol":
        drinkNumber += e.drinks;
        items.push({ ...base, glyph: "wine", title: `${e.label}, drink ${drinkNumber}` });
        break;
      case "nicotine":
        items.push({ ...base, glyph: "nicotine", title: e.label });
        break;
      case "workout":
        items.push({ ...base, glyph: "workout", title: e.label, detail: `${e.minutes} min` });
        break;
      case "outdoor":
        items.push({ ...base, glyph: e.sunlight ? "sun" : "shade", title: e.label, detail: `${e.minutes} min${e.sunlight ? ", in the sun" : ", no direct sun"}` });
        break;
      case "sedentary":
        items.push({ ...base, glyph: "seated", title: "Seated", detail: `${e.minutes} min` });
        break;
      case "screen":
        if (e.device === "computer" && spanOverlaps(e, frames) && tone !== "violation") break;
        items.push({ ...base, glyph: e.device === "phone" ? "phone" : "computer", title: e.device === "phone" ? "Phone" : "Computer", detail: `${e.minutes} min` });
        break;
      case "phone_in_bed":
        items.push({ ...base, glyph: "bed", title: "Phone in bed", detail: `${e.minutes} min` });
        break;
      case "conversation":
        items.push({ ...base, glyph: "people", title: e.label, detail: `${e.minutes} min face to face` });
        break;
      case "nap":
        items.push({ ...base, glyph: "nap", title: "Nap", detail: `${e.minutes} min` });
        break;
      case "sauna":
        items.push({ ...base, glyph: "sauna", title: "Sauna", detail: `${e.minutes} min` });
        break;
      case "cold":
        items.push({ ...base, glyph: "cold", title: "Cold plunge", detail: `${e.minutes} min` });
        break;
      case "peptide": {
        const detail = fs.find((f) => f.tone === "inside")?.line;
        items.push({
          ...base,
          glyph: "peptide",
          title: `Peptide ${e.dose} ${e.taken ? "taken" : "missed"}`,
          detail,
          thumb: e.thumb,
          mark: e.taken ? "taken" : "missed",
        });
        break;
      }
      case "supplements":
        items.push({ ...base, glyph: "pill", title: e.label, detail: fs[0]?.line });
        break;
      case "drive":
        items.push({ ...base, tone: "neutral", glyph: /flight/i.test(e.label) ? "plane" : "car", title: e.label, detail: `${e.minutes} min, no light, seated` });
        break;
      case "work":
        break;
      case "stress":
        items.push({ ...base, glyph: "stress", title: `Heart rate ${e.hr}`, detail: e.scene });
        break;
      case "mind_check": {
        const self = e.energy !== null ? ` · energy ${e.energy}, mood ${e.mood}, clarity ${e.clarity}` : "";
        items.push({ ...base, tone: "neutral", glyph: "mind", title: "Mind check", detail: `${e.ms} ms, ${e.lapses} ${e.lapses === 1 ? "lapse" : "lapses"}${self}`, quiet: true });
        break;
      }
      case "whispered":
        items.push({ ...base, tone: "neutral", glyph: "whisper", title: `Whispered “${e.line}”`, quiet: true });
        break;
      case "asked":
        items.push({ ...base, tone: "neutral", glyph: "ask", title: `Asked “${e.line}”`, quiet: true });
        break;
      case "acted":
        items.push({ ...base, tone: "neutral", glyph: "act", title: `Acted: ${e.line}`, quiet: true });
        break;
      case "water":
        break;
    }
  }

  // Day-level findings: last night, the targets, tonight's bedtime.
  for (const f of findings) {
    if (f.eventId) continue;
    switch (f.rule) {
      case "sleep_short":
      case "sleep_deep":
      case "sleep_fragmented":
      case "sick":
        items.push({ id: `f-${f.rule}`, time: f.time, glyph: "moon", title: f.rule === "sick" ? "Recovery day" : "Last night", tone: f.tone, lines: [f.line] });
        break;
      case "sleep_window":
        items.push({ id: "f-bed", time: f.time, glyph: "moon", title: "Late to bed", tone: f.tone, lines: [f.line] });
        break;
      default:
        items.push({ id: `f-${f.rule}`, time: f.time, glyph: "target", title: TARGET_TITLE[f.rule] ?? "Target", tone: f.tone, lines: [f.line] });
    }
  }

  // Waking and bedtime on the Sleep band; wakings in the night, never red.
  const sleep = day.sleep;
  for (const [n, w] of sleep.wakings.entries()) {
    items.push({
      id: `wake-${n}`, time: w.start, glyph: w.baby ? "baby" : "moon", title: `Awake ${w.minutes} min`,
      detail: w.baby ? "The baby woke. Not your decision." : undefined, tone: "neutral", lines: [], quiet: true,
    });
  }
  items.push({
    id: "woke", time: sleep.wake, glyph: "moon", title: `Up at ${clock(sleep.wake)}`,
    detail: `${hm(sleep.minutes)} asleep, deep ${hm(sleep.deep)}`, tone: "neutral", lines: [], quiet: true,
  });
  if (next && !findings.some((f) => f.rule === "sleep_window")) {
    items.push({ id: "bed", time: Math.min(1439, 1440 + next.sleep.bed), glyph: "moon", title: `In bed ${clock(1440 + next.sleep.bed)}`, tone: "inside", lines: [], quiet: true });
  }

  const sun = sunFor(day.date);
  items.push({ id: "sunrise", time: sun.sunrise, glyph: "sunrise", title: `Sunrise ${clock(sun.sunrise)}`, tone: "neutral", lines: [], quiet: true, line: true });
  items.push({ id: "sunset", time: sun.sunset, glyph: "sunset", title: `Sunset ${clock(sun.sunset)}`, tone: "neutral", lines: [], quiet: true, line: true });

  items.sort((a, b) => a.time - b.time || Number(!!b.line) - Number(!!a.line));
  return { items: items.filter((i) => i.time < day.until || i.id === "sunset" || i.id === "sunrise"), frames };
}

const TARGET_TITLE: Partial<Record<Finding["rule"], string>> = {
  morning_light: "No morning light",
  daylight: "Daylight short",
  people: "People short",
  water: "Water short",
  wake_anchor: "Wake time",
};

// ---------------------------------------------------------------------------
// The day header
// ---------------------------------------------------------------------------

export interface DayHeader {
  title: string;
  short: string;
  cognition: number;
  body: number;
  sleep: string;
  streaks: string[];
  weather: string;
  aqi: number;
  heldBack: number;
  recovery: boolean;
}

export function headerFor(days: readonly Day[], index: number, operating: Operating): DayHeader {
  const day = days[index];
  const date = new Date(`${day.date}T12:00:00`);
  const title = date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  const short = date.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  const pep = peptideSchedule(days, index);
  let clean = 0;
  for (let i = index; i >= 0; i--) {
    const late = days[i].events.some((e) => e.kind === "caffeine" && e.start > WINDOWS.caffeineEnd);
    if (late) break;
    clean += 1;
  }
  return {
    title,
    short,
    cognition: operating.cognition,
    body: operating.body,
    sleep: `${hm(day.sleep.minutes)}, deep ${hm(day.sleep.deep)}`,
    streaks: [`Peptide ${pep.onSchedule} of ${pep.of}`, clean > 0 ? `clean caffeine ${clean} ${clean === 1 ? "day" : "days"}` : "late caffeine today"],
    weather: `${day.weather.high_f}°, ${day.weather.summary.toLowerCase()}`,
    aqi: day.aqi,
    heldBack: day.held_back,
    recovery: day.type === "sick",
  };
}

/** Findings worth a dot in the week view: red and amber only, plus the green count. */
export function dotsFor(findings: Finding[]): { time: number; tone: Tone }[] {
  return findings.filter((f) => f.tone === "violation" || f.tone === "watch").map((f) => ({ time: f.time, tone: f.tone }));
}
