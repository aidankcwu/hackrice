import { describe, expect, it } from "vitest";
import { contextFor, FACTOR_ORIGIN, LIVE_SOURCES, PROVENANCE_LABEL, provenanceOf } from "./provenance";

describe("healthkit rows", () => {
  it("resolves a row the phone's HealthKit sync wrote to live, labelled Apple Health", () => {
    expect(LIVE_SOURCES.has("healthkit")).toBe(true);
    const sources = { sleep_hours: "healthkit", steps: "healthkit" };
    for (const key of ["sleep_hours", "steps"]) {
      const chip = provenanceOf(key, true, contextFor(key, sources, "seeded"));
      expect(chip, key).toBe("healthkit");
      expect(PROVENANCE_LABEL[chip]).toBe("Apple Health");
    }
  });

  it("keeps a demo-seed row beside it labelled Seeded", () => {
    const chip = provenanceOf("sri", true, contextFor("sri", { sleep_hours: "healthkit", sleep_regularity_sri: "whoop" }, "seeded"));
    expect(PROVENANCE_LABEL[chip]).toBe("Seeded");
  });
});

describe("provenanceOf", () => {
  it("labels every unmeasured factor as imputed regardless of origin", () => {
    expect(provenanceOf("steps", false, { wearable: "whoop" })).toBe("imputed");
    expect(provenanceOf("rt_z", false, { wearable: "seeded" })).toBe("imputed");
  });

  it("keeps seeded wearable data labelled seeded and never relabels it live", () => {
    expect(provenanceOf("steps", true, { wearable: "seeded" })).toBe("seeded");
    expect(provenanceOf("sleep_hours", true, { wearable: "whoop" })).toBe("whoop");
  });

  it("maps glasses and entered keys", () => {
    expect(provenanceOf("day_light_min", true, { wearable: "seeded" })).toBe("glasses");
    expect(provenanceOf("rt_z", true, { wearable: "seeded" })).toBe("entered");
    // Unknown keys default to the glasses stream, which is where new episode-derived factors come from.
    expect(provenanceOf("new_factor", true, { wearable: "seeded" })).toBe("glasses");
  });

  it("covers every engine factor key", () => {
    const engineKeys = [
      "steps", "vilpa_min", "resistance_min_wk", "fitness_pct", "gait_speed", "sleep_hours", "sri", "day_light_min",
      "night_light_lux", "social_index", "purpose", "nature_min_wk", "noise_night_db", "med_adherence", "alcohol_drinks",
      "smoker", "sauna_wk", "rt_z", "recovery_ratio",
    ];
    for (const key of engineKeys) expect(FACTOR_ORIGIN[key], key).toBeDefined();
  });
});
