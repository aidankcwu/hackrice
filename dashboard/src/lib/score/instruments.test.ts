import { describe, expect, it } from "vitest";
import {
  chipFor,
  chipLabel,
  instrumentProvenance,
  instrumentTiles,
  UNMEASURED,
  type FactorRow,
  type InstrumentSource,
  type InstrumentTile,
  type ObsProvenance,
} from "./instruments";
import { contextFor, FACTOR_ORIGIN, provenanceOf, type PageSource } from "./provenance";

const LIVE: PageSource = { mode: "live", demo_mode: false };

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
    ];
    for (const ctx of contexts) {
      const fallback = ctx.mode === "live" && ctx.demo_mode !== true ? "whoop" : "seeded";
      for (const key of Object.keys(FACTOR_ORIGIN)) {
        for (const measured of [true, false]) {
          const prov = instrumentProvenance([{ key, label: key, measured }], ctx);
          const expected = provenanceOf(key, measured, contextFor(key, ctx.wearable_sources, fallback));
          expect(chipFor(prov[key]), `${key} measured=${measured}`).toBe(expected);
        }
      }
    }
  });
});
