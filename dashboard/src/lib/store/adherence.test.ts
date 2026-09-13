import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { adherenceFile, autoResolve, logSuggestions, markDone, pAdherence, stateOf, type DayEvidence } from "./adherence";

let dir: string;
beforeAll(async () => {
  dir = await mkdtemp(path.join(tmpdir(), "brian-adh-"));
  process.env.BRIAN_DATA_DIR = dir;
});
afterAll(async () => {
  delete process.env.BRIAN_DATA_DIR;
  await rm(dir, { recursive: true, force: true });
});

const evidence = (date: string, over: Partial<DayEvidence> = {}): DayEvidence => ({ date, outdoor_min: 0, vigorous: false, strength_min: 0, bed_in_window: null, ...over });

describe("stateOf / pAdherence", () => {
  it("starts every lever at Beta(1,1) and counts only resolved logs", () => {
    const state = stateOf([
      { id: "1", lever_key: "vilpa_min", suggested_at: "", due_at: "", done: true },
      { id: "2", lever_key: "vilpa_min", suggested_at: "", due_at: "", done: true },
      { id: "3", lever_key: "vilpa_min", suggested_at: "", due_at: "", done: false },
      { id: "4", lever_key: "sri", suggested_at: "", due_at: "", done: null },
    ]);
    expect(state).toEqual({ vilpa_min: [3, 2] });
    expect(pAdherence(state, "vilpa_min")).toBeCloseTo(0.6);
    expect(pAdherence(state, "sri")).toBe(0.5);
  });
});

describe("logging and resolving", () => {
  it("logs each shown lever once per open window, settles from evidence, and times out the rest", async () => {
    const t0 = new Date("2026-09-12T09:00:00");
    const first = await logSuggestions(["bundle_walk", "vilpa_min", "alcohol_drinks"], t0);
    expect(first.logged.map((l) => l.lever_key)).toEqual(["bundle_walk", "vilpa_min", "alcohol_drinks"]);
    // A re-render five minutes later adds nothing.
    const again = await logSuggestions(["bundle_walk", "vilpa_min"], new Date("2026-09-12T09:05:00"));
    expect(again.logged).toHaveLength(0);

    // Same day, outdoors 25 min → the walk is done; nothing vigorous yet.
    const mid = await autoResolve([evidence("2026-09-12", { outdoor_min: 25 })], new Date("2026-09-12T18:00:00"));
    expect(mid.resolved.map((l) => [l.lever_key, l.done])).toEqual([["bundle_walk", true]]);
    expect(mid.state).toEqual({ bundle_walk: [2, 1] });

    // The next morning inside the window still counts for vilpa; alcohol has no evidence rule and stays open until the window closes.
    const next = await autoResolve([evidence("2026-09-13", { vigorous: true })], new Date("2026-09-13T08:00:00"));
    expect(next.resolved.map((l) => [l.lever_key, l.done])).toEqual([["vilpa_min", true]]);

    const late = await autoResolve([], new Date("2026-09-13T09:01:00"));
    expect(late.resolved.map((l) => [l.lever_key, l.done, l.evidence])).toEqual([["alcohol_drinks", false, "no sign of it within 24 h"]]);
    expect(late.state).toEqual({ bundle_walk: [2, 1], vilpa_min: [2, 1], alcohol_drinks: [1, 2] });

    // A manual tap settles a fresh log and is recorded as entered.
    const fresh = await logSuggestions(["sri"], new Date("2026-09-13T10:00:00"));
    const marked = await markDone(fresh.logged[0].id, true, new Date("2026-09-13T22:00:00"));
    expect(marked).toMatchObject({ lever_key: "sri", done: true, evidence: "entered" });
    expect((await adherenceFile()).logs).toHaveLength(4);
  });
});
