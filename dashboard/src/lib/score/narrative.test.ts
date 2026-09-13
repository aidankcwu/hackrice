import { describe, expect, it } from "vitest";
import type { Decision } from "@/lib/types";
import type { PinRow } from "./types";
import {
  actionTypes,
  annotateLine,
  decisionState,
  effectHours,
  feedLine,
  insightLines,
  linkedPin,
  minutesOfHhmm,
  newestFirst,
  spokenText,
} from "./narrative";

/** Epoch seconds for `HH:MM` local time on a fixed day, so the tests read the same clock the UI does. */
const at = (hh: number, mm: number): number => new Date(2026, 8, 12, hh, mm, 0).getTime() / 1000;

const decision = (over: Partial<Decision> = {}): Decision => ({
  id: "d_1",
  t: at(9, 12),
  trigger: "caffeine_seen",
  trigger_tick_id: "t_1",
  episode_id: "e_1",
  interpretation: "caffeine in frame",
  confidence: 0.7,
  actions: [{ type: "annotate", line: "caffeine in frame" }],
  spoke: false,
  dropped: false,
  drop_reason: null,
  latency_ms: 0,
  model: "fake",
  ...over,
});

const pin = (over: Partial<PinRow> = {}): PinRow => ({
  id: "p_1",
  time: "09:15",
  seen: "Caffeine at 09:15",
  kind: "earn",
  effect: "outside your cutoff — fine",
  grade: "B",
  img: null,
  icon: "coffee",
  ...over,
});

describe("minutesOfHhmm", () => {
  it("parses a pin stamp and rejects nonsense rather than guessing", () => {
    expect(minutesOfHhmm("09:15")).toBe(555);
    expect(minutesOfHhmm("0:05")).toBe(5);
    expect(minutesOfHhmm("24:00")).toBeNull();
    expect(minutesOfHhmm("09:60")).toBeNull();
    expect(minutesOfHhmm("—")).toBeNull();
  });
});

describe("linkedPin", () => {
  it("links the nearest pin inside the window", () => {
    const near = pin({ id: "near", time: "09:13" });
    const far = pin({ id: "far", time: "09:20" });
    expect(linkedPin(decision(), [far, near])?.id).toBe("near");
  });

  it("links nothing outside the window, and nothing at all without an episode", () => {
    expect(linkedPin(decision(), [pin({ time: "11:00" })])).toBeNull();
    expect(linkedPin(decision({ episode_id: null }), [pin({ time: "09:15" })])).toBeNull();
    expect(linkedPin(decision(), [])).toBeNull();
  });

  it("skips pins whose stamp cannot be read", () => {
    expect(linkedPin(decision(), [pin({ time: "n/a" })])).toBeNull();
  });
});

describe("action readers", () => {
  it("reads the annotate line, insights and speech, or null when absent", () => {
    const d = decision({
      actions: [
        { type: "annotate", line: "caffeine in frame" },
        { type: "log_insight", category: "caffeine", text: "second cup before noon" },
        { type: "speak", text: "Coffee this late may cost you sleep.", urgency: "low" },
      ],
    });
    expect(annotateLine(d.actions)).toBe("caffeine in frame");
    expect(insightLines(d.actions)).toEqual([{ category: "caffeine", text: "second cup before noon" }]);
    expect(spokenText(d.actions)).toBe("Coffee this late may cost you sleep.");
    expect(annotateLine([{ type: "nothing" }])).toBeNull();
    expect(annotateLine([{ type: "annotate", line: "   " }])).toBeNull();
    expect(spokenText([{ type: "nothing" }])).toBeNull();
  });

  it("deduplicates action types in first-seen order", () => {
    expect(
      actionTypes([
        { type: "watch", after_s: 60, reason: "check again" },
        { type: "annotate", line: "x" },
        { type: "watch", after_s: 90, reason: "again" },
      ]),
    ).toEqual(["watch", "annotate"]);
  });
});

describe("decisionState", () => {
  it("puts dropped ahead of spoke, and silent last", () => {
    expect(decisionState(decision({ dropped: true, spoke: true }))).toBe("dropped");
    expect(decisionState(decision({ spoke: true }))).toBe("spoke");
    expect(decisionState(decision())).toBe("silent");
  });
});

describe("effectHours", () => {
  it("reads the signed hours the engine wrote, and nothing where there are none", () => {
    expect(effectHours("bright light before 10:00 — advances your clock · +0.4 h today")).toBe(0.4);
    expect(effectHours("inside your cutoff — ~−1 h of sleep tonight")).toBe(-1);
    expect(effectHours("counts toward social integration")).toBeNull();
    expect(effectHours("HRV -12% tonight (forecast)")).toBeNull();
  });
});

describe("newestFirst", () => {
  it("sorts by time descending without mutating the input", () => {
    const older = decision({ id: "old", t: at(8, 0) });
    const newer = decision({ id: "new", t: at(10, 0) });
    const input = [older, newer];
    expect(newestFirst(input).map((d) => d.id)).toEqual(["new", "old"]);
    expect(input.map((d) => d.id)).toEqual(["old", "new"]);
  });
});

describe("feedLine", () => {
  it("writes the docs/API.md feed line, including the silent case", () => {
    expect(feedLine(decision({ t: at(12, 31), trigger: "food_in_frame", interpretation: "mixed lunch w/ people" }))).toBe(
      "12:31 · food_in_frame · mixed lunch w/ people · annotate · silent",
    );
    expect(feedLine(decision({ t: at(12, 31), interpretation: "", actions: [] }))).toBe(
      "12:31 · caffeine_seen · no interpretation · no action · silent",
    );
  });
});
