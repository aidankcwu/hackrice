import { describe, expect, it } from "vitest";
import {
  buildDayRequests,
  buildHistory,
  buildWearableDay,
  buildWeekRow,
  episodeToEngine,
  habitualBedtime,
  mapEpisodes,
  socialIndex,
} from "./adapter";
import { localDecimalHour } from "./format";
import type { DayInputs, EngineEpisode, PipelineEpisode } from "./types";

// Local-time construction keeps the expectations true in any timezone: the
// adapter reads local hours, and these helpers build local timestamps.
const DAY = "2026-09-10";
const at = (h: number, m = 0, s = 0): number => new Date(2026, 8, 10, h, m, s).getTime() / 1000;
const NOW = at(18, 0);

function ep(overrides: Partial<PipelineEpisode> & { kind: string }): PipelineEpisode {
  const start_t = overrides.start_t ?? at(12, 6);
  const duration_s = overrides.duration_s ?? 600;
  return {
    id: "e1",
    start_t,
    end_t: start_t + duration_s,
    duration_s,
    dominant: { scene: "office" },
    open: false,
    ...overrides,
  };
}

function day(overrides: Partial<DayInputs> = {}): DayInputs {
  return { date: DAY, episodes: [], seeded: {}, frameUrls: {}, isToday: false, nowT: NOW, ...overrides };
}

const engineEp = (overrides: Partial<EngineEpisode> & { type: EngineEpisode["type"] }): EngineEpisode => ({
  start_hh: 12,
  minutes: 10,
  ...overrides,
});

describe("episodeToEngine", () => {
  it.each([
    ["screen_block", "screen_block"],
    ["caffeine_sighting", "caffeine_sighting"],
    ["meal", "meal"],
    ["conversation", "conversation"],
    ["outdoor_block", "outdoor_block"],
    ["alcohol_sighting", "alcohol_sighting"],
    ["sauna_session", "sauna"],
    ["gym_session", "gym_session"],
  ])("maps kind %s to type %s", (kind, type) => {
    expect(episodeToEngine(ep({ kind }), { nowT: NOW })?.type).toBe(type);
  });

  it("returns null for kinds the engine does not score", () => {
    expect(episodeToEngine(ep({ kind: "cold_plunge" }), { nowT: NOW })).toBeNull();
    expect(episodeToEngine(ep({ kind: "" }), { nowT: NOW })).toBeNull();
  });

  it("uses the local decimal hour and duration in minutes for a closed episode", () => {
    const out = episodeToEngine(ep({ kind: "screen_block", start_t: at(8, 30, 0), duration_s: 141 }), { nowT: NOW });
    expect(out?.start_hh).toBeCloseTo(8.5, 6);
    expect(out?.minutes).toBe(2.4);
    expect(localDecimalHour(at(22, 15, 36))).toBeCloseTo(22.26, 6);
  });

  it("closes an open episode at nowT, never negative", () => {
    const open = episodeToEngine(ep({ kind: "screen_block", start_t: NOW - 90 * 60 - 20, end_t: null, open: true }), { nowT: NOW });
    expect(open?.minutes).toBe(90.3);
    const future = episodeToEngine(ep({ kind: "screen_block", start_t: NOW + 60, end_t: null, open: true }), { nowT: NOW });
    expect(future?.minutes).toBe(0);
  });

  it("maps scenes onto the engine's nature vocabulary and leaves others alone", () => {
    const scene = (s: unknown) => episodeToEngine(ep({ kind: "outdoor_block", dominant: { scene: s } }), { nowT: NOW })?.scene;
    expect(scene("beach")).toBe("water");
    expect(scene("backyard")).toBe("garden");
    expect(scene("park")).toBe("park");
    expect(scene("street")).toBe("street");
    const noScene = episodeToEngine(ep({ kind: "outdoor_block", dominant: {} }), { nowT: NOW });
    expect(noScene).not.toHaveProperty("scene");
  });

  it("never sets lux — the tick lux_proxy is not lux", () => {
    for (const kind of ["outdoor_block", "screen_block", "meal", "conversation"]) {
      const out = episodeToEngine(ep({ kind, dominant: { scene: "park", lux_proxy: 0.8 } }), { nowT: NOW });
      expect(out).not.toHaveProperty("lux");
    }
  });

  it("gives a conversation one person and an alcohol sighting one drink", () => {
    expect(episodeToEngine(ep({ kind: "conversation" }), { nowT: NOW })?.people).toBe(1);
    expect(episodeToEngine(ep({ kind: "alcohol_sighting" }), { nowT: NOW })?.count).toBe(1);
    expect(episodeToEngine(ep({ kind: "screen_block" }), { nowT: NOW })).not.toHaveProperty("people");
  });

  it("tags meals from the pipeline's own healthy set", () => {
    const rice = episodeToEngine(ep({ kind: "meal", dominant: { scene: "cafe", food_type: "rice_bowl" } }), { nowT: NOW });
    expect(rice?.label).toBe("rice_bowl");
    expect(rice?.tags).toEqual(["rice_bowl", "mediterranean"]);
    const processed = episodeToEngine(ep({ kind: "meal", dominant: { food_type: "processed" } }), { nowT: NOW });
    expect(processed?.tags).toEqual(["processed"]);
    const unknown = episodeToEngine(ep({ kind: "meal", dominant: {} }), { nowT: NOW });
    expect(unknown?.label).toBe("meal");
    expect(unknown?.tags).toEqual(["meal"]);
  });

  it("passes id and kind through and does not attach a frame", () => {
    const out = episodeToEngine(ep({ id: "e_0042", kind: "sauna_session" }), { nowT: NOW });
    expect(out?.id).toBe("e_0042");
    expect(out?.kind).toBe("sauna_session");
    expect(out).not.toHaveProperty("frame_url");
  });
});

describe("habitualBedtime", () => {
  it("is 23 when nothing is seeded", () => {
    expect(habitualBedtime([])).toBe(23);
    expect(habitualBedtime([{ sleep_hours: 7 }])).toBe(23);
  });

  it("normalises after-midnight values and takes the median", () => {
    expect(habitualBedtime([{ bed_time: 23 }, { bed_time: 0.75 }, { bed_time: 23 }])).toBe(23);
    expect(habitualBedtime([{ bed_time: 0.75 }, { bed_time: 23 }])).toBe(23.88);
    expect(habitualBedtime([{ bed_time: 0.5 }, { bed_time: 1 }, { bed_time: 0.75 }])).toBe(24.75);
  });
});

describe("buildWearableDay", () => {
  const seeded = {
    steps: 9100,
    vilpa_minutes: 4.2,
    sleep_hours: 7.4,
    sleep_regularity_sri: 84,
    hrv_rmssd_ratio: 1.02,
    strain: 14.2,
    night_noise_db: 41,
    bed_time: 23,
    recovery_score: 82,
    purpose_score: 4,
    vo2_max: 51,
  };

  it("renames the seeded metrics onto the engine's keys", () => {
    const d = day({ seeded });
    const out = buildWearableDay(d, [], [d]);
    expect(out).toMatchObject({ steps: 9100, vilpa_min: 4.2, sleep_hours: 7.4, sri: 84, hrv_ratio: 1.02, strain: 14.2, night_db: 41 });
  });

  it("never sets the keys the pipeline does not measure, even when a seeded lookalike exists", () => {
    const d = day({ seeded });
    const out = buildWearableDay(d, [], [d]);
    for (const key of ["night_lux", "vo2max_pct", "purpose", "height_m", "smoker"]) expect(out).not.toHaveProperty(key);
  });

  it("omits every key whose seeded value is absent", () => {
    const d = day({ seeded: { steps: 5500 } });
    const out = buildWearableDay(d, [], [d]);
    expect(Object.keys(out).sort()).toEqual(["steps", "workouts"]);
  });

  it("turns gym sessions into strength minutes", () => {
    const d = day({ seeded });
    const out = buildWearableDay(d, [engineEp({ type: "gym_session", minutes: 48 }), engineEp({ type: "outdoor_block" })], [d]);
    expect(out.workouts).toEqual([{ kind: "strength", minutes: 48 }]);
  });

  it("takes baseline sleep as the mean over the week and the bed shift against the week's habit", () => {
    const earlier = day({ date: "2026-09-08", seeded: { sleep_hours: 5.8, bed_time: 0.75 } });
    const middle = day({ date: "2026-09-09", seeded: { bed_time: 23 } });
    const today = day({ seeded: { sleep_hours: 7.4, bed_time: 0.75 } });
    const out = buildWearableDay(today, [], [earlier, middle, today]);
    expect(out.baseline_sleep_h).toBeCloseTo(6.6, 9);
    // habit = median(24.75, 23, 24.75) = 24.75 → tonight is on habit
    expect(out.planned_bed_shift_min).toBe(0);
    const late = buildWearableDay(today, [], [middle, today]);
    // habit = median(23, 24.75) = 23.88 → +52 min
    expect(late.planned_bed_shift_min).toBe(Math.round((24.75 - 23.88) * 60));
  });

  it("omits baseline sleep and bed shift when nobody measured them", () => {
    const d = day({ seeded: { steps: 1 } });
    const out = buildWearableDay(d, [], [d]);
    expect(out).not.toHaveProperty("baseline_sleep_h");
    expect(out).not.toHaveProperty("planned_bed_shift_min");
  });
});

describe("buildWeekRow", () => {
  it("splits outdoor minutes into nature and bright light by scene", () => {
    const eps = [
      engineEp({ type: "outdoor_block", minutes: 20, scene: "park" }),
      engineEp({ type: "outdoor_block", minutes: 15, scene: "street" }),
      engineEp({ type: "outdoor_block", minutes: 5, scene: "water" }),
    ];
    const row = buildWeekRow(day({ seeded: { steps: 7000, vilpa_minutes: 2 } }), eps);
    expect(row.nature_min).toBe(25);
    expect(row.day_light_min).toBe(40);
    expect(row.steps).toBe(7000);
    expect(row.vilpa_min).toBe(2);
  });

  it("uses the engine's social formula and omits it without conversations", () => {
    expect(socialIndex(30, 1)).toBeCloseTo(38, 9);
    expect(socialIndex(120, 9)).toBe(100);
    const row = buildWeekRow(day(), [
      engineEp({ type: "conversation", minutes: 20, people: 1 }),
      engineEp({ type: "conversation", minutes: 10, people: 1 }),
    ]);
    expect(row.social_index).toBeCloseTo(38, 9);
    expect(buildWeekRow(day(), [engineEp({ type: "screen_block" })])).not.toHaveProperty("social_index");
  });

  it("counts a sauna only past 19 minutes and books gym minutes as strength", () => {
    expect(buildWeekRow(day(), [engineEp({ type: "sauna", minutes: 15 })]).sauna).toBe(0);
    expect(buildWeekRow(day(), [engineEp({ type: "sauna", minutes: 25 })]).sauna).toBe(1);
    expect(buildWeekRow(day(), [engineEp({ type: "gym_session", minutes: 40 })]).workouts).toEqual([{ kind: "strength", minutes: 40 }]);
    expect(buildWeekRow(day(), []).workouts).toEqual([]);
  });
});

describe("buildHistory", () => {
  const outdoor = (minutes: number) => ep({ kind: "outdoor_block", start_t: at(18, 30), duration_s: minutes * 60, dominant: { scene: "park" } });
  const days = [
    day({ date: "2026-09-06", episodes: [outdoor(12)], seeded: { hrv_rmssd_ratio: 1.02, strain: 14.2 } }),
    day({ date: "2026-09-07", episodes: [outdoor(18)], seeded: { hrv_rmssd_ratio: 0.82, strain: 6.1 } }),
    day({ date: "2026-09-08", episodes: [outdoor(10)], seeded: { strain: 16.8 } }),
    day({ date: "2026-09-09", episodes: [], seeded: { hrv_rmssd_ratio: 0.84, strain: 5.4 } }),
  ];

  it("aligns outdoor minutes with ln(HRV ratio) of the same day and skips days without HRV", () => {
    const h = buildHistory(days);
    expect(h?.exposure).toEqual([12, 18, 0]);
    expect(h?.outcome).toEqual([Math.log(1.02), Math.log(0.82), Math.log(0.84)]);
    expect(h?.covariates).toEqual([14.2, 6.1, 5.4]);
    // 2026-09-06 is a Sunday.
    expect(h?.weekday).toEqual([0, 1, 3]);
    expect(h).toMatchObject({ exposure_name: "outdoor minutes", outcome_name: "next-night ln HRV ratio", prior_beta: 0.002, prior_se: 0.003 });
  });

  it("omits covariates when any usable day lacks strain", () => {
    const partial = [days[0], day({ date: "2026-09-07", seeded: { hrv_rmssd_ratio: 0.9 } })];
    const h = buildHistory(partial);
    expect(h?.exposure).toHaveLength(2);
    expect(h).not.toHaveProperty("covariates");
  });

  it("is undefined below two usable days", () => {
    expect(buildHistory([days[0]])).toBeUndefined();
    expect(buildHistory([days[0], days[2]])).toBeUndefined();
    expect(buildHistory([])).toBeUndefined();
  });
});

describe("buildDayRequests", () => {
  const days = [
    day({
      date: "2026-09-08",
      episodes: [ep({ id: "a", kind: "outdoor_block", dominant: { scene: "park" }, duration_s: 900 })],
      seeded: { sleep_hours: 7.4, bed_time: 23, hrv_rmssd_ratio: 1.02, steps: 9100 },
      frameUrls: { a: "http://localhost:8010/api/evidence/d1/f1" },
    }),
    day({
      date: "2026-09-09",
      episodes: [ep({ id: "b", kind: "conversation", duration_s: 1200 }), ep({ id: "x", kind: "cold_plunge" })],
      seeded: { sleep_hours: 5.8, bed_time: 0.75, hrv_rmssd_ratio: 0.82, steps: 8500 },
    }),
    day({
      date: DAY,
      episodes: [ep({ id: "c", kind: "gym_session", duration_s: 2400 })],
      seeded: { sleep_hours: 7.6, bed_time: 23, hrv_rmssd_ratio: 1.03, steps: 7900 },
      isToday: true,
    }),
  ];

  it("returns one request per day with history only on the last", () => {
    const reqs = buildDayRequests(days, { age: 20, sex: "M", goal: "average" });
    expect(reqs).toHaveLength(3);
    expect(reqs[0].history).toBeUndefined();
    expect(reqs[1].history).toBeUndefined();
    expect(reqs[2].history?.exposure).toEqual([15, 0, 0]);
  });

  it("grows week_rows with the days before each request", () => {
    const reqs = buildDayRequests(days, {});
    expect(reqs.map((r) => r.week_rows?.length)).toEqual([0, 1, 2]);
    expect(reqs[2].week_rows?.[0]).toMatchObject({ nature_min: 15, day_light_min: 15, steps: 9100 });
    expect(reqs[2].week_rows?.[1]).toMatchObject({ social_index: socialIndex(20, 1), steps: 8500 });
  });

  it("attaches frame urls, drops unscorable kinds and fills the wearable day from the days so far", () => {
    const reqs = buildDayRequests(days, {});
    expect(reqs[0].episodes?.[0].frame_url).toBe("http://localhost:8010/api/evidence/d1/f1");
    expect(reqs[1].episodes?.map((e) => e.id)).toEqual(["b"]);
    expect(reqs[1].episodes?.[0].frame_url).toBeNull();
    expect(reqs[2].wearable_day?.workouts).toEqual([{ kind: "strength", minutes: 40 }]);
    expect(reqs[2].wearable_day?.baseline_sleep_h).toBeCloseTo((7.4 + 5.8 + 7.6) / 3, 9);
    expect(reqs[0].wearable_day?.baseline_sleep_h).toBeCloseTo(7.4, 9);
  });

  it("fills bedtime_hh from the seeded habit unless the profile already has one", () => {
    const filled = buildDayRequests(days, { age: 20 });
    expect(filled[0].profile).toEqual({ age: 20, bedtime_hh: 23 });
    const explicit = buildDayRequests(days, { age: 20, bedtime_hh: 22.5 });
    expect(explicit[2].profile?.bedtime_hh).toBe(22.5);
  });

  it("mapEpisodes keeps only engine kinds in order", () => {
    expect(mapEpisodes(days[1]).map((e) => e.type)).toEqual(["conversation"]);
  });
});
