import { describe, expect, it } from "vitest";
import type { Day, EventDraft, MonthEvent, Sleep } from "./month/types";
import { operatingFor, monthFindings } from "./operating";

const at = (h: number, m = 0): number => h * 60 + m;

/**
 * A day that keeps every window and meets every target: both ceilings at 100.
 * The strength session is 40 min, under the 45 min that costs the next day's
 * body as recovery.
 */
function cleanDay(date: string, extra: EventDraft[] = [], sleep: Partial<Sleep> = {}): Day {
  const base: EventDraft[] = [
    { kind: "outdoor", start: at(7, 10), minutes: 15, label: "Porch", sunlight: true },
    { kind: "caffeine", start: at(7, 15), drink: "coffee" },
    { kind: "peptide", start: at(8, 30), dose: "AM", taken: true, thumb: "pen" },
    { kind: "meal", start: at(10, 10), label: "Eggs", food: "whole", thumb: "eggs" },
    { kind: "meal", start: at(12, 20), label: "Rice bowl", food: "whole", thumb: "rice_bowl" },
    { kind: "conversation", start: at(12, 20), minutes: 40, label: "Lunch" },
    { kind: "outdoor", start: at(12, 55), minutes: 20, label: "Walk", sunlight: true },
    { kind: "workout", start: at(15, 30), minutes: 40, label: "Strength", vigorous: true },
    { kind: "outdoor", start: at(16, 45), minutes: 40, label: "Evening walk", sunlight: true },
    { kind: "meal", start: at(17, 50), label: "Salmon", food: "whole", thumb: "fish" },
    { kind: "peptide", start: at(20, 0), dose: "PM", taken: true, thumb: "pen" },
    ...[7, 9, 10, 11, 12, 14, 15, 17].map((h) => ({ kind: "water" as const, start: at(h, 30), ml: 280 })),
  ];
  const events = [...base, ...extra]
    .sort((a, b) => a.start - b.start)
    .map((e, n) => ({ ...e, id: `${date}-${n}`, seeded: true }) as MonthEvent);
  return {
    date,
    type: "clean",
    sleep: { bed: -90, wake: 390, minutes: 480, deep: 100, rem: 110, fragmented: false, wakings: [], hrv_ms: 56, rhr_bpm: 58, seeded: true, ...sleep },
    events,
    aqi: 40,
    weather: { high_f: 90, summary: "Clear" },
    held_back: 0,
    until: 1440,
    seeded: true,
  };
}

const pvt = (ms: number): EventDraft => ({ kind: "mind_check", start: at(9), ms, lapses: 0, energy: null, mood: null, clarity: null });

const dates = (n: number) => Array.from({ length: n }, (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`);

describe("the two ceilings", () => {
  it("a clean day operates at 100", () => {
    const days = dates(3).map((d) => cleanDay(d));
    expect(operatingFor(days, 2)).toMatchObject({ cognition: 100, body: 100 });
  });

  it("late caffeine shows up as the lever, then as the short night it caused", () => {
    // The recorded night carries the coffee's cost: 45 min short, as Gardiner's pooled loss says.
    const clean = dates(4).map((d) => cleanDay(d));
    const late = dates(4).map((d, i) => cleanDay(d, i === 1 ? [{ kind: "caffeine", start: at(16, 10), drink: "coffee" }] : [], i === 2 ? { minutes: 435 } : {}));
    // The coffee's own day is untouched. Judged as today (no next night yet), its forecast is the lever;
    // judged later, with the next night recorded, there is no forecast (0, not 45: the model changed)
    // and the lever falls back to the week's costliest rule, still the coffee.
    expect(operatingFor(late, 1).cognition).toBe(operatingFor(clean, 1).cognition);
    const asToday = operatingFor(late.slice(0, 2), 1);
    expect(asToday.lever?.rule).toBe("caffeine");
    expect(asToday.lever?.text).toContain("Tonight about 45 min short, tomorrow's cognition about 0.4% lower, assumed.");
    expect(asToday.inputs.sleepLossForecastMin).toBe(45);
    expect(operatingFor(late, 1).inputs.sleepLossForecastMin).toBe(0);
    expect(operatingFor(late, 1).lever?.rule).toBe("caffeine");
    expect(operatingFor(late, 1).lever?.text).not.toContain("Tonight");
    // The next day pays through the debt, and the debt line names the coffee; no second charge for the coffee itself.
    const morning = operatingFor(late, 2);
    expect(morning.cognition).toBeLessThan(operatingFor(clean, 2).cognition);
    expect(morning.top[0]).toBe("Sleep debt last night");
    expect(morning.contributions.find((c) => c.finding.rule === "sleep_debt")?.finding.line).toContain("45 min short after yesterday's 16:10 coffee");
    expect(morning.contributions.some((c) => c.finding.rule === "caffeine")).toBe(false);
    // The day after, only the 14-night debt remains.
    expect(operatingFor(late, 3).contributions.some((c) => c.finding.rule === "caffeine")).toBe(false);
    expect(operatingFor(late, 3).cognition).toBeGreaterThanOrEqual(morning.cognition);
  });

  it("alcohol's effect is smaller on day 2 and gone on day 3", () => {
    const drink = (h: number) => ({ kind: "alcohol" as const, start: at(h), drinks: 1, label: "Wine" });
    const days = dates(6).map((d, i) => cleanDay(d, i === 1 ? [drink(20), drink(21)] : []));
    const findings = monthFindings(days);
    const alcoholLoss = (index: number) =>
      operatingFor(days, index, findings)
        .contributions.filter((c) => c.finding.rule === "alcohol")
        .reduce((sum, c) => sum + c.cognition, 0);
    const day1 = alcoholLoss(2); // the morning after
    const day2 = alcoholLoss(3);
    const day3 = alcoholLoss(4);
    expect(day1).toBeGreaterThan(0);
    expect(day2).toBeGreaterThan(0);
    expect(day2).toBeLessThan(day1);
    expect(day3).toBe(0);
  });

  it("a clean streak recovers toward 100", () => {
    const bad: EventDraft[] = [
      { kind: "caffeine", start: at(16, 30), drink: "coffee" },
      { kind: "meal", start: at(20, 45), label: "Burger", food: "fast_food", thumb: "burger" },
      { kind: "alcohol", start: at(21), drinks: 3, label: "Wine" },
    ];
    const days = dates(8).map((d, i) => cleanDay(d, i === 1 ? bad : []));
    const findings = monthFindings(days);
    const series = days.map((_, i) => operatingFor(days, i, findings).cognition);
    const worst = Math.min(...series);
    const worstAt = series.indexOf(worst);
    // Three drinks at 2% each land on the morning after (94). The coffee and the
    // dinner cost nothing directly: the recorded night after them was full.
    expect(worst).toBeLessThan(95);
    // From the worst day on, every day is at least as good as the one before, ending at 100.
    for (let i = worstAt + 1; i < series.length; i++) expect(series[i]).toBeGreaterThanOrEqual(series[i - 1]);
    expect(series.at(-1)).toBe(100);
  });

  it("long direct sun at a very high UV is a red; a short midday walk is not", () => {
    const market: EventDraft = { kind: "outdoor", start: at(14), minutes: 55, label: "Market", sunlight: true };
    const days = dates(2).map((d, i) => cleanDay(d, i === 1 ? [market] : []));
    const findings = monthFindings(days);
    expect(findings[0].some((f) => f.rule === "uv")).toBe(false);
    expect(findings[1].filter((f) => f.rule === "uv").map((f) => f.tone)).toEqual(["violation"]);
    expect(operatingFor(days, 1, findings).body).toBeLessThan(100);
  });

  it("14 nights at 6 h put cognition in the 80s with sleep debt on top", () => {
    const days = dates(15).map((d) => cleanDay(d, [], { minutes: 360 }));
    const op = operatingFor(days, 14);
    // 14 nights x 2 h = 28 h of debt at 0.5% an hour: 14% off.
    expect(op.cognition).toBeGreaterThan(80);
    expect(op.cognition).toBeLessThan(90);
    expect(op.top[0]).toBe("Sleep debt last night");
    expect(op.inputs.sleepDebtHours).toBe(28);
  });

  it("three drinks cost the next day 6 points of cognition and the body through HRV and resting HR", () => {
    const days = dates(6).map((d, i) =>
      cleanDay(d, i === 1 ? [{ kind: "alcohol", start: at(21), drinks: 3, label: "Wine" }] : [], i === 2 ? { hrv_ms: 45.5, rhr_bpm: 65.8 } : {}),
    );
    const findings = monthFindings(days);
    const clean = operatingFor(dates(6).map((d) => cleanDay(d)), 2);
    const morning = operatingFor(days, 2, findings);
    expect(clean.cognition - morning.cognition).toBeGreaterThanOrEqual(5);
    expect(clean.cognition - morning.cognition).toBeLessThanOrEqual(7);
    expect(morning.body).toBeLessThan(clean.body);
    const bodyLine = morning.contributions.find((c) => c.finding.rule === "alcohol" && c.body > 0)?.finding.line;
    expect(bodyLine).toContain("3 drinks: HRV -10.5 ms, resting HR +7.8 bpm against your baseline");
    expect(bodyLine).toContain("Grosicki 2026");
    // The day after carries half; the day after that is clean.
    const dayAfter = operatingFor(days, 3, findings);
    const residual = dayAfter.contributions.filter((c) => c.finding.rule === "alcohol").reduce((s, c) => s + c.cognition, 0);
    expect(residual).toBeGreaterThan(2.5);
    expect(residual).toBeLessThan(3.5);
    expect(operatingFor(days, 4, findings).contributions.some((c) => c.finding.rule === "alcohol")).toBe(false);
    expect(operatingFor(days, 4, findings).cognition).toBe(100);
  });

  it("calibration climbs to 7 of 7 days, then measures the morning against the best week", () => {
    const ms = [310, 300, 305, 298, 302, 296, 304, 300, 320, 290];
    const days = dates(10).map((d, i) => cleanDay(d, [pvt(ms[i])]));
    const findings = monthFindings(days);
    for (let i = 0; i < 6; i++) {
      const c = operatingFor(days, i, findings).calibration;
      expect(c).toMatchObject({ days: i + 1, ready: false, pvtBestMs: null, label: `calibrating, ${i + 1} of 7 days` });
      expect(operatingFor(days, i, findings).measured).toEqual({ cognition: null, body: null });
    }
    // Day 7: the first full week, median 302 ms; this morning's 304 reads 302 / 304.
    const ready = operatingFor(days, 6, findings);
    expect(ready.calibration).toMatchObject({ days: 7, ready: true, pvtBestMs: 302, hrvBestMs: 56, label: "your 100%: PVT 302 ms, HRV 56 ms" });
    expect(ready.measured.cognition).toBe(99.3);
    expect(ready.measured.body).toBe(100);
    // Day 8's week (300 to 304) has median 300, the best so far; day 9's slow 320 ms reads 300 / 320.
    expect(operatingFor(days, 7, findings).calibration.pvtBestMs).toBe(300);
    expect(operatingFor(days, 8, findings).measured.cognition).toBe(93.8);
    // A morning faster than the best week is capped at 100.
    const fast = operatingFor(days, 9, findings);
    expect(fast.calibration.pvtBestMs).toBe(300);
    expect(fast.measured.cognition).toBe(100);
  });

  it("a stale room costs about 7% that day only", () => {
    const room: EventDraft = { kind: "co2", start: at(13, 20), minutes: 220, ppm: 1280, label: "Office, windows shut" };
    const days = dates(3).map((d, i) => cleanDay(d, i === 1 ? [room] : []));
    const findings = monthFindings(days);
    // 30% x 220 / 960 waking minutes = 6.9%.
    const stale = operatingFor(days, 1, findings);
    expect(stale.cognition).toBeGreaterThan(92.5);
    expect(stale.cognition).toBeLessThan(93.5);
    expect(stale.inputs.co2Minutes).toEqual({ amber: 0, red: 220 });
    expect(stale.contributions.find((c) => c.finding.rule === "co2")?.finding.line).toContain("220 of 960 waking minutes");
    expect(operatingFor(days, 0, findings).cognition).toBe(100);
    expect(operatingFor(days, 2, findings).cognition).toBe(100);
  });

  it("an unrecorded night is not a night of debt; yesterday's coffee is charged once, on the debt curve", () => {
    // Night 1 is 7 h (1 h of debt, so the debt term has a line); night 3 was never recorded.
    const sleep = (i: number): Partial<Sleep> => (i === 0 ? { minutes: 420 } : i === 2 ? { minutes: 0, hrv_ms: null, rhr_bpm: null } : {});
    const late = dates(3).map((d, i) => cleanDay(d, i === 1 ? [{ kind: "caffeine", start: at(16, 10), drink: "coffee" }] : [], sleep(i)));
    const morning = operatingFor(late, 2);
    // No phantom 8 h of debt on top of the real hour, and the debt line says so.
    expect(morning.inputs.sleepDebtHours).toBe(1);
    expect(morning.contributions.find((c) => c.finding.rule === "sleep_debt")?.finding.line).toContain("last night not recorded");
    // The coffee's 45 min at 0.5% an hour: 0.375%, not its 3% rule effect.
    const coffee = morning.contributions.filter((c) => c.finding.rule === "caffeine");
    expect(coffee).toHaveLength(1);
    expect(coffee[0].cognition).toBeCloseTo(0.375, 3);
    expect(coffee[0].finding.line).toContain("45 min");
    expect(coffee[0].finding.line).toContain("assumed");
    // 1 h of debt at 0.5% and the coffee's 0.375%: 100 x 0.995 x 0.99625.
    expect(morning.cognition).toBe(99.1);
  });

  it("calibration still flips at 7 days when the PVT is taken only every third morning", () => {
    const ms = [310, 300, 305, 298, 302, 296, 304];
    const days = dates(20).map((d, i) => cleanDay(d, i % 3 === 0 ? [pvt(ms[i / 3])] : []));
    const findings = monthFindings(days);
    expect(operatingFor(days, 17, findings).calibration).toMatchObject({ days: 6, ready: false });
    const ready = operatingFor(days, 18, findings).calibration;
    // No 7-day window holds four PVTs, so the median of the seven readings (302 ms) stands in.
    expect(ready).toMatchObject({ days: 7, ready: true, pvtBestMs: 302, hrvBestMs: 56, label: "your 100%: PVT 302 ms, HRV 56 ms" });
  });

  it("a clean day with a full recorded night and baseline HRV reads 100 and 100", () => {
    const days = dates(8).map((d) => cleanDay(d));
    const op = operatingFor(days, 7);
    expect(op).toMatchObject({ cognition: 100, body: 100 });
    expect(op.inputs).toMatchObject({ sleepDebtHours: 0, drinksLastNight: 0, hrvMs: 56, rhrBpm: 58, hardExerciseYesterday: false });
    expect(op.contributions).toEqual([]);
  });
});
