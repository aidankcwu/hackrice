/**
 * Offline stand-in for the backend (`npm run dev:mock`, or the backend down).
 *
 * The seven-day data is the pipeline's own fixed dataset, mirrored from
 * backend/pipeline/seed/fixtures.py: every `seed_rows` series, and the
 * `seed_live_episodes` day pattern for the six earlier days — morning coffee,
 * office conversations, a restaurant meal, one long screen block, an evening
 * park walk, and the 16:30 coffee on days 2, 4 and 6 that explains the bad
 * nights. Episode ids follow backend/pipeline/seed/generate.py
 * (`e_seed_<day>_<nn>`), which is what `/api/episodes` actually returns.
 *
 * Today follows the sim script in backend/pipeline/sim/scenario.py (office
 * screen → desk coffee → cafe lunch with company → park walk → screen →
 * evening phone → wine) stretched over a real day, and is cut off at the
 * current hour so the fixture day is only as far along as the clock.
 *
 * Pure: no I/O, no clock reads beyond the arguments.
 */
import { daysEnding } from "./backend";
import type { DataSource, DayInputs, PipelineEpisode } from "./types";

export const FIXTURE_DAY_COUNT = 7;

// ---------------------------------------------------------------------------
// Seeded series — fixtures.py `seed_rows`, one value per day, oldest first
// ---------------------------------------------------------------------------

const ZEROS = [0, 0, 0, 0, 0, 0, 0] as const;
/** fixtures.py keeps `DAYTIME_LIGHT_MINUTES` and `DAYLIGHT_MIN` identical. */
const LIGHT_MINUTES = [35, 28, 41, 22, 38, 26, 44] as const;

const SERIES: Readonly<Record<string, readonly number[]>> = {
  sleep_hours: [7.4, 5.8, 7.6, 6.0, 7.8, 6.2, 7.5],
  sleep_regularity_sri: [84, 62, 85, 61, 83, 63, 84],
  hrv_rmssd_ratio: [1.02, 0.82, 1.03, 0.84, 1.01, 0.86, 1.02],
  bed_time: [23.0, 0.75, 23.0, 0.75, 23.0, 0.75, 23.0],
  steps: [9100, 8500, 7900, 7300, 6700, 6100, 5500],
  vilpa_minutes: [4.2, 3.8, 3.1, 2.6, 2.2, 1.8, 1.4],
  gait_speed_ms: [1.31, 1.28, 1.26, 1.24, 1.22, 1.19, 1.17],
  balance_one_leg_s: [14, 13, 15, 12, 13, 11, 12],
  night_noise_db: [41, 42, 43, 42, 41, 44, 42],
  breathwork_minutes: [6, 5, 5, 0, 6, 0, 5],
  purpose_score: [4, 4, 4, 3, 4, 4, 4],
  daytime_light_minutes: LIGHT_MINUTES,
  evening_light_ok: [1, 0, 1, 0, 1, 1, 0],
  // Nature is a live metric; the seeded row exists only so the panel is not blank.
  nature_minutes: ZEROS,
  resting_hr: [58, 63, 58, 63, 57, 63, 58],
  respiratory_rate: [14.2, 15.1, 14.0, 15.3, 13.9, 15.0, 14.1],
  skin_temp_dev: [0.0, 0.3, -0.1, 0.4, 0.0, 0.2, -0.1],
  spo2: [98, 96, 98, 96, 99, 97, 98],
  deep_min: [92, 61, 96, 64, 101, 68, 94],
  rem_min: [112, 78, 116, 81, 119, 84, 114],
  recovery_score: [82, 34, 86, 39, 79, 43, 84],
  strain: [14.2, 6.1, 16.8, 5.4, 18.1, 6.8, 8.3],
  run_km: [8, 0, 12, 0, 16, 0, 0],
  run_pace: [5.15, 0, 5.25, 0, 5.42, 0, 0],
  run_avg_hr: [146, 0, 151, 0, 154, 0, 0],
  vo2_max: [51, 51, 51, 51, 51, 51, 51],
  walking_steadiness: [94, 91, 95, 90, 94, 92, 95],
  daylight_min: LIGHT_MINUTES,
  journal_alcohol: [0, 0, 0, 1, 0, 0, 0],
  journal_caffeine_late: [0, 1, 0, 1, 0, 1, 0],
  journal_nicotine: ZEROS,
  journal_cannabis: ZEROS,
};

function seededFor(dayIndex: number): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [metric, values] of Object.entries(SERIES)) out[metric] = values[dayIndex];
  // fixtures.py: `sleep_hours` and `bed_time` describe the night that starts on
  // this day, and `wake_time` is derived from them.
  out.wake_time = Math.round(((out.bed_time + out.sleep_hours) % 24) * 100) / 100;
  return out;
}

// ---------------------------------------------------------------------------
// Episodes
// ---------------------------------------------------------------------------

interface Scripted {
  kind: string;
  /** Local decimal hour. */
  startHh: number;
  minutes: number;
  dominant: Record<string, unknown>;
}

/** fixtures.py `seed_live_episodes`, per historical day (six days, oldest first). */
const LATE_CAFFEINE_INDEXES: ReadonlySet<number> = new Set([1, 3, 5]);
const LATE_COFFEE_HOUR = 16.5;
const MORNING_COFFEE_HOUR = 8.25;
const SCREEN_HOURS = [7.5, 8.0, 7.0, 8.5, 7.2, 6.8] as const;
const OUTDOOR_MINUTES = [12, 18, 10, 14, 16, 14] as const;
const MEAL_FOOD_TYPE = ["mixed", "processed", "vegetables", "mixed", "fish", "grains"] as const;
const CONVERSATION_HOURS: ReadonlyArray<readonly number[]> = [
  [10.5, 15.0],
  [11.0],
  [9.75, 14.5],
  [13.0],
  [10.0, 16.0],
  [11.5, 15.5],
];

function historicalScript(dayIndex: number): Scripted[] {
  const office = { scene: "office", activity: "seated" };
  // Insertion order is the id order (`_00` coffee, then conversations, meal, screen, outdoor, late coffee).
  const script: Scripted[] = [
    { kind: "caffeine_sighting", startHh: MORNING_COFFEE_HOUR, minutes: 1.5, dominant: { scene: "home", activity: "standing" } },
    ...CONVERSATION_HOURS[dayIndex].map((startHh) => ({ kind: "conversation", startHh, minutes: 22, dominant: { ...office } })),
    { kind: "meal", startHh: 12.5, minutes: 35, dominant: { scene: "restaurant", activity: "eating", food_type: MEAL_FOOD_TYPE[dayIndex] } },
    { kind: "screen_block", startHh: 9.5, minutes: SCREEN_HOURS[dayIndex] * 60, dominant: { ...office } },
    { kind: "outdoor_block", startHh: 18.5, minutes: OUTDOOR_MINUTES[dayIndex], dominant: { scene: "park", activity: "walking" } },
  ];
  if (LATE_CAFFEINE_INDEXES.has(dayIndex)) {
    script.push({ kind: "caffeine_sighting", startHh: LATE_COFFEE_HOUR, minutes: 2, dominant: { ...office } });
  }
  return script;
}

/**
 * Today, in the shape the live episode builder emits for the sim scenario
 * (`dominant` carries `food_type`, and the lunch conversation inherits the
 * meal's tags because both run over the same ticks).
 */
const TODAY_SCRIPT: readonly Scripted[] = [
  { kind: "screen_block", startHh: 9 + 5 / 60, minutes: 110, dominant: { scene: "office", activity: "seated", food_type: "none" } },
  { kind: "caffeine_sighting", startHh: 9 + 40 / 60, minutes: 1, dominant: { scene: "office", activity: "seated", food_type: "none" } },
  { kind: "meal", startHh: 12 + 20 / 60, minutes: 35, dominant: { scene: "cafe", activity: "eating", food_type: "rice_bowl" } },
  { kind: "conversation", startHh: 12 + 20 / 60, minutes: 40, dominant: { scene: "cafe", activity: "eating", food_type: "rice_bowl" } },
  { kind: "outdoor_block", startHh: 13 + 5 / 60, minutes: 25, dominant: { scene: "park", activity: "walking", food_type: "none" } },
  { kind: "screen_block", startHh: 14, minutes: 180, dominant: { scene: "office", activity: "seated", food_type: "none" } },
  { kind: "screen_block", startHh: 21.5, minutes: 40, dominant: { scene: "living_room", activity: "phone_use", food_type: "none" } },
  { kind: "alcohol_sighting", startHh: 21 + 50 / 60, minutes: 1, dominant: { scene: "home", activity: "seated", food_type: "none" } },
];

/** The glasses emit a tick every 1.5 s (sim `interval_s`). */
const TICK_INTERVAL_S = 1.5;

/** Unix seconds at local midnight of an ISO date — fixtures.py `_ts(day, 0)`. */
function localMidnight(dateIso: string): number {
  const [y, m, d] = dateIso.split("-").map(Number);
  return new Date(y, m - 1, d).getTime() / 1000;
}

function localHourOfDay(t: number): number {
  const d = new Date(t * 1000);
  return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600;
}

function episode(id: string, s: Scripted, midnight: number, durationS: number, open: boolean, tickS: number): PipelineEpisode {
  const start_t = midnight + s.startHh * 3600;
  return {
    id,
    kind: s.kind,
    start_t,
    end_t: open ? null : start_t + durationS,
    duration_s: durationS,
    dominant: { ...s.dominant },
    tick_count: Math.max(1, Math.floor(durationS / tickS)),
    open,
  };
}

function historicalEpisodes(dateIso: string, dayIndex: number): PipelineEpisode[] {
  const midnight = localMidnight(dateIso);
  // fixtures.py stamps seeded episodes with one tick per second.
  return historicalScript(dayIndex).map((s, n) => episode(`e_seed_${dateIso}_${String(n).padStart(2, "0")}`, s, midnight, s.minutes * 60, false, 1));
}

function todayEpisodes(dateIso: string, nowHh: number): PipelineEpisode[] {
  const midnight = localMidnight(dateIso);
  return TODAY_SCRIPT.filter((s) => s.startHh < nowHh).map((s, n) => {
    const id = `e_${String(n + 1).padStart(4, "0")}`;
    const stillRunning = s.startHh + s.minutes / 60 > nowHh;
    // An episode the clock is inside is reported the way the live builder does: open, duration so far.
    const durationS = stillRunning ? (nowHh - s.startHh) * 3600 : s.minutes * 60;
    return episode(id, s, midnight, durationS, stillRunning, TICK_INTERVAL_S);
  });
}

// ---------------------------------------------------------------------------
// Public
// ---------------------------------------------------------------------------

/** Seven `DayInputs` ending on `todayIso`; today's episodes stop at `nowT`'s local hour. No frames. */
export function fixtureDays(todayIso: string, nowT: number): DayInputs[] {
  const dates = daysEnding(todayIso, FIXTURE_DAY_COUNT);
  const todayIndex = dates.length - 1;
  const nowHh = localHourOfDay(nowT);
  return dates.map((date, i) => ({
    date,
    episodes: i === todayIndex ? todayEpisodes(date, nowHh) : historicalEpisodes(date, i),
    seeded: seededFor(i),
    frameUrls: {},
    isToday: i === todayIndex,
    nowT,
  }));
}

export function fixtureSource(todayIso: string, nowT?: number): DataSource {
  return {
    mode: "mock",
    api_base: "fixture",
    day: todayIso,
    capture_source: "fixture",
    demo_mode: true,
    last_tick_t: nowT,
  };
}
