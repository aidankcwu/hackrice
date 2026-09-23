import { describe, expect, it } from "vitest";
import {
  NBACK_COUNT,
  NBACK_N,
  clockLeft,
  dPrime,
  dsstKey,
  durationLabel,
  median,
  nbackResult,
  nbackSequence,
  pvtResult,
  stroopResult,
  zScore,
  type NbackAnswer,
} from "./engine";
import { dayStrip30, median7, restsOnSeeded, seededHistory, type TestStore } from "./store";

describe("engine", () => {
  it("labels durations the way the cards read", () => {
    expect(durationLabel(180)).toBe("3 min");
    expect(durationLabel(120)).toBe("2 min");
    expect(durationLabel(90)).toBe("90 s");
    expect(durationLabel(60)).toBe("60 s");
    expect(clockLeft(92_000)).toBe("1:32");
    expect(clockLeft(0)).toBe("0:00");
  });

  it("finds the median", () => {
    expect(median([])).toBeNull();
    expect(median([3])).toBe(3);
    expect(median([1, 5, 3])).toBe(3);
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });

  it("inverts the normal CDF", () => {
    expect(zScore(0.5)).toBeCloseTo(0, 8);
    expect(zScore(0.975)).toBeCloseTo(1.959964, 5);
    expect(zScore(0.025)).toBeCloseTo(-1.959964, 5);
  });

  it("scores d′ with the 0.5/N correction", () => {
    expect(dPrime(7, 7, 16, 16)).toBeCloseTo(0, 8);
    // Perfect performance stays finite.
    const perfect = dPrime(14, 0, 0, 32);
    expect(Number.isFinite(perfect)).toBe(true);
    expect(perfect).toBeGreaterThan(3);
    expect(dPrime(0, 14, 32, 0)).toBeCloseTo(-perfect, 8);
  });

  it("builds a 2-back sequence with 30% targets", () => {
    const { letters, targets } = nbackSequence();
    expect(letters).toHaveLength(NBACK_COUNT);
    expect(targets.slice(0, NBACK_N).every((t) => !t)).toBe(true);
    expect(targets.filter(Boolean)).toHaveLength(Math.round((NBACK_COUNT - NBACK_N) * 0.3));
    for (let i = NBACK_N; i < letters.length; i++) expect(letters[i] === letters[i - NBACK_N]).toBe(targets[i]);
  });

  it("scores a 2-back run from answers", () => {
    const { targets } = nbackSequence();
    const answers: NbackAnswer[] = targets.map((t) => (t ? "match" : "nomatch"));
    const result = nbackResult(targets, answers);
    expect(result.extra.misses).toBe(0);
    expect(result.extra.falseAlarms).toBe(0);
    expect(result.score).toBeGreaterThan(3);
  });

  it("shuffles a full DSST key", () => {
    expect([...dsstKey()].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
  });

  it("scores PVT-B and Stroop", () => {
    const pvt = pvtResult([300, 360, 280], 1, 1);
    expect(pvt.score).toBe(313);
    expect(pvt.extra).toEqual({ meanRt: 313, lapses: 2, falseStarts: 1, trials: 3 });
    const stroop = stroopResult([
      { congruent: true, correct: true, rt: 600 },
      { congruent: false, correct: true, rt: 700 },
      { congruent: false, correct: false, rt: 900 },
    ]);
    expect(stroop.score).toBe(100);
    expect(stroop.extra.errors).toBe(1);
  });
});

describe("store reads", () => {
  const now = new Date(2026, 8, 22, 15, 0, 0);

  it("seeds twelve PVT days inside 285–330 ms and nothing else", () => {
    const seeded = seededHistory("pvt", now);
    expect(seeded).toHaveLength(12);
    expect(seeded.every((e) => e.seeded && e.score >= 285 && e.score <= 330)).toBe(true);
    expect(seededHistory("nback", now)).toEqual([]);
  });

  it("gives PVT a seeded median from day one and marks it", () => {
    const empty: TestStore = {};
    expect(median7("pvt", empty, now)).not.toBeNull();
    expect(restsOnSeeded("pvt", empty, now)).toBe(true);
    expect(median7("dsst", empty, now)).toBeNull();
    expect(restsOnSeeded("dsst", empty, now)).toBe(false);
  });

  it("lets a real run replace the seeded day and fill today's cell", () => {
    const store: TestStore = { pvt: [{ t: now.toISOString(), score: 250, extra: {} }] };
    const strip = dayStrip30("pvt", store, now);
    expect(strip).toHaveLength(30);
    expect(strip[29]).toBe(true);
    expect(strip.filter(Boolean)).toHaveLength(13);
    expect(dayStrip30("stroop", {}, now).some(Boolean)).toBe(false);
  });
});
