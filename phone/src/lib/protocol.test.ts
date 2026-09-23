import { describe, expect, it } from "vitest";
import type { Day, MonthEvent } from "./month/types";
import { daysText, localWindowText, statusFor, statusText, weekdayOf, windowText } from "./protocol";

const sleep: Day["sleep"] = { bed: -60, wake: 390, minutes: 430, deep: 90, rem: 100, fragmented: false, wakings: [], hrv_ms: 60, rhr_bpm: 52, seeded: true };
const day = (events: MonthEvent[]): Pick<Day, "sleep" | "events"> => ({ sleep, events });
const seeded = { seeded: true } as const;

describe("statusFor", () => {
  it("sees a taken peptide inside the dose window", () => {
    const d = day([{ id: "a", kind: "peptide", dose: "AM", taken: true, thumb: "pen", start: 511, ...seeded }]);
    expect(statusFor({ kind: "dose", window: { start: "07:00", end: "10:00" } }, d, 700)).toEqual({ status: "seen", time: 511 });
  });

  it("waits while the window is ahead, misses once it has closed", () => {
    const item = { kind: "meal" as const, window: { start: "11:30", end: "14:00" } };
    expect(statusFor(item, day([]), 600).status).toBe("waiting");
    expect(statusFor(item, day([]), 900).status).toBe("missed");
  });

  it("resolves a wake anchor from the sleep record", () => {
    const d = day([{ id: "w", kind: "outdoor", label: "Walk", sunlight: true, start: 520, minutes: 20, ...seeded }]);
    expect(statusFor({ kind: "light", window: { anchor: "wake", offsetMin: 120, lengthMin: 30 } }, d, 600)).toEqual({ status: "seen", time: 520 });
  });

  it("reads last night's bed for a sleep window that crosses midnight", () => {
    expect(statusFor({ kind: "sleep", window: { start: "22:30", end: "06:30" } }, day([]), 600)).toEqual({ status: "seen", time: 1380 });
  });

  it("meets a bed window from an early bed, misses it from a late one", () => {
    const item = { kind: "sleep" as const, window: { start: "22:30", end: "06:30" } };
    const early = { ...day([]), sleep: { ...sleep, bed: -92 } };
    expect(statusFor(item, early, 600)).toEqual({ status: "seen", time: 1348 });
    const late = { ...day([]), sleep: { ...sleep, bed: 39 } };
    expect(statusFor(item, late, 600)).toEqual({ status: "seen", time: 39 });
    const veryLate = { ...day([]), sleep: { ...sleep, bed: 420 } };
    expect(statusFor(item, veryLate, 600).status).toBe("missed");
  });

  it("treats a bed anchor as met by the record's own bed, past midnight included", () => {
    const item = { kind: "sleep" as const, window: { anchor: "bed" as const, offsetMin: 0, lengthMin: 480 } };
    const afterMidnight = { ...day([]), sleep: { ...sleep, bed: 39 } };
    expect(statusFor(item, afterMidnight, 600)).toEqual({ status: "seen", time: 39 });
  });

  it("lets only a supplements event satisfy a supplement item", () => {
    const d = day([
      { id: "a", kind: "peptide", dose: "AM", taken: true, thumb: "pen", start: 527, ...seeded },
      { id: "b", kind: "supplements", label: "Omega-3 and vitamin D", start: 625, ...seeded },
    ]);
    expect(statusFor({ kind: "supplement", window: { start: "08:00", end: "12:00" } }, d, 700)).toEqual({ status: "seen", time: 625 });
    expect(statusFor({ kind: "supplement", window: { start: "08:00", end: "12:00" } }, day([d.events[0]]), 800).status).toBe("missed");
  });

  it("misses screens when a screen block runs past the window start", () => {
    const d = day([{ id: "s", kind: "screen", device: "phone", start: 1320, minutes: 30, ...seeded }]);
    expect(statusFor({ kind: "screens", window: { start: "21:30", end: "23:59" } }, d, 1440).status).toBe("missed");
    const overlapping = day([{ id: "s", kind: "screen", device: "phone", start: 1280, minutes: 30, ...seeded }]);
    expect(statusFor({ kind: "screens", window: { start: "21:30", end: "23:59" } }, overlapping, 1440).status).toBe("missed");
    const before = day([{ id: "s", kind: "screen", device: "phone", start: 1200, minutes: 30, ...seeded }]);
    expect(statusFor({ kind: "screens", window: { start: "21:30", end: "23:59" } }, before, 1440).status).toBe("seen");
    expect(statusFor({ kind: "screens", window: { start: "21:30", end: "23:59" } }, day([]), 1440).status).toBe("seen");
  });
});

describe("words", () => {
  it("writes windows and days", () => {
    expect(windowText({ window_start: "07:00", window_end: "10:00" })).toBe("07:00 to 10:00");
    expect(localWindowText({ anchor: "wake", offsetMin: 120, lengthMin: 30 })).toBe("2 h after waking, 30 min");
    expect(daysText([0, 1, 2, 3, 4, 5, 6])).toBe("Every day");
    expect(daysText([0, 1, 2, 3, 4])).toBe("Weekdays");
    expect(daysText([0, 2, 4])).toBe("Mon, Wed, Fri");
  });

  it("names a backend status", () => {
    expect(statusText({ status: "done", seen_t: null })).toBe("Done");
    expect(statusText({ status: "undone", seen_t: null })).toBe("Waiting");
  });

  it("counts Monday as 0", () => {
    expect(weekdayOf("2026-09-22")).toBe(1);
  });
});
