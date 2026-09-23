/**
 * A month of seeded life: 30 days ending 2026-09-22 in Houston, written to
 * `days.json`. Deterministic: every day draws from its own generator seeded by
 * its date, so the same file comes out on every run.
 *
 *   node fixtures/month/generate.ts        (from phone/; Node strips the types)
 *
 * Day types are fixed per index below, clean days most common, every type at
 * least once. A day's type shapes its own events and the night that follows
 * it (the next day's `sleep`): late caffeine thins the next night's deep
 * sleep, drinks cut REM, a crying-baby day's own night is fragmented.
 *
 * Every night also carries overnight HRV and resting heart rate. The baseline
 * draws from its own generator (seeded by the date), so adding it left every
 * other draw in place; drinks, short sleep, a sick day and a crying-baby night
 * move it by fixed amounts (Grosicki 2026 for the drinks).
 *
 * Office air: a meeting room at 1,050 ppm on Tuesdays and Thursdays, and one
 * stale_room day with the windows shut at 1,280 ppm. Neither draws randomness.
 */
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Day, DayType, EventDraft, MonthEvent, NightWaking, Sleep, Month } from "../../src/lib/month/types.ts";

const END = "2026-09-22";
const COUNT = 30;
/** The last day is today; its record stops here (20:15). */
const TODAY_UNTIL = 20 * 60 + 15;

/** Index 0 is 2026-08-24, a Monday. A perfect day never follows a red day; only the sleep debt behind it moves its ceilings. */
const TYPES: DayType[] = [
  "clean", "clean", "late_caffeine", "clean", "perfect", "clean", "clean",
  "skipped_lunch", "clean", "late_dinner", "crying_baby", "late_workout", "social_evening", "perfect",
  "late_caffeine", "late_nap", "stale_room", "crying_baby", "screens_in_bed", "travel", "sick",
  "clean", "clean", "late_caffeine", "crying_baby", "drinking_night", "late_caffeine", "midday_sun",
  "perfect", "late_caffeine",
];

// ---------------------------------------------------------------------------
// Deterministic randomness
// ---------------------------------------------------------------------------

function seedOf(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

function rng(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const at = (h: number, m = 0): number => h * 60 + m;

function isoDates(end: string, n: number): string[] {
  const [y, m, d] = end.split("-").map(Number);
  return Array.from({ length: n }, (_, i) => {
    const day = new Date(Date.UTC(y, m - 1, d - (n - 1 - i)));
    return day.toISOString().slice(0, 10);
  });
}

/** 0 = Monday … 6 = Sunday. */
function weekday(date: string): number {
  return (new Date(`${date}T12:00:00Z`).getUTCDay() + 6) % 7;
}

type Draft = EventDraft;

class DayBuilder {
  events: Draft[] = [];
  readonly r: () => number;
  constructor(r: () => number) {
    this.r = r;
  }
  /** Integer in [lo, hi]. */
  int(lo: number, hi: number): number {
    return lo + Math.floor(this.r() * (hi - lo + 1));
  }
  pick<T>(options: readonly T[]): T {
    return options[Math.floor(this.r() * options.length)];
  }
  add(event: Draft): void {
    this.events.push(event);
  }
  remove(predicate: (e: Draft) => boolean): void {
    this.events = this.events.filter((e) => !predicate(e));
  }
}

const LUNCHES = [
  { label: "Rice bowl with chicken", thumb: "rice_bowl" },
  { label: "Salad with salmon", thumb: "salad" },
  { label: "Lentil soup and bread", thumb: "soup" },
  { label: "Chicken and vegetables", thumb: "plate" },
] as const;
const DINNERS = [
  { label: "Salmon and vegetables", thumb: "fish" },
  { label: "Chicken, rice and greens", thumb: "plate" },
  { label: "Bean chili", thumb: "bowl" },
  { label: "Steak salad", thumb: "salad" },
] as const;
const BREAKFASTS = [
  { label: "Eggs and greens", thumb: "eggs" },
  { label: "Greek yogurt and berries", thumb: "bowl" },
  { label: "Oats with nuts", thumb: "bowl" },
] as const;
const WORKOUTS = [
  { label: "Strength, 45 min", minutes: 45, vigorous: true },
  { label: "Run, 6 km", minutes: 35, vigorous: true },
  { label: "Cycling", minutes: 50, vigorous: true },
  { label: "Mobility and core", minutes: 30, vigorous: false },
] as const;

// ---------------------------------------------------------------------------
// A day
// ---------------------------------------------------------------------------

function buildEvents(date: string, type: DayType, r: () => number): { events: Draft[]; held_back: number } {
  const b = new DayBuilder(r);
  const wd = weekday(date);
  const weekend = wd >= 5;
  const sick = type === "sick";
  const travel = type === "travel";

  if (!sick && !travel) {
    // First light on the porch with the first coffee. Houston's sunrise is 6:54 to
    // 7:09 over the month, so the porch comes after it and still inside the hour.
    b.add({ kind: "outdoor", start: at(7, b.int(12, 18)), minutes: b.int(11, 16), label: "Porch, first light", sunlight: true });
    b.add({ kind: "caffeine", start: at(7, b.int(12, 18)), drink: "coffee" });
    b.add({ kind: "mind_check", start: at(9, b.int(10, 30)), ms: b.int(292, 322), lapses: b.int(0, 2), energy: b.int(3, 5), mood: b.int(3, 5), clarity: b.int(3, 5) });
    b.add({ kind: "peptide", start: at(8, b.int(20, 50)), dose: "AM", taken: true, thumb: "pen" });
    const breakfast = b.pick(BREAKFASTS);
    b.add({ kind: "meal", start: at(10, b.int(5, 20)), label: breakfast.label, food: "whole", thumb: breakfast.thumb });
    b.add({ kind: "supplements", start: at(10, b.int(22, 28)), label: "Omega-3 and vitamin D" });
    if (r() < 0.55) b.add({ kind: "caffeine", start: at(11, b.int(30, 55)), drink: b.pick(["coffee", "tea"] as const) });

    if (!weekend) {
      b.add({ kind: "drive", start: at(8, b.int(30, 38)), minutes: b.int(18, 24), label: "Commute" });
      b.add({ kind: "work", start: at(9, 0), minutes: 180, label: "Office" });
      b.add({ kind: "work", start: at(13, 20), minutes: 200, label: "Office" });
      b.add({ kind: "drive", start: at(17, b.int(0, 10)), minutes: b.int(18, 26), label: "Commute" });
      const lunch = b.pick(LUNCHES);
      b.add({ kind: "meal", start: at(12, b.int(15, 25)), label: lunch.label, food: "whole", thumb: lunch.thumb });
      b.add({ kind: "conversation", start: at(12, b.int(15, 25)), minutes: b.int(25, 40), label: "Lunch with Maya" });
      b.add({ kind: "outdoor", start: at(12, b.int(55, 59)), minutes: b.int(15, 25), label: "Walk outside", sunlight: true });
      if (r() < 0.6) b.add({ kind: "sedentary", start: at(13, 25), minutes: b.int(95, 120) });
      if (r() < 0.45) b.add({ kind: "stress", start: at(14, b.int(5, 40)), scene: "Meeting", hr: b.int(82, 90), resting: 58 });
      b.add({ kind: "screen", start: at(9, 5), minutes: 170, device: "computer" });
      b.add({ kind: "screen", start: at(13, 25), minutes: 190, device: "computer" });
      // Tuesday and Thursday afternoons in the meeting room, door closed.
      if (wd === 1 || wd === 3) b.add({ kind: "co2", start: at(14, 0), minutes: 90, ppm: 1050, label: "Meeting room" });
    } else {
      b.add({ kind: "outdoor", start: at(9, b.int(30, 50)), minutes: b.int(45, 70), label: "Park with the stroller", sunlight: true });
      b.add({ kind: "conversation", start: at(9, b.int(30, 50)), minutes: b.int(35, 55), label: "Walk with Maya" });
      const lunch = b.pick(LUNCHES);
      b.add({ kind: "meal", start: at(12, b.int(30, 50)), label: lunch.label, food: "whole", thumb: lunch.thumb });
      b.add({ kind: "drive", start: at(15, b.int(0, 20)), minutes: b.int(15, 25), label: "Groceries" });
      if (r() < 0.5) b.add({ kind: "nap", start: at(13, b.int(35, 50)), minutes: b.int(15, 19) });
    }

    const workout = b.pick(WORKOUTS);
    b.add({ kind: "workout", start: at(15, b.int(30, 40)), minutes: workout.minutes, label: workout.label, vigorous: workout.vigorous });
    const dinner = b.pick(DINNERS);
    b.add({ kind: "meal", start: at(17, b.int(45, 58)), label: dinner.label, food: "whole", thumb: dinner.thumb });
    b.add({ kind: "conversation", start: at(18, 0), minutes: b.int(22, 35), label: "Dinner with family" });
    if (wd === 1 || wd === 3 || wd === 5) b.add({ kind: "sauna", start: at(18, b.int(35, 50)), minutes: b.int(18, 22) });
    if (wd === 5) b.add({ kind: "cold", start: at(19, 15), minutes: 3 });
    b.add({ kind: "screen", start: at(20, b.int(5, 20)), minutes: b.int(20, 30), device: "phone" });
    b.add({ kind: "peptide", start: at(20, b.int(0, 15)), dose: "PM", taken: true, thumb: "pen" });

    // Water: seven or eight sightings, most before 18:00.
    const times = [at(7, 30), at(9, 45), at(11, 15), at(12, 40), at(14, 20), at(15, 50), at(17, 30), at(19, 40)];
    for (const t of times) b.add({ kind: "water", start: t + b.int(0, 12), ml: b.int(260, 330) });
  }

  switch (type) {
    case "perfect":
      b.remove((e) => e.kind === "sedentary" || e.kind === "stress" || (e.kind === "caffeine" && e.start > at(11)));
      b.add({ kind: "outdoor", start: at(16, 20), minutes: 25, label: "Evening walk", sunlight: true });
      b.add({ kind: "conversation", start: at(16, 20), minutes: 25, label: "Call with Dad" });
      if (!b.events.some((e) => e.kind === "sauna")) b.add({ kind: "sauna", start: at(18, 40), minutes: 20 });
      b.add({ kind: "water", start: at(10, 50), ml: 300 });
      break;

    case "late_caffeine":
      b.add({ kind: "caffeine", start: at(16, 10), drink: "coffee" });
      b.add({ kind: "whispered", start: at(16, 11), line: "That coffee lands in tonight's sleep." });
      break;

    case "skipped_lunch":
      b.remove((e) => e.kind === "meal" && e.start >= at(12) && e.start < at(13));
      b.remove((e) => e.kind === "conversation" && e.start >= at(12) && e.start < at(13));
      b.add({ kind: "acted", start: at(11, 30), line: "Added a 20-minute lunch block to your calendar." });
      b.add({ kind: "skipped_meal", start: at(14, 0), meal: "lunch" });
      b.remove((e) => e.kind === "meal" && e.start >= at(17));
      b.add({ kind: "meal", start: at(18, 10), label: "Pasta, a large plate", food: "whole", thumb: "pasta" });
      break;

    case "late_dinner":
      b.remove((e) => e.kind === "meal" && e.start >= at(17));
      b.remove((e) => e.kind === "conversation" && e.start >= at(18));
      b.add({ kind: "meal", start: at(20, 40), label: "Burger and fries", food: "fast_food", thumb: "burger" });
      b.add({ kind: "conversation", start: at(20, 40), minutes: 30, label: "Late dinner with Sam" });
      break;

    case "late_workout":
      b.remove((e) => e.kind === "workout");
      b.add({ kind: "workout", start: at(19, 30), minutes: 55, label: "Run, 8 km", vigorous: true });
      break;

    case "late_nap":
      b.remove((e) => e.kind === "nap");
      b.add({ kind: "nap", start: at(16, 40), minutes: 45 });
      break;

    case "screens_in_bed":
      b.remove((e) => e.kind === "screen" && e.start >= at(20));
      b.add({ kind: "acted", start: at(21, 30), line: "Screens shielded until 07:00." });
      b.add({ kind: "screen", start: at(21, 45), minutes: 60, device: "phone" });
      b.add({ kind: "phone_in_bed", start: at(22, 50), minutes: 55 });
      b.add({ kind: "whispered", start: at(22, 52), line: "Phone down. Sleep is ten minutes away." });
      break;

    case "social_evening":
      b.remove((e) => e.kind === "meal" && e.start >= at(17));
      b.remove((e) => e.kind === "conversation" && e.start >= at(18));
      b.remove((e) => e.kind === "sauna" || e.kind === "cold");
      b.add({ kind: "conversation", start: at(19, 15), minutes: 165, label: "Friends over" });
      b.add({ kind: "meal", start: at(19, 35), label: "Tacos", food: "whole", thumb: "tacos" });
      break;

    case "drinking_night":
      b.remove((e) => e.kind === "meal" && e.start >= at(17));
      b.remove((e) => (e.kind === "conversation" || e.kind === "screen") && e.start >= at(18));
      b.remove((e) => e.kind === "peptide" && e.dose === "PM");
      b.remove((e) => e.kind === "sauna" || e.kind === "cold");
      b.add({ kind: "conversation", start: at(19, 20), minutes: 150, label: "Friends at the bar" });
      b.add({ kind: "meal", start: at(19, 45), label: "Burger and fries", food: "fast_food", thumb: "burger" });
      b.add({ kind: "alcohol", start: at(19, 50), drinks: 1, label: "Wine" });
      b.add({ kind: "alcohol", start: at(20, 45), drinks: 1, label: "Wine" });
      b.add({ kind: "whispered", start: at(20, 47), line: "Water with the next one gets most of tonight back." });
      b.add({ kind: "alcohol", start: at(21, 50), drinks: 1, label: "Cocktail" });
      b.add({ kind: "peptide", start: at(22, 0), dose: "PM", taken: false, thumb: null });
      b.add({ kind: "nicotine", start: at(22, 35), label: "Vape in hand" });
      b.add({ kind: "screen", start: at(23, 10), minutes: 40, device: "phone" });
      break;

    case "travel":
      b.add({ kind: "drive", start: at(5, 0), minutes: 45, label: "To the airport" });
      b.add({ kind: "meal", start: at(6, 40), label: "Breakfast sandwich", food: "ultra_processed", thumb: "sandwich" });
      b.add({ kind: "caffeine", start: at(6, 45), drink: "coffee" });
      b.add({ kind: "drive", start: at(7, 40), minutes: 225, label: "Flight IAH to SFO" });
      b.add({ kind: "peptide", start: at(10, 0), dose: "AM", taken: false, thumb: null });
      b.add({ kind: "meal", start: at(12, 10), label: "Airport pastry", food: "sweets", thumb: "pastry" });
      b.add({ kind: "caffeine", start: at(13, 30), drink: "energy_drink" });
      b.add({ kind: "drive", start: at(13, 50), minutes: 50, label: "Rideshare to the hotel" });
      b.add({ kind: "outdoor", start: at(15, 20), minutes: 12, label: "Hotel courtyard", sunlight: false });
      b.add({ kind: "work", start: at(15, 40), minutes: 150, label: "Hotel room, laptop" });
      b.add({ kind: "screen", start: at(15, 40), minutes: 150, device: "computer" });
      b.add({ kind: "conversation", start: at(18, 30), minutes: 20, label: "Client call" });
      b.add({ kind: "meal", start: at(20, 45), label: "Hotel burger", food: "fast_food", thumb: "burger" });
      b.add({ kind: "peptide", start: at(20, 55), dose: "PM", taken: true, thumb: "pen" });
      for (const t of [at(8, 30), at(12, 15), at(16, 0), at(19, 30)]) b.add({ kind: "water", start: t, ml: 250 });
      break;

    case "sick":
      b.add({ kind: "mind_check", start: at(10, 40), ms: 384, lapses: 5, energy: 1, mood: 2, clarity: 2 });
      b.add({ kind: "peptide", start: at(9, 5), dose: "AM", taken: true, thumb: "pen" });
      b.add({ kind: "caffeine", start: at(9, 30), drink: "tea" });
      b.add({ kind: "meal", start: at(12, 30), label: "Chicken soup", food: "whole", thumb: "soup" });
      b.add({ kind: "nap", start: at(14, 0), minutes: 90 });
      b.add({ kind: "meal", start: at(18, 0), label: "Toast and broth", food: "whole", thumb: "soup" });
      b.add({ kind: "screen", start: at(19, 0), minutes: 60, device: "phone" });
      b.add({ kind: "peptide", start: at(22, 0), dose: "PM", taken: false, thumb: null });
      for (const t of [at(8, 0), at(11, 0), at(13, 30), at(16, 30), at(19, 45)]) b.add({ kind: "water", start: t, ml: 300 });
      break;

    case "crying_baby":
      // Awake half the night: a second coffee lands late morning, still inside the window.
      b.add({ kind: "caffeine", start: at(11, 55), drink: "coffee" });
      b.add({ kind: "nap", start: at(13, 30), minutes: 20 });
      break;

    case "midday_sun":
      // A market with no shade at the day's highest UV; costs nothing through sleep.
      b.add({ kind: "outdoor", start: at(14, 0), minutes: 55, label: "Farmers market, no shade", sunlight: true });
      b.add({ kind: "conversation", start: at(14, 0), minutes: 40, label: "Market with Maya" });
      break;

    case "stale_room":
      // A clean day in an office with the windows shut all afternoon.
      b.add({ kind: "co2", start: at(13, 20), minutes: 220, ppm: 1280, label: "Office, windows shut" });
      break;

    case "clean":
      break;
  }

  // Asked: a question to close a gap, some days. A stale_room day is a clean day
  // with bad air, so it keeps the same draw.
  if ((type === "clean" || type === "stale_room") && r() < 0.35) b.add({ kind: "asked", start: at(15, 5), line: "Was that tea or coffee?" });

  const held_back = type === "perfect" ? b.int(2, 4) : b.int(5, 14);
  return { events: b.events, held_back };
}

// ---------------------------------------------------------------------------
// The night that ends on a day's morning
// ---------------------------------------------------------------------------

/** Sleep under 6.5 h counts as short for the overnight HRV. */
const SHORT_SLEEP = 390;

/**
 * Overnight HRV (ms) and resting heart rate (bpm). The baseline comes from a
 * generator of its own so the day's main stream is untouched; each effect is a
 * fixed step: per drink the night before rhr +2.6 and hrv −3.5 (Grosicki 2026),
 * short sleep hrv −6, a sick day hrv −12 and rhr +6, a crying-baby night hrv −4.
 */
function heart(date: string, minutes: number, ownType: DayType, prevDrinks: number): { hrv_ms: number; rhr_bpm: number } {
  const h = rng(seedOf(`${date}:heart`));
  const k = (lo: number, hi: number) => lo + Math.floor(h() * (hi - lo + 1));
  let hrv = k(52, 60);
  let rhr = k(56, 60);
  hrv -= 3.5 * prevDrinks;
  rhr += 2.6 * prevDrinks;
  if (minutes < SHORT_SLEEP) hrv -= 6;
  if (ownType === "sick") {
    hrv -= 12;
    rhr += 6;
  }
  if (ownType === "crying_baby") hrv -= 4;
  const tenth = (n: number) => Math.round(n * 10) / 10;
  return { hrv_ms: tenth(hrv), rhr_bpm: tenth(rhr) };
}

function buildSleep(date: string, prevType: DayType, ownType: DayType, prevDrinks: number, r: () => number): Sleep {
  const j = (lo: number, hi: number) => lo + Math.floor(r() * (hi - lo + 1));
  let bed = -90 + j(-8, 10); // 22:30 is 90 min before midnight
  let wake = 390 + j(-6, 12); // 06:30
  let deep = j(92, 112);
  let rem = j(95, 118);
  let latency = j(8, 14);
  const wakings: NightWaking[] = [];

  switch (prevType) {
    case "late_caffeine":
      bed += j(12, 20);
      latency += 15;
      deep -= j(36, 44);
      break;
    case "drinking_night":
      bed = 40 + j(-5, 10);
      wake = 430 + j(0, 20);
      rem = Math.round(rem * 0.64);
      deep -= j(18, 26);
      break;
    case "late_dinner":
      deep -= j(15, 22);
      bed += 20;
      break;
    case "late_workout":
      bed = -40 + j(-5, 10);
      latency += 20;
      deep -= 12;
      break;
    case "late_nap":
      bed = -25 + j(-5, 10);
      latency += 25;
      break;
    case "screens_in_bed":
      bed = -10 + j(-5, 10);
      latency += 15;
      deep -= 15;
      break;
    case "social_evening":
      bed = -10 + j(-5, 10);
      break;
    case "sick":
      bed = -135;
      wake = 450;
      deep += 10;
      break;
    case "perfect":
      bed = -92;
      wake = 388;
      deep = j(110, 120);
      rem = j(112, 122);
      break;
  }

  if (ownType === "travel") wake = 270; // 04:30 alarm for the flight
  if (ownType === "sick") wake = 460;
  if (ownType === "crying_baby") {
    for (const [start, minutes] of [[40, j(12, 22)], [135, j(15, 25)], [230, j(12, 20)], [305, j(8, 15)]] as const) {
      wakings.push({ start, minutes, baby: true });
    }
    deep -= j(30, 40);
    rem -= j(15, 25);
  }
  if (ownType === "perfect") {
    wake = 388;
    deep = Math.max(deep, 108);
  }

  const awake = wakings.reduce((sum, w) => sum + w.minutes, 0);
  const minutes = wake - bed - latency - awake;
  const { hrv_ms, rhr_bpm } = heart(date, minutes, ownType, prevDrinks);
  return {
    bed,
    wake,
    minutes,
    deep: Math.max(40, deep),
    rem: Math.max(45, rem),
    fragmented: wakings.length > 0,
    wakings,
    hrv_ms,
    rhr_bpm,
    seeded: true,
  };
}

/** Standard drinks logged on a day; they land in the night that follows. */
function drinksOn(events: MonthEvent[]): number {
  return events.reduce((sum, e) => (e.kind === "alcohol" ? sum + e.drinks : sum), 0);
}

// ---------------------------------------------------------------------------
// The month
// ---------------------------------------------------------------------------

const SUMMARIES = ["Humid, sun", "Afternoon storms", "Clear and hot", "Partly cloudy"] as const;

function buildMonth(): Month {
  const dates = isoDates(END, COUNT);
  const days: Day[] = [];
  dates.forEach((date, i) => {
    const r = rng(seedOf(date));
    const type = TYPES[i];
    const prev = i > 0 ? days[i - 1] : null;
    const sleep = buildSleep(date, prev ? prev.type : "clean", type, prev ? drinksOn(prev.events) : 0, r);
    const last = i === COUNT - 1;
    const { events, held_back } = buildEvents(date, type, r);
    const until = last ? TODAY_UNTIL : 1440;
    const kept = events
      .filter((e) => e.start < until)
      .sort((a, b) => a.start - b.start)
      .map((e, n) => ({ ...e, id: `${date}-${String(n).padStart(2, "0")}`, seeded: true }) as MonthEvent);
    const aqi = date === "2026-09-09" ? 118 : 30 + Math.floor(r() * 28);
    days.push({
      date,
      type,
      sleep,
      events: kept,
      aqi,
      weather: { high_f: 88 + Math.floor(r() * 8), summary: SUMMARIES[Math.floor(r() * SUMMARIES.length)] },
      held_back,
      until,
      seeded: true,
    });
  });
  return { city: "Houston", lat: 29.7604, lon: -95.3698, utc_offset: -5, days, seeded: true };
}

const out = join(dirname(fileURLToPath(import.meta.url)), "days.json");
writeFileSync(out, `${JSON.stringify(buildMonth(), null, 1)}\n`);
console.log(`wrote ${out}`);
