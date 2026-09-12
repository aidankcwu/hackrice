import { describe, expect, it } from "vitest";
import {
  allowedNumbers,
  clean,
  narrate,
  numbersIn,
  runLength,
  sentenceIsSafe,
  templateSentence,
  type ContrastAnnotation,
  type ExtremeAnnotation,
  type RunAnnotation,
} from "./narrator";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const run: RunAnnotation = {
  kind: "run",
  from: "Thu",
  to: "Sat",
  direction: -1,
  delta_hours: -2.6,
  sleep_from: 7.8,
  sleep_to: 7.5,
  rec_from: 79,
  rec_to: 84,
  drivers: ["alcohol", "night_screen"],
  evidence: ["Zhao 2023 JAMA Netw Open: no protective dose; same-night HRV suppression", "Brown 2022 PLOS Biology: evening light ≥10 lx melanopic delays melatonin"],
};

const contrast: ContrastAnnotation = {
  kind: "contrast",
  driver: "caffeine_late",
  n_with: 3,
  n_without: 4,
  hours_with: -0.4,
  hours_without: 0.6,
  sleep_with: 6.0,
  sleep_without: 7.6,
  rec_with: 38.7,
  rec_without: 82.8,
  evidence: ["Drake 2013 J Clin Sleep Med: caffeine 6 h before bed cut sleep by >1 h"],
};

const extreme: ExtremeAnnotation = { kind: "extreme", worst_day: "Sat", worst_hours: -1.2, worst_drivers: ["alcohol", "night_screen"], best_day: "Thu", best_hours: 1.4 };

describe("numbersIn", () => {
  it("reads decimals, thousands separators, minus signs and ranges as magnitudes", () => {
    expect(numbersIn("−2.6 h over 3 days; 1,300 people; 20–48% higher")).toEqual([2.6, 3, 1300, 20, 48]);
    expect(numbersIn("Thu→Sat: -0.4 h")).toEqual([0.4]);
    expect(numbersIn("no numbers here")).toEqual([]);
  });
});

describe("allowedNumbers / sentenceIsSafe", () => {
  it("accepts numbers from the JSON, including those inside evidence strings and integer forms of floats", () => {
    const allowed = allowedNumbers(contrast);
    expect(allowed.has(38.7)).toBe(true);
    expect(allowed.has(6)).toBe(true); // sleep_with 6.0
    expect(allowed.has(2013)).toBe(true); // Drake 2013
    expect(allowed.has(1)).toBe(true); // ">1 h" in the evidence
    expect(sentenceIsSafe("Three late-caffeine days averaged 6 h of sleep and recovery 38.7; the other 4 averaged 7.6 h and 82.8.", contrast)).toBe(true);
  });

  it("rejects a sentence with a number the annotation does not contain", () => {
    expect(sentenceIsSafe("Late caffeine cost you about 1.5 h of sleep on 3 days.", contrast)).toBe(false);
    expect(sentenceIsSafe("Thu→Sat: −2.6 h over 3 days.", run)).toBe(false);
    expect(sentenceIsSafe("Thu→Sat: −2.6 h over 3 days.", run, [3])).toBe(true);
  });
});

describe("templateSentence", () => {
  it("writes the run, contrast and extreme templates from the JSON only", () => {
    expect(templateSentence(run, { days: DAYS })).toBe("Thu→Sat: −2.6 h over 3 days. Common thread: alcohol, night screens.");
    expect(templateSentence(run)).toBe("Thu→Sat: −2.6 h. Common thread: alcohol, night screens.");
    expect(templateSentence({ ...run, drivers: [] })).toBe("Thu→Sat: −2.6 h. Common thread: nothing the glasses saw on every day.");
    expect(templateSentence(contrast)).toBe("3 late caffeine days averaged 6 h of sleep and recovery 38.7; the other 4 averaged 7.6 h and 82.8.");
    expect(templateSentence(extreme)).toBe("Sat was the worst day at −1.2 h (alcohol, night screens); Thu the best at +1.4 h.");
  });

  it("every template passes its own number check", () => {
    expect(sentenceIsSafe(templateSentence(run, { days: DAYS }), run, [runLength(run, DAYS)!])).toBe(true);
    expect(sentenceIsSafe(templateSentence(contrast), contrast)).toBe(true);
    expect(sentenceIsSafe(templateSentence(extreme), extreme)).toBe(true);
  });
});

describe("narrate", () => {
  it("keeps a safe LLM sentence and falls back to the template on a leaked number or a failure", async () => {
    const llm = async (prompt: string) => {
      if (prompt.includes("run")) return '"Thursday to Saturday you dropped 2.6 hours, with alcohol and night screens on every day."';
      if (prompt.includes("contrast")) return "Late caffeine nights averaged 6 h of sleep and about 1.5 h less than usual.";
      throw new Error("boom");
    };
    const out = await narrate([run, contrast, extreme], ["run prompt", "contrast prompt", "extreme prompt"], { days: DAYS }, llm);
    expect(out[0]).toMatchObject({ origin: "llm", text: "Thursday to Saturday you dropped 2.6 hours, with alcohol and night screens on every day." });
    expect(out[1]).toMatchObject({ origin: "template", text: templateSentence(contrast) });
    expect(out[2]).toMatchObject({ origin: "template", text: templateSentence(extreme), evidence: [] });
    expect(out[0].evidence).toHaveLength(2);
  });

  it("uses templates when no LLM is configured", async () => {
    const out = await narrate([extreme], [undefined]);
    expect(out[0]).toMatchObject({ origin: "template" });
  });
});

describe("clean", () => {
  it("takes the first non-empty line without quotes and rejects overlong output", () => {
    expect(clean('\n  "Sat was the worst day."\nExtra line')).toBe("Sat was the worst day.");
    expect(clean("")).toBeNull();
    expect(clean("x".repeat(300))).toBeNull();
  });
});
