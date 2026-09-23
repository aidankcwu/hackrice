import { describe, expect, it } from "vitest";
import { episodeToEngine, habitualBedtime, mapEpisodes } from "./adapter";
import { localDecimalHour } from "./format";
import type { DayInputs, PipelineEpisode } from "./types";

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
  return { date: DAY, episodes: [], seeded: {}, isToday: false, nowT: NOW, ...overrides };
}

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

  it("tags meals from the pipeline's own healthy set and nothing wider", () => {
    const rice = episodeToEngine(ep({ kind: "meal", dominant: { scene: "cafe", food_type: "rice_bowl" } }), { nowT: NOW });
    expect(rice?.label).toBe("rice_bowl");
    expect(rice?.tags).toEqual(["rice_bowl", "mediterranean"]);
    // models.py HEALTHY_FOOD_TYPES verbatim — both of these are in it.
    const meal = (food: string) => episodeToEngine(ep({ kind: "meal", dominant: { food_type: food } }), { nowT: NOW });
    expect(meal("beans_legumes")?.tags).toEqual(["beans_legumes", "mediterranean"]);
    expect(meal("seafood")?.tags).toEqual(["seafood", "mediterranean"]);
    // Off-pattern and neutral types are never tagged mediterranean.
    for (const food of ["processed", "burger", "sandwich", "pasta", "red_meat", "none"]) {
      expect(meal(food)?.tags).toEqual([food]);
    }
  });

  it("leaves an untyped meal without a food tag rather than inventing one", () => {
    const unknown = episodeToEngine(ep({ kind: "meal", dominant: {} }), { nowT: NOW });
    expect(unknown?.label).toBe("meal");
    expect(unknown).not.toHaveProperty("tags");
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
describe("mapEpisodes", () => {
  it("keeps only engine kinds, in order, closing open ones at the day's nowT", () => {
    const d = day({
      episodes: [
        ep({ id: "b", kind: "conversation", duration_s: 1200 }),
        ep({ id: "x", kind: "cold_plunge" }),
        ep({ id: "c", kind: "gym_session", start_t: NOW - 40 * 60, end_t: null, duration_s: 0, open: true }),
      ],
    });
    const mapped = mapEpisodes(d);
    expect(mapped.map((e) => e.id)).toEqual(["b", "c"]);
    expect(mapped.map((e) => e.type)).toEqual(["conversation", "gym_session"]);
    expect(mapped[1].minutes).toBe(40);
  });
});
