import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { baselineOf, forScoring, parseInput, recordRun, rtZ, summarise, type PvtRun, type PvtTrial } from "./pvt";

let dir: string;
beforeAll(async () => {
  dir = await mkdtemp(path.join(tmpdir(), "brian-pvt-"));
  process.env.BRIAN_DATA_DIR = dir;
});
afterAll(async () => {
  delete process.env.BRIAN_DATA_DIR;
  await rm(dir, { recursive: true, force: true });
});

const rt = (ms: number): PvtTrial => ({ rt_ms: ms, false_start: false });
const trials = (ms: number[]): PvtTrial[] => ms.map(rt);

function run(id: string, meanRt: number, timestamp: string, completed = true): PvtRun {
  return {
    id,
    timestamp,
    duration_s: 180,
    completed,
    trials: [],
    check: null,
    mean_rt: meanRt,
    median_rt: meanRt,
    lapses: 0,
    false_starts: 0,
    rt_sd: 20,
    n_valid: 30,
    n_trials: 30,
  };
}

describe("summarise", () => {
  it("counts lapses over 500 ms, false starts and timeouts, and excludes them from the mean", () => {
    const s = summarise([rt(250), rt(300), rt(620), { rt_ms: null, false_start: true }, { rt_ms: null, false_start: false, timeout: true }]);
    expect(s).toEqual({ mean_rt: 390, median_rt: 300, lapses: 2, false_starts: 1, rt_sd: 200.7, n_valid: 3, n_trials: 5 });
  });

  it("returns nulls when nothing valid was recorded", () => {
    expect(summarise([{ rt_ms: null, false_start: true }])).toMatchObject({ mean_rt: null, median_rt: null, rt_sd: null, n_valid: 0 });
  });
});

describe("baseline and z", () => {
  it("needs three completed runs, takes the first three by time, and floors the SD at 25 ms", () => {
    expect(baselineOf([run("a", 300, "2026-09-10T09:00:00"), run("b", 310, "2026-09-11T09:00:00")])).toBeNull();
    const runs = [
      run("c", 320, "2026-09-12T09:00:00"),
      run("a", 300, "2026-09-10T09:00:00"),
      run("x", 999, "2026-09-10T12:00:00", false),
      run("b", 310, "2026-09-11T09:00:00"),
      run("d", 400, "2026-09-13T09:00:00"),
    ];
    const b = baselineOf(runs);
    expect(b).toEqual({ mu_rt: 310, sd_rt: 25, sd_raw: 10, run_ids: ["a", "b", "c"] });
    expect(rtZ(348, b!)).toBe(1.52);
  });

  it("uses the raw SD once it exceeds the floor", () => {
    const b = baselineOf([run("a", 250, "2026-09-10T09:00:00"), run("b", 310, "2026-09-11T09:00:00"), run("c", 370, "2026-09-12T09:00:00")]);
    expect(b).toMatchObject({ mu_rt: 310, sd_rt: 60, sd_raw: 60 });
  });
});

describe("parseInput", () => {
  it("accepts trials, a 1–5 check and a duration; rejects the rest", () => {
    const ok = parseInput({ trials: [{ rt_ms: 240.6 }, { false_start: true }, { timeout: true }], check: { energy: 3, mood: 4, clarity: 2 }, duration_s: 180 });
    expect(ok).toEqual({
      ok: true,
      input: {
        trials: [{ rt_ms: 241, false_start: false }, { rt_ms: null, false_start: true }, { rt_ms: null, false_start: false, timeout: true }],
        check: { energy: 3, mood: 4, clarity: 2 },
        timestamp: undefined,
        duration_s: 180,
      },
    });
    expect(parseInput({ trials: [{}], duration_s: 180 })).toMatchObject({ ok: false });
    expect(parseInput({ trials: [], duration_s: 0 })).toMatchObject({ ok: false });
    expect(parseInput({ trials: [], duration_s: 180, check: { energy: 6, mood: 1, clarity: 1 } })).toMatchObject({ ok: true, input: { check: null } });
  });
});

describe("recordRun and forScoring", () => {
  it("stores runs, builds the baseline on the third test and exposes pvt + check for the day", async () => {
    const day = "2026-09-12";
    const first = await recordRun({ trials: trials([300, 310, 290]), duration_s: 180, timestamp: `${day}T09:00:00` });
    expect(first.rt_z).toBeNull();
    expect(first.completed_runs).toBe(1);
    await recordRun({ trials: trials([320, 300, 310]), duration_s: 180, timestamp: `${day}T14:00:00` });
    const third = await recordRun({ trials: trials([360, 350, 370]), duration_s: 180, timestamp: `${day}T23:00:00`, check: { energy: 2, mood: 3, clarity: 2 } });
    expect(third.baseline).toMatchObject({ mu_rt: 330 });
    // means 300, 310, 360 → SD 32.1 → z = (360 − 330) / 32.1
    expect(third.rt_z).toBeCloseTo(0.93, 2);
    expect(third.vs_baseline_ms).toBe(30);

    const runs = (await import("./pvt")).allRuns;
    const all = await runs();
    expect(all).toHaveLength(3);
    const scoring = forScoring(all, day);
    expect(scoring.pvt).toEqual({ rt_z: third.rt_z, lapses: 0 });
    expect(scoring.check).toEqual({ energy: 2, mood: 3, clarity: 2 });
    expect(forScoring(all, "2026-09-13")).toEqual({});

    // A short test is stored but never completes, so it cannot move the baseline.
    const short = await recordRun({ trials: trials([200]), duration_s: 40, timestamp: `${day}T23:30:00` });
    expect(short.run.completed).toBe(false);
    expect(short.completed_runs).toBe(3);
  });
});
