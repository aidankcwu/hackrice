import { describe, expect, it } from "vitest";
import {
  chipFor,
  chipLabel,
  instrumentProvenance,
  instrumentSource,
  instrumentTiles,
  signedUnit,
  UNMEASURED,
  withUnit,
  type FactorRow,
  type InstrumentSource,
  type InstrumentTile,
  type ObsProvenance,
} from "./instruments";
import { contextFor, FACTOR_ORIGIN, provenanceOf, type PageSource } from "./provenance";
import { LAYER_SPECS, layerViewRows } from "@/components/brian/Layers";
import type { DashboardData, EngineFactor } from "./types";

const LIVE: PageSource = { mode: "live", demo_mode: false };

/** Pages whose glasses filed nothing today, and nothing all week. */
const COVERAGE_CONTEXTS: PageSource[] = [
  { ...LIVE, glasses_coverage: { today: false, week: true } },
  { ...LIVE, glasses_coverage: { today: false, week: false }, wearable_sources: { steps: "healthkit" } },
];

/** Written out rather than read from provenance.ts, so the gate is checked against a spec, not itself. */
const GLASSES_TODAY = ["gait_speed", "day_light_min", "social_index", "med_adherence", "alcohol_drinks", "last_caffeine_hh", "night_screen_min"];
const GLASSES_WEEK = ["resistance_min_wk", "nature_min_wk", "sauna_wk"];

/** True when the page's coverage cannot back a glasses value for `key`. */
const uncovered = (key: string, ctx: PageSource): boolean =>
  ctx.glasses_coverage !== undefined &&
  ((GLASSES_TODAY.includes(key) && !ctx.glasses_coverage.today) || (GLASSES_WEEK.includes(key) && !ctx.glasses_coverage.week));

function source(provenance: Record<string, ObsProvenance>, observations: Record<string, number>): InstrumentSource {
  return { observations, provenance, forecast: { melatonin_delay_min: 0, drivers: [] }, bedtime_hh: 23, trailing: [] };
}

const tile = (tiles: InstrumentTile[], key: InstrumentTile["key"]): InstrumentTile => {
  const t = tiles.find((x) => x.key === key);
  if (t === undefined) throw new Error(`no ${key} tile`);
  return t;
};

describe("instrument tile provenance", () => {
  it("labels a factor the glasses measured Glasses, even on a live WHOOP day (never Seeded / WHOOP)", () => {
    const factors: FactorRow[] = [{ key: "social_index", label: "Social integration", measured: true }];
    const tiles = instrumentTiles(source(instrumentProvenance(factors, LIVE), { social_index: 72 }));
    const people = tile(tiles, "people");
    expect(people.measured).toBe(true);
    expect(people.chip).toBe("glasses");
    expect(chipLabel(people.chip)).toBe("Glasses");
  });

  it("labels a HealthKit row Apple Health", () => {
    const prov = instrumentProvenance([{ key: "sleep_hours", label: "Sleep", measured: true }], {
      ...LIVE,
      wearable_sources: { sleep_hours: "healthkit" },
    });
    expect(chipLabel(chipFor(prov.sleep_hours))).toBe("Apple Health");
    // The backend's own shape for a HealthKit-filed light row, read by a tile.
    const light = tile(
      instrumentTiles(source({ day_light_min: { source: "live", basis: "healthkit", detail: "healthkit row" } }, { day_light_min: 50 })),
      "light",
    );
    expect(chipLabel(light.chip)).toBe("Apple Health");
  });

  it("labels a demo-seed row Seeded, whatever device the seed imitates", () => {
    const prov = instrumentProvenance([{ key: "steps", label: "Steps", measured: true }], {
      ...LIVE,
      wearable_sources: { steps: "whoop" },
    });
    expect(prov.steps).toMatchObject({ source: "seeded", basis: "whoop" });
    expect(chipLabel(chipFor(prov.steps))).toBe("Seeded");
    expect(chipFor({ source: "seeded", basis: "apple_watch", detail: "" })).toBe("seeded");
  });

  it("leaves an unmeasured factor missing, with the voice.md string, and never a number", () => {
    const factors: FactorRow[] = [{ key: "nature_min_wk", label: "Nature", measured: false }];
    const prov = instrumentProvenance(factors, LIVE);
    expect(prov.nature_min_wk).toMatchObject({ source: "missing", detail: UNMEASURED });
    const outside = tile(instrumentTiles(source(prov, {})), "outside");
    expect(outside.measured).toBe(false);
    expect(outside.number).toBeNull();
    expect(outside.chip).toBe("imputed");
    expect(outside.status).toBe("unmeasured");
    expect(outside.reason).toBe(UNMEASURED);
  });

  it("names live devices with provenance.ts's labels", () => {
    expect(chipLabel(chipFor({ source: "live", basis: "fitbit", detail: "" }))).toBe("Fitbit");
    expect(chipLabel(chipFor({ source: "live", basis: "apple_watch_live", detail: "" }))).toBe("Apple Health");
    expect(chipLabel(chipFor({ source: "derived", basis: "fitbit", detail: "" }))).toBe("Fitbit");
    expect(chipLabel(chipFor({ source: "derived", basis: "glasses", detail: "" }))).toBe("Glasses");
    expect(chipLabel(chipFor({ source: "live", basis: "openaq", detail: "" }))).toBe("Seeded");
  });

  it("agrees with the By-layer derivation for every factor, measured or not, in every context", () => {
    const contexts: PageSource[] = [
      LIVE,
      { mode: "mock" },
      { ...LIVE, wearable_sources: { sleep_hours: "fitbit", steps: "healthkit", sleep_regularity_sri: "whoop" } },
      ...COVERAGE_CONTEXTS,
    ];
    for (const ctx of contexts) {
      const fallback = ctx.mode === "live" && ctx.demo_mode !== true ? "whoop" : "seeded";
      for (const key of Object.keys(FACTOR_ORIGIN)) {
        for (const measured of [true, false]) {
          const prov = instrumentProvenance([{ key, label: key, measured }], ctx);
          const expected = uncovered(key, ctx)
            ? "imputed"
            : provenanceOf(key, measured, contextFor(key, ctx.wearable_sources, fallback));
          expect(chipFor(prov[key]), `${key} measured=${measured}`).toBe(expected);
        }
      }
    }
  });

  it("By layer names the same stream as the tiles for every row's lead factor, in every context", () => {
    const contexts: PageSource[] = [
      LIVE,
      { mode: "live", demo_mode: true },
      { ...LIVE, wearable_sources: { sleep_hours: "fitbit", steps: "healthkit", sleep_regularity_sri: "whoop" } },
      ...COVERAGE_CONTEXTS,
    ];
    for (const ctx of contexts) {
      for (const spec of LAYER_SPECS) {
        const key = spec.keys[0];
        const factor = { key, label: key, measured: true, dose: 1, hours: 0.1 } as EngineFactor;
        const d = { factors: [factor], layers: [], source: ctx } as unknown as DashboardData;
        const row = layerViewRows(d).find((r) => r.name === spec.name);
        const tileChip = chipFor(instrumentProvenance([factor], ctx)[key]);
        expect(row?.provenance, `${spec.name} (${key})`).toBe(tileChip);
        if (uncovered(key, ctx)) {
          expect(row?.provenance, `${spec.name} (${key}) uncovered`).toBe("imputed");
          expect(row?.measured).toBe(0);
          expect(row?.hours).toBe(0);
        }
      }
    }
  });
});

describe("glasses coverage (a zero from the glasses is a measurement only on a day with episodes)", () => {
  const input = (observations: Record<string, number>, coverage: { today: boolean; week: boolean }, delay = 0) =>
    instrumentSource({
      factors: [
        { key: "day_light_min", label: "Bright light minutes", measured: "day_light_min" in observations },
        { key: "nature_min_wk", label: "Time in nature", measured: "nature_min_wk" in observations },
      ],
      source: { ...LIVE, glasses_coverage: coverage },
      observations,
      forecast: { melatonin_delay_min: delay, drivers: [] },
      bedtime_hh: 23,
    });
  // What the engine returns on a day the glasses never ran: its sums default to 0.
  const ENGINE_ZEROS = { night_screen_min: 0, day_light_min: 0, nature_min_wk: 0, alcohol_drinks: 0 };

  it("renders clock and Light unmeasured on a day with no glasses episode, never a measured 0", () => {
    const tiles = instrumentTiles(input(ENGINE_ZEROS, { today: false, week: false }));
    for (const key of ["clock", "light", "outside"] as const) {
      const t = tile(tiles, key);
      expect(t.measured, key).toBe(false);
      expect(t.number, key).toBeNull();
      expect(t.chip, key).toBe("imputed");
      expect(t.status, key).toBe("unmeasured");
      expect(t.unmeasuredNote, key).toBe(UNMEASURED);
    }
    expect(tile(tiles, "clock").reason).toBe("no glasses episodes today");
    expect(tile(tiles, "light").reason).toBe("no glasses episodes today");
    expect(tile(tiles, "outside").reason).toBe("no glasses episodes this week");
    expect(tile(tiles, "light").detail.find((r) => r.label === "Screens after 22:00")?.value).toBe("—");
  });

  it("keeps a 0 from a day the glasses were on as a real measurement", () => {
    const tiles = instrumentTiles(input(ENGINE_ZEROS, { today: true, week: true }));
    const clock = tile(tiles, "clock");
    expect(clock.measured).toBe(true);
    expect(clock.chip).toBe("glasses");
    expect(clock.number).toBe(signedUnit(0, "min"));
    const light = tile(tiles, "light");
    expect(light.measured).toBe(true);
    expect(light.number).toBe(withUnit(0, "min"));
  });

  it("scores the weekly glasses values on the week's coverage, today's on today's", () => {
    const tiles = instrumentTiles(input({ ...ENGINE_ZEROS, nature_min_wk: 84 }, { today: false, week: true }));
    expect(tile(tiles, "outside").measured).toBe(true);
    expect(tile(tiles, "light").measured).toBe(false);
    expect(tile(tiles, "clock").measured).toBe(false);
  });
});

describe("clock tile provenance (a leading indicator, not a factor)", () => {
  const withObs = (observations: Record<string, number>, delay = 0): InstrumentSource => ({
    observations,
    provenance: instrumentProvenance([], LIVE, observations),
    forecast: { melatonin_delay_min: delay, drivers: [] },
    bedtime_hh: 23,
    trailing: [],
  });

  it("is measured, and Glasses, when the engine put night screen minutes in observations", () => {
    const clock = tile(instrumentTiles(withObs({ night_screen_min: 40 }, 10)), "clock");
    expect(clock.measured).toBe(true);
    expect(clock.chip).toBe("glasses");
    expect(chipLabel(clock.chip)).toBe("Glasses");
    expect(clock.number).toBe(signedUnit(10, "min"));
    expect(clock.chipDetail).toContain("40");
    expect(clock.status).toBe("near");
  });

  it("counts a measured zero as measured, not as missing", () => {
    const clock = tile(instrumentTiles(withObs({ night_screen_min: 0 })), "clock");
    expect(clock.measured).toBe(true);
    expect(clock.chip).toBe("glasses");
    expect(clock.number).toBe(signedUnit(0, "min"));
  });

  it("is unmeasured, Imputed, with the voice.md string, when observations lack them", () => {
    const src = withObs({ day_light_min: 30 }, 25);
    expect(src.provenance.night_screen_min).toMatchObject({ source: "missing", basis: "glasses", detail: UNMEASURED });
    const clock = tile(instrumentTiles(src), "clock");
    expect(clock.measured).toBe(false);
    expect(clock.number).toBeNull();
    expect(clock.chip).toBe("imputed");
    expect(clock.status).toBe("unmeasured");
    expect(clock.unmeasuredNote).toBe(UNMEASURED);
    expect(clock.detail.find((r) => r.label === "Predicted delay tonight")?.value).toBe("—");
  });

  it("lets an engine factor of the same key win over the observation", () => {
    const prov = instrumentProvenance([{ key: "night_screen_min", label: "Screens", measured: false }], LIVE, { night_screen_min: 40 });
    expect(prov.night_screen_min.source).toBe("missing");
  });
});
