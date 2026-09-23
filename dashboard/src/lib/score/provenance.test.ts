import { describe, expect, it } from "vitest";
import { chipFor, contextFor, FACTOR_ORIGIN, factorProvenance, glassesGap, LIVE_SOURCES, measuredOnPage, PROVENANCE_LABEL, provenanceOf, type PageSource } from "./provenance";

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

describe("the backend's provenance, when the payload carries it", () => {
  // A day the glasses filed nothing: the coverage-only gate would blank every glasses key.
  const UNCOVERED: PageSource = { mode: "live", demo_mode: false, glasses_coverage: { today: false, week: true } };
  const WITH_ROWS: PageSource = {
    ...UNCOVERED,
    provenance: {
      day_light_min: { source: "seeded", basis: "phone", detail: "phone daytime_light_minutes, row 2026-09-22" },
      social_index: { source: "missing", basis: "glasses", detail: "no episodes on 2026-09-22 — glasses not worn yet" },
      alcohol_drinks: { source: "live", basis: "glasses + wearer report", detail: "1 sighting(s) in 1 occasion(s)" },
      nature_min_wk: { source: "live", basis: "glasses", detail: "84 min in park over 6 covered days" },
    },
  };

  it("decides the gate: a row from another stream is a measurement whatever the glasses did", () => {
    expect(glassesGap("day_light_min", UNCOVERED)).toBe("no glasses episodes today");
    expect(glassesGap("day_light_min", WITH_ROWS)).toBeNull();
    expect(measuredOnPage("day_light_min", true, WITH_ROWS)).toBe(true);
    expect(factorProvenance("day_light_min", true, WITH_ROWS)).toBe("seeded");
  });

  it("carries the backend's own reason for a glasses key it left missing", () => {
    expect(glassesGap("social_index", WITH_ROWS)).toBe("no episodes on 2026-09-22 — glasses not worn yet");
    expect(measuredOnPage("social_index", false, WITH_ROWS)).toBe(false);
    expect(factorProvenance("social_index", false, WITH_ROWS)).toBe("imputed");
  });

  it("names the glasses when the wearer's answer only corrected their count", () => {
    expect(chipFor(WITH_ROWS.provenance?.alcohol_drinks)).toBe("glasses");
    expect(factorProvenance("alcohol_drinks", true, WITH_ROWS)).toBe("glasses");
    expect(factorProvenance("nature_min_wk", true, WITH_ROWS)).toBe("glasses");
  });

  it("falls back to the re-derivation for a key the payload has no row for", () => {
    expect(factorProvenance("night_screen_min", true, WITH_ROWS)).toBe("imputed");
    expect(glassesGap("night_screen_min", WITH_ROWS)).toBe("no glasses episodes today");
    expect(factorProvenance("steps", true, WITH_ROWS)).toBe("whoop");
  });
});
