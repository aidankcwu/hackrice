import { describe, expect, it } from "vitest";
import {
  doseNote,
  effectRows,
  forecastView,
  layerHours,
  layerRows,
  ledgerRows,
  leverRows,
  pinRows,
  shapeDashboard,
  weekDays,
  weekSummary,
} from "./shape";
import type { DayInputs, EngineFactor, EnginePayload, LayerName, Person, WeekDay } from "./types";
import { LAYER_ORDER } from "./types";
import { LAYER_DISCOUNT } from "./units";

const at = (h: number, m = 0): number => new Date(2026, 8, 12, h, m, 0).getTime() / 1000;

function factor(key: string, layer: LayerName, hours: number, dose: number | null = 1, measured = dose !== null): EngineFactor {
  return { key, layer, label: key.replace(/_/g, " "), dose, hr: measured ? 0.9 : null, hours, grade: "A_cohort", measured, source: "test" };
}

/**
 * Hand-built fixture: hours per layer chosen so the discounted layer sums are
 * exact at two decimals and add to `hours_today` (0.64).
 */
const FACTORS: EngineFactor[] = [
  factor("steps", "Movement", 0.9, 9100),
  factor("vilpa_min", "Movement", -0.3, 2),
  factor("resistance_min_wk", "Movement", 0.2, 40),
  factor("fitness_pct", "Movement", 0, null),
  factor("gait_speed", "Movement", 0, null),
  factor("sleep_hours", "Sleep", 0.1, 7.4),
  factor("sri", "Sleep", 0.4, 84),
  factor("day_light_min", "Light & clock", 0.15, 44),
  factor("night_light_lux", "Light & clock", 0, null),
  factor("social_index", "Social", -0.2, 65),
  factor("purpose", "Social", 0, null),
  factor("nature_min_wk", "Environment", -0.05, 35),
  factor("noise_night_db", "Environment", 0, 45),
  factor("med_adherence", "Diet & substances", 0.1, 0.6),
  factor("alcohol_drinks", "Diet & substances", -0.6, 2),
  factor("smoker", "Diet & substances", 0, 0),
  factor("sauna_wk", "Recovery", 0, 0),
  factor("recovery_ratio", "Recovery", 0.02, 1.02),
];

const EXPECTED_LAYER_HOURS: Record<LayerName, number> = {
  Movement: 0.8,
  Sleep: 0.46,
  "Light & clock": 0.15,
  Social: -0.2,
  Environment: -0.05,
  "Diet & substances": -0.54,
  Recovery: 0.02,
  // No PVT in the fixture, so the cognition factor is unmeasured and earns nothing.
  Cognition: 0,
};

function payload(overrides: Partial<EnginePayload> = {}): EnginePayload {
  return {
    overall: 71,
    layers: { Movement: 80.4, Sleep: 70, "Light & clock": 60, Social: 55, Environment: 50, "Diet & substances": 40, Recovery: 65, Cognition: 65 },
    years_delta: 1.2,
    years_ci: [0.8, 1.6],
    hours_today: 0.64,
    hours_ci: [0.4, 0.88],
    factors: FACTORS,
    ledger: [
      { key: "nature_min_wk", label: "Time in nature", accrued: 35, target: 120, projected: 61.3, deficit: 58.7, days_elapsed: 4, status: "behind" },
      { key: "steps", label: "Daily steps", accrued: 8100, target: 8000, projected: 8100, deficit: 0, days_elapsed: 4, status: "on_track" },
    ],
    forecast: { sleep_hours: 6.9, hrv_change_pct: -12, sri_change_pts: 0, melatonin_delay_min: 10, drivers: [] },
    levers: [
      { key: "vilpa_min", label: "Vigorous bursts", action: "Vigorous bursts: 2 → 5 min/day", hours_gain: 0.3, time_min: 3, roi_hours_per_min: 0.1, layers: ["movement"], source: "Stamatakis 2022" },
    ],
    insights: [{ kind: "today", text: "Today nets +0.6 h.", source: "test" }],
    pins: [
      { time: "08:41", img: "http://x/f1", grade: "A", kind: "credit", seen: "Outdoors, park", effect: "bright light before 10:00" },
      { time: "12:05", img: null, grade: "A", kind: "credit", seen: "Conversation, 41 min", effect: "counts toward social integration" },
      { time: "12:08", img: null, grade: "A", kind: "debit", seen: "Alcohol in frame", effect: "HRV -12% tonight" },
      { time: "16:00", img: null, grade: "B", kind: "debit", seen: "Caffeine at 16:00", effect: "inside your cutoff" },
      { time: "22:12", img: null, grade: "C", kind: "debit", seen: "Screen, 40 min after 22:00", effect: "melatonin delayed" },
      { time: "12:06", img: null, grade: "A", kind: "credit", seen: "Meal: rice_bowl", effect: "tagged" },
      { time: "19:00", img: null, grade: "B", kind: "credit", seen: "Sauna", effect: "session counted" },
      { time: "07:00", img: null, grade: "A", kind: "credit", seen: "Walking, cadence 112", effect: "gait sample" },
      { time: "09:00", img: null, grade: "A", kind: "credit", seen: "Something new", effect: "" },
    ],
    effects: [],
    observations: { steps: 9100, night_screen_min: 40, alcohol_drinks: 2, last_caffeine_hh: 16 },
    ...overrides,
  };
}

const PERSON: Person = { name: "Bryan", age: 20, sex: "M", goal: "average", profileLabel: "Average", device: "Ray-Ban Meta + whoop", bedtime_hh: 23 };

function day(overrides: Partial<DayInputs> = {}): DayInputs {
  return { date: "2026-09-12", episodes: [], seeded: {}, frameUrls: {}, isToday: false, nowT: at(18), ...overrides };
}

describe("layerHours", () => {
  it("discounts by rank of |h| like the engine's _layer_sum", () => {
    expect(layerHours([0.5, -0.2, 0.1])).toBeCloseTo(0.5 * 1 + -0.2 * 0.6 + 0.1 * 0.4, 12);
    expect(layerHours([-0.2, 0.1, 0.5])).toBeCloseTo(layerHours([0.5, -0.2, 0.1]), 12);
    expect(layerHours([])).toBe(0);
  });

  it("uses the last discount for every factor past the sixth", () => {
    const eight = [8, 7, 6, 5, 4, 3, 2, 1];
    const expected = eight.reduce((sum, h, i) => sum + h * LAYER_DISCOUNT[Math.min(i, 5)], 0);
    expect(layerHours(eight)).toBeCloseTo(expected, 12);
  });
});

describe("layerRows", () => {
  it("reproduces the engine's layer sums so the seven rows add up to hours_today", () => {
    const rows = layerRows(payload());
    expect(rows.map((r) => r.name)).toEqual([...LAYER_ORDER]);
    for (const row of rows) expect(row.hours).toBeCloseTo(EXPECTED_LAYER_HOURS[row.name], 9);
    const total = rows.reduce((sum, r) => sum + r.hours, 0);
    expect(total).toBeCloseTo(payload().hours_today, 9);
  });

  it("counts measured factors, rounds the score and picks the icon", () => {
    const rows = layerRows(payload());
    const movement = rows[0];
    expect(movement).toMatchObject({ name: "Movement", icon: "footprints", score: 80, measured: 3, total: 5 });
    expect(movement.note).toBe("9,100 steps · 2 min hard effort · 40 min strength this week");
    expect(rows.find((r) => r.name === "Sleep")?.icon).toBe("moon");
    expect(rows.find((r) => r.name === "Diet & substances")?.icon).toBe("wine");
  });

  it("says so when nothing in a layer was measured", () => {
    const only = payload({ factors: [factor("purpose", "Social", 0, null)], layers: { ...payload().layers, Social: 50 } });
    const social = layerRows(only).find((r) => r.name === "Social");
    expect(social).toMatchObject({ note: "nothing measured today", measured: 0, total: 1, hours: 0 });
    // A layer with no factors at all is still listed, at zero.
    expect(layerRows(only).find((r) => r.name === "Recovery")).toMatchObject({ measured: 0, total: 0, hours: 0 });
  });
});

describe("doseNote", () => {
  it("formats each key the way the brief spells it", () => {
    const note = doseNote([
      factor("steps", "Movement", 0, 9100),
      factor("vilpa_min", "Movement", 0, 2),
      factor("resistance_min_wk", "Movement", 0, 40),
      factor("fitness_pct", "Movement", 0, 70),
      factor("gait_speed", "Movement", 0, 1.35),
      factor("sleep_hours", "Sleep", 0, 7.4),
      factor("sri", "Sleep", 0, 84),
      factor("day_light_min", "Light & clock", 0, 44),
      factor("night_light_lux", "Light & clock", 0, 2),
      factor("social_index", "Social", 0, 65),
      factor("purpose", "Social", 0, 4),
      factor("nature_min_wk", "Environment", 0, 35),
      factor("noise_night_db", "Environment", 0, 45),
      factor("med_adherence", "Diet & substances", 0, 0.6),
      factor("alcohol_drinks", "Diet & substances", 0, 2),
      factor("smoker", "Diet & substances", 0, 0),
      factor("sauna_wk", "Recovery", 0, 0),
      factor("recovery_ratio", "Recovery", 0, 1.02),
    ]);
    expect(note).toBe(
      [
        "9,100 steps",
        "2 min hard effort",
        "40 min strength this week",
        "fitness p70",
        "1.35 m/s",
        "7.4 h",
        "regularity 84",
        "44 bright min",
        "2 lx at night",
        "social 65",
        "purpose 4/6",
        "35 min in nature this week",
        "45 dB at night",
        "meals 60% on pattern",
        "2 drinks",
        "no nicotine",
        "0 sauna sessions",
        "HRV 1.02× baseline",
      ].join(" · "),
    );
  });

  it("handles singulars, nicotine and unknown keys", () => {
    expect(doseNote([factor("alcohol_drinks", "Diet & substances", 0, 1)])).toBe("1 drink");
    expect(doseNote([factor("alcohol_drinks", "Diet & substances", 0, 0)])).toBe("no drinks");
    expect(doseNote([factor("smoker", "Diet & substances", 0, 1)])).toBe("nicotine daily");
    expect(doseNote([factor("sauna_wk", "Recovery", 0, 1)])).toBe("1 sauna session");
    expect(doseNote([factor("new_thing", "Recovery", 0, 3.14159)])).toBe("new thing 3.14");
    expect(doseNote([factor("steps", "Movement", 0, null)])).toBe("");
  });
});

describe("pinRows", () => {
  it("maps credit/debit to earn/cost and picks icons from the seen prefix", () => {
    const rows = pinRows(payload());
    expect(rows.map((r) => r.icon)).toEqual(["sun", "users", "wine", "coffee", "smartphone", "utensils", "flame", "footprints", "eye"]);
    expect(rows.map((r) => r.kind)).toEqual(["earn", "earn", "cost", "cost", "cost", "earn", "earn", "earn", "earn"]);
    expect(rows[0]).toMatchObject({ id: "08:41-0", time: "08:41", img: "http://x/f1", grade: "A", seen: "Outdoors, park" });
    expect(rows[1].img).toBeNull();
  });
});

describe("forecastView", () => {
  it("writes the fix from screens, caffeine and alcohol drivers", () => {
    const fc = forecastView(
      payload({
        forecast: {
          sleep_hours: 6.1,
          hrv_change_pct: -12,
          sri_change_pts: 0,
          melatonin_delay_min: 10,
          drivers: ["caffeine at 16:00 is inside your 9 h cutoff", "40 min of screens after 22:00", "2 drink(s) — expect a lower HRV tonight"],
        },
      }),
      23,
      true,
    );
    expect(fc.bedtime).toBe("23:00");
    expect(fc.sleep_hours).toBe(6.1);
    expect(fc.fix).toBe("No more screens tonight recovers about 0.2 h of sleep. The coffee and the drinks are already booked.");
  });

  it("names a single booked driver and the bedtime shift", () => {
    const base = payload({ observations: {} });
    const coffee = forecastView({ ...base, forecast: { ...base.forecast, drivers: ["caffeine at 16:00 is inside your 9 h cutoff"] } }, 23, true);
    expect(coffee.fix).toBe("The coffee is already booked.");
    const drinks = forecastView({ ...base, forecast: { ...base.forecast, drivers: ["1 drink(s) — expect a lower HRV tonight"] } }, 23, true);
    expect(drinks.fix).toBe("The drinks are already booked.");
    const shift = forecastView({ ...base, forecast: { ...base.forecast, drivers: ["bedtime +105 min vs habit"] } }, 24.75, true);
    expect(shift.fix).toBe("Lights out at 00:45 keeps your regularity score.");
  });

  it("says nothing is dragging when there are no drivers", () => {
    const fc = forecastView(payload({ observations: { night_screen_min: 0 } }), 23.5, true);
    expect(fc.fix).toBe("Nothing is dragging tonight down. Lights out near 23:30 keeps it that way.");
  });

  it("never quotes the engine's default bedtime as a habit nobody measured", () => {
    const base = payload({ observations: {} });
    const fc = forecastView({ ...base, forecast: { ...base.forecast, drivers: [] } }, 23, false);
    expect(fc.bedtime).toBe("—");
    expect(fc.fix).toBe("Nothing measured today is dragging tonight down.");
    // A shift driver only exists when a real bed_time was recorded, so it may name the hour.
    const shift = forecastView({ ...base, forecast: { ...base.forecast, drivers: ["bedtime +105 min vs habit"] } }, 24.75, false);
    expect(shift.fix).toBe("Lights out at 00:45 keeps your regularity score.");
  });
});

describe("leverRows and ledgerRows", () => {
  it("renames lever fields", () => {
    expect(leverRows(payload())).toEqual([
      { key: "vilpa_min", action: "Vigorous bursts: 2 → 5 min/day", gain: 0.3, time: 3, layers: ["movement"], source: "Stamatakis 2022" },
    ]);
  });

  it("attaches a display unit to each ledger line", () => {
    const rows = ledgerRows(payload());
    expect(rows[0]).toMatchObject({ key: "nature_min_wk", unit: "min", status: "behind" });
    expect(rows[1]).toMatchObject({ key: "steps", unit: "steps" });
    expect(ledgerRows(payload({ ledger: [{ ...payload().ledger[0], key: "mystery" }] }))[0].unit).toBe("");
  });
});

describe("effectRows", () => {
  const effect = (over: Partial<EnginePayload["effects"][number]>) => ({
    exposure: "outdoor minutes",
    outcome: "next-night ln HRV ratio",
    beta: 0.004,
    ci: [0.001, 0.007] as [number, number],
    n: 30,
    blended_beta: 0.0035,
    note: "personal estimate",
    ...over,
  });

  it("reports the population prior below 14 days", () => {
    const [row] = effectRows(payload({ effects: [effect({ beta: null, ci: [null, null], n: 7, note: "fewer than 14 days — showing population prior" })] }));
    expect(row).toMatchObject({ beta: "fewer than 14 days — population prior shown", lo: null, hi: null, n: 7, ok: false });
  });

  it("formats ln-per-minute betas as percent per 10 min, others per unit", () => {
    const [ok] = effectRows(payload({ effects: [effect({})] }));
    // 0.004 ln-units per minute = 4.0 % per 10 min.
    expect(ok).toMatchObject({ beta: "+4.0% per 10 min", lo: 0.001, hi: 0.007, ok: true });
    const [neg] = effectRows(payload({ effects: [effect({ beta: -0.0021, note: "not yet distinguishable from zero" })] }));
    expect(neg).toMatchObject({ beta: "−2.1% per 10 min", ok: false });
    const [unit] = effectRows(payload({ effects: [effect({ exposure: "sauna sessions", outcome: "recovery score", beta: 1.23456 })] }));
    expect(unit.beta).toBe("+1.235 per unit");
    const [few] = effectRows(payload({ effects: [effect({ n: 13 })] }));
    expect(few.ok).toBe(false);
  });
});

describe("weekDays", () => {
  const coffee = (id: string, h: number, m = 0) => ({
    id,
    kind: "caffeine_sighting",
    start_t: at(h, m),
    end_t: at(h, m) + 90,
    duration_s: 90,
    dominant: { scene: "office" },
    open: false,
  });
  const days: DayInputs[] = [
    day({
      date: "2026-09-06",
      seeded: { bed_time: 23, sleep_hours: 7.4, hrv_rmssd_ratio: 1.02, recovery_score: 82, resting_hr: 58, steps: 9100, sleep_regularity_sri: 84 },
      episodes: [coffee("m", 8, 15)],
    }),
    day({ date: "2026-09-07", seeded: { bed_time: 0.75, sleep_hours: 5.8, journal_caffeine_late: 1 }, episodes: [] }),
    day({ date: "2026-09-08", seeded: { journal_alcohol: 1, journal_nicotine: 1, journal_cannabis: 1 }, episodes: [coffee("l", 16, 30)] }),
    day({
      date: "2026-09-12",
      seeded: {},
      episodes: [
        { id: "a", kind: "alcohol_sighting", start_t: at(20), end_t: at(20) + 40, duration_s: 40, dominant: {}, open: false },
        { id: "b", kind: "alcohol_sighting", start_t: at(21), end_t: at(21) + 40, duration_s: 40, dominant: {}, open: false },
      ],
      isToday: true,
    }),
  ];

  it("formats bedtimes on a 24 h clock and blanks missing ones", () => {
    const week = weekDays([], days, 23);
    expect(week.map((d) => d.bed)).toEqual(["23:00", "00:45", "—", "—"]);
    expect(week.map((d) => d.day)).toEqual(["Sun", "Mon", "Tue", "Sat"]);
    expect(week[0]).toMatchObject({ date: "2026-09-06", sleep: 7.4, hrv: 1.02, rec: 82, rhr: 58, steps: 9100, sri: 84, today: false });
    expect(week[3]).toMatchObject({ sleep: null, hrv: null, rec: null, rhr: null, steps: null, sri: null, today: true });
  });

  it("tags caffeine from the journal or a sighting after the cutoff, alcohol from either source, and dedupes", () => {
    const week = weekDays([], days, 23);
    expect(week.map((d) => d.tag)).toEqual(["", "caffeine", "caffeine, alcohol, nicotine, cannabis", "alcohol"]);
    // A later habitual bedtime moves the cutoff: 16:30 is inside 9 h of 01:30 (25.5).
    expect(weekDays([], days, 25.5)[2].tag).toBe("alcohol, nicotine, cannabis");
  });

  it("takes hours from the aligned payload and null when there is none", () => {
    const week = weekDays([payload({ hours_today: 1.1 }), payload({ hours_today: -0.4 })], days, 23);
    expect(week.map((d) => d.hours)).toEqual([1.1, -0.4, null, null]);
  });
});

describe("weekSummary", () => {
  const row = (over: Partial<WeekDay>): WeekDay => ({
    day: "Mon",
    date: "2026-09-07",
    bed: "23:00",
    sleep: null,
    hrv: null,
    rec: null,
    rhr: null,
    steps: null,
    sri: null,
    hours: null,
    tag: "",
    today: false,
    ...over,
  });

  it("compares late-caffeine days with the rest and names the worst", () => {
    const text = weekSummary([
      row({ day: "Sun", sleep: 7.4, rec: 82, hours: 1.2 }),
      row({ day: "Mon", sleep: 5.8, rec: 34, hours: -0.9, tag: "caffeine" }),
      row({ day: "Tue", sleep: 7.6, rec: 86, hours: 0.8 }),
      row({ day: "Wed", sleep: 6.0, rec: 39, hours: -0.4, tag: "caffeine, alcohol" }),
    ]);
    expect(text).toBe(
      "2 late-caffeine days averaged 5.9 h of sleep and recovery 37; 2 without late caffeine averaged 7.5 h and 84. Mon was the worst: −0.9 h.",
    );
  });

  it("drops recovery and the worst clause when they are missing", () => {
    const text = weekSummary([row({ day: "Sun", sleep: 7.4 }), row({ day: "Mon", sleep: 5.8, tag: "caffeine" })]);
    expect(text).toBe("1 late-caffeine day averaged 5.8 h of sleep; 1 without late caffeine averaged 7.4 h.");
  });

  it("counts only the days that carried the number, never the whole bucket", () => {
    // Four late-caffeine days, two with a sleep row and one with recovery: the
    // sentence may claim 2 and 1, never 4.
    const text = weekSummary([
      row({ day: "Sun", sleep: 7.4, rec: 82 }),
      row({ day: "Mon", sleep: 5.8, rec: 34, tag: "caffeine" }),
      row({ day: "Tue", sleep: 6.0, tag: "caffeine" }),
      row({ day: "Wed", tag: "caffeine" }),
      row({ day: "Thu", tag: "caffeine" }),
    ]);
    expect(text).toBe(
      "2 late-caffeine days averaged 5.9 h of sleep and recovery 34 over 1 of them; 1 without late caffeine averaged 7.4 h and 82.",
    );
  });

  it("keeps the comparison when one side has no tagged day at all", () => {
    // Every day is a late-caffeine day, so the "without" half is zero days and
    // the split sentence is not written.
    const text = weekSummary([row({ day: "Mon", sleep: 5.8, hours: -0.9, tag: "caffeine" })]);
    expect(text).toBe("Mon is the only scored day, at −0.9 h.");
  });

  it("falls back to worst and best when there is no caffeine split", () => {
    const text = weekSummary([row({ day: "Sun", hours: 1.2 }), row({ day: "Mon", hours: -0.9 }), row({ day: "Tue", hours: 0.8 })]);
    expect(text).toBe("Mon was the worst day at −0.9 h; Sun the best at +1.2 h.");
    expect(weekSummary([row({ day: "Sat", hours: 0 })])).toBe("Sat is the only scored day, at 0.0 h.");
    expect(weekSummary([row({})])).toBe("No day has been scored yet.");
  });
});

describe("shapeDashboard", () => {
  const SOURCE = { mode: "live" as const, api_base: "http://localhost:8010", day: "2026-09-12" };

  it("assembles today's payload with the week and passes engine fields through", () => {
    const days = [
      day({ date: "2026-09-11", seeded: { sleep_hours: 7, bed_time: 23 }, nowT: at(10) }),
      day({ isToday: true, nowT: at(18, 30) }),
    ];
    const payloads = [payload({ hours_today: 0.3 }), payload()];
    const data = shapeDashboard({ payloads, days, person: PERSON, source: SOURCE, engineMs: 412 });
    expect(data.generated_at).toBe(at(18, 30));
    expect(data.engine_ms).toBe(412);
    expect(data.source).toEqual({ ...SOURCE, glasses_coverage: { today: false, week: false } });
    expect(data.person).toBe(PERSON);
    expect(data).toMatchObject({ overall: 71, hours_today: 0.64, hours_ci: [0.4, 0.88], years_delta: 1.2, years_ci: [0.8, 1.6] });
    expect(data.layers).toHaveLength(8);
    expect(data.pins).toHaveLength(9);
    expect(data.forecast.bedtime).toBe("23:00");
    expect(data.week.map((d) => d.hours)).toEqual([0.3, 0.64]);
    expect(data.week[1].today).toBe(true);
    expect(data.week_summary).toBe("Fri was the worst day at +0.3 h; Sat the best at +0.6 h.");
    expect(data.factors).toBe(payloads[1].factors);
    expect(data.insights).toEqual(payload().insights);
    expect(data.observations).toEqual(payload().observations);
  });

  it("blanks the bedtime when no night in the window recorded one", () => {
    const days = [day({ date: "2026-09-11", nowT: at(10) }), day({ isToday: true, nowT: at(18, 30) })];
    const data = shapeDashboard({ payloads: [payload(), payload()], days, person: PERSON, source: SOURCE, engineMs: 1 });
    expect(data.forecast.bedtime).toBe("—");
  });

  it("marks glasses coverage from the day inputs' episodes, today and across the week", () => {
    const walk = { id: "e1", kind: "outdoor_block", start_t: at(9), end_t: at(9, 30), duration_s: 1800, dominant: {}, open: false };
    const shaped = (earlier: DayInputs["episodes"], today: DayInputs["episodes"]) =>
      shapeDashboard({
        payloads: [payload(), payload()],
        days: [day({ date: "2026-09-11", episodes: earlier }), day({ isToday: true, episodes: today })],
        person: PERSON,
        source: SOURCE,
        engineMs: 1,
      }).source.glasses_coverage;
    expect(shaped([], [])).toEqual({ today: false, week: false });
    expect(shaped([walk], [])).toEqual({ today: false, week: true });
    expect(shaped([], [walk])).toEqual({ today: true, week: true });
  });

  it("refuses an empty run rather than inventing a day", () => {
    expect(() => shapeDashboard({ payloads: [], days: [], person: PERSON, source: SOURCE, engineMs: 0 })).toThrow(/at least one payload/);
  });
});
