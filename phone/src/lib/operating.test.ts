import { describe, expect, it } from "vitest";
import type { Day, EventDraft, MonthEvent } from "./month/types";
import { operatingFor, monthFindings } from "./operating";

const at = (h: number, m = 0): number => h * 60 + m;

/** A day that keeps every window and meets every target: both ceilings at 100. */
function cleanDay(date: string, extra: EventDraft[] = []): Day {
  const base: EventDraft[] = [
    { kind: "outdoor", start: at(7, 10), minutes: 15, label: "Porch", sunlight: true },
    { kind: "caffeine", start: at(7, 15), drink: "coffee" },
    { kind: "peptide", start: at(8, 30), dose: "AM", taken: true, thumb: "pen" },
    { kind: "meal", start: at(10, 10), label: "Eggs", food: "whole", thumb: "eggs" },
    { kind: "meal", start: at(12, 20), label: "Rice bowl", food: "whole", thumb: "rice_bowl" },
    { kind: "conversation", start: at(12, 20), minutes: 40, label: "Lunch" },
    { kind: "outdoor", start: at(12, 55), minutes: 20, label: "Walk", sunlight: true },
    { kind: "workout", start: at(15, 30), minutes: 45, label: "Strength", vigorous: true },
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
    sleep: { bed: -90, wake: 390, minutes: 480, deep: 100, rem: 110, fragmented: false, wakings: [], hrv_ms: 56, rhr_bpm: 58, seeded: true },
    events,
    aqi: 40,
    weather: { high_f: 90, summary: "Clear" },
    held_back: 0,
    until: 1440,
    seeded: true,
  };
}

const dates = (n: number) => Array.from({ length: n }, (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`);

describe("the two ceilings", () => {
  it("a clean day operates at 100", () => {
    const days = dates(3).map((d) => cleanDay(d));
    expect(operatingFor(days, 2)).toMatchObject({ cognition: 100, body: 100 });
  });

  it("late caffeine lowers next-day cognition", () => {
    const clean = dates(4).map((d) => cleanDay(d));
    const late = dates(4).map((d, i) => cleanDay(d, i === 1 ? [{ kind: "caffeine", start: at(16, 10), drink: "coffee" }] : []));
    // The coffee's own day is untouched; the next day pays.
    expect(operatingFor(late, 1).cognition).toBe(operatingFor(clean, 1).cognition);
    expect(operatingFor(late, 2).cognition).toBeLessThan(operatingFor(clean, 2).cognition);
    expect(operatingFor(late, 2).top[0]).toBe("Coffee at 16:10 yesterday");
    // Gone the day after (decay 1 day).
    expect(operatingFor(late, 3).cognition).toBe(100);
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
    // Coffee 3%, dinner 1% and three drinks at 2% each land on the morning after.
    expect(worst).toBeLessThan(92);
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
});
