import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackendOffline } from "./backend";
import type { LiveDataSource } from "./backend";
import type { DayInputs, HealthspanPayload, HealthspanWeek } from "./types";

// The loader is server-only orchestration, so its I/O edges are stubbed: the
// day inputs and the backend's healthspan score. Everything in between is real.
// There is no engine edge any more — `./engine` is never imported by the loader,
// and a stub that throws proves no number can come from a local engine run.
const loadDayInputs = vi.hoisted(() => vi.fn());
const fetchHealthspanWeek = vi.hoisted(() => vi.fn());
const runEngine = vi.hoisted(() => vi.fn(() => Promise.reject(new Error("the dashboard must not score locally"))));

vi.mock("./backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./backend")>()),
  loadDayInputs,
  fetchHealthspanWeek,
}));
vi.mock("./engine", () => ({ runEngine }));

const { clearLoaderCache, loadDashboardData, resolveGoal } = await import("./loader");

const at = (h: number): number => new Date(2026, 8, 12, h, 0, 0).getTime() / 1000;

const payload = (): HealthspanPayload => ({
  day: "2026-09-12",
  profile: { goal: "average", bedtime_hh: 23, bedtime_source: "seeded" },
  provenance: {},
  window: { factor_days: ["2026-09-12"], uncovered_days: ["2026-09-12"] },
  overall: 60,
  layers: { Movement: 50, Sleep: 50, "Light & clock": 50, Social: 50, Environment: 50, "Diet & substances": 50, Recovery: 50, Cognition: 50 },
  years_delta: 0,
  years_ci: [0, 0],
  hours_today: 0.1,
  hours_ci: [0, 0.2],
  factors: [],
  ledger: [],
  forecast: { sleep_hours: 7, hrv_change_pct: 0, sri_change_pts: 0, melatonin_delay_min: 0, drivers: [] },
  levers: [],
  insights: [],
  pins: [],
  effects: [],
  observations: {},
});

const day = (): DayInputs => ({
  date: "2026-09-12",
  episodes: [],
  seeded: { bed_time: 23, sleep_hours: 7.4 },
  isToday: true,
  nowT: at(18),
});

const source = (over: Partial<LiveDataSource> = {}): LiveDataSource => ({
  mode: "live",
  api_base: "http://localhost:8010",
  day: "2026-09-12",
  live_connected: false,
  live_devices: [],
  live_metrics: [],
  ...over,
});

/** Env keys the loader reads; cleared per test so a developer's shell cannot colour the result. */
const ENV_KEYS = [
  "BRYAN_PERSON_NAME", "BRYAN_PERSON_AGE", "BRYAN_PERSON_SEX", "BRYAN_DEVICE",
  "BRIAN_PERSON_NAME", "BRIAN_PERSON_AGE", "BRIAN_PERSON_SEX", "BRIAN_DEVICE",
] as const;

const week = (): HealthspanWeek => ({ day: "2026-09-12", days: [{ day: "2026-09-12", hours_today: 0.1 }], today: payload() });

beforeEach(() => {
  clearLoaderCache();
  loadDayInputs.mockReset();
  fetchHealthspanWeek.mockReset();
  fetchHealthspanWeek.mockImplementation(() => Promise.resolve(week()));
  runEngine.mockClear();
  for (const key of ENV_KEYS) delete process.env[key];
});

afterEach(() => {
  for (const key of ENV_KEYS) delete process.env[key];
});

describe("resolveGoal", () => {
  it("accepts the known goals and falls back to average", () => {
    expect(resolveGoal("athlete")).toBe("athlete");
    expect(resolveGoal("shift")).toBe("shift");
    expect(resolveGoal("nonsense")).toBe("average");
    expect(resolveGoal(undefined)).toBe("average");
  });
});

describe("loadDashboardData when the backend is down", () => {
  it("rejects instead of substituting a fabricated day", async () => {
    loadDayInputs.mockRejectedValue(new BackendOffline("/api/status: fetch failed"));
    await expect(loadDashboardData()).rejects.toBeInstanceOf(BackendOffline);
    // No fixture path exists, so nothing is scored at all.
    expect(fetchHealthspanWeek).not.toHaveBeenCalled();
    expect(runEngine).not.toHaveBeenCalled();
  });

  it("rejects when the score route fails, rather than showing rows without a score", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    fetchHealthspanWeek.mockRejectedValue(new BackendOffline("/api/healthspan: HTTP 500"));
    await expect(loadDashboardData()).rejects.toBeInstanceOf(BackendOffline);
    expect(runEngine).not.toHaveBeenCalled();
  });

  it("does not cache the failure, so the next poll retries", async () => {
    loadDayInputs.mockRejectedValueOnce(new BackendOffline("down"));
    await expect(loadDashboardData()).rejects.toThrow("down");
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    await expect(loadDashboardData()).resolves.toMatchObject({ overall: 60 });
  });
});

describe("person", () => {
  it("is Bryan by default, with the profile label from the goal", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    const data = await loadDashboardData({ goal: "athlete" });
    expect(data.person).toMatchObject({ name: "Bryan", age: 20, sex: "M", goal: "athlete", profileLabel: "Athlete" });
  });

  it("takes BRYAN_* from the env and still honours the older BRIAN_* spelling", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    process.env.BRYAN_PERSON_NAME = "Ada";
    process.env.BRYAN_PERSON_AGE = "41";
    process.env.BRIAN_PERSON_SEX = "female";
    const data = await loadDashboardData();
    expect(data.person).toMatchObject({ name: "Ada", age: 41, sex: "F" });

    clearLoaderCache();
    delete process.env.BRYAN_PERSON_NAME;
    process.env.BRIAN_PERSON_NAME = "Grace";
    expect((await loadDashboardData()).person.name).toBe("Grace");
  });

  it("ignores an unusable age rather than scoring on it", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    process.env.BRYAN_PERSON_AGE = "not-a-number";
    expect((await loadDashboardData()).person.age).toBe(20);
    clearLoaderCache();
    process.env.BRYAN_PERSON_AGE = "-3";
    expect((await loadDashboardData()).person.age).toBe(20);
  });
});

describe("device string", () => {
  it("names only the wearables the backend reports as live", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source({ live_connected: true, live_devices: ["whoop"] }) });
    expect((await loadDashboardData()).person.device).toBe("Ray-Ban Meta + whoop");
  });

  it("lists several live devices and dedupes them", async () => {
    loadDayInputs.mockResolvedValue({
      days: [day()],
      source: source({ live_connected: true, live_devices: ["whoop", "fitbit", "whoop"] }),
    });
    expect((await loadDashboardData()).person.device).toBe("Ray-Ban Meta + fitbit + whoop");
  });

  it("says no wearable is connected instead of naming hardware that is not reporting", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    expect((await loadDashboardData()).person.device).toBe("Ray-Ban Meta · no wearable connected");
  });

  it("lets BRYAN_DEVICE override the derived string", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    process.env.BRYAN_DEVICE = "Ray-Ban Meta + Apple Watch";
    expect((await loadDashboardData()).person.device).toBe("Ray-Ban Meta + Apple Watch");
  });
});

describe("the score is the backend's", () => {
  it("asks /api/healthspan for the day the rows were read for, under the chosen goal", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    const data = await loadDashboardData({ goal: "shift", apiBase: "http://localhost:8016" });
    expect(fetchHealthspanWeek).toHaveBeenCalledWith("http://localhost:8016", "2026-09-12", "shift");
    expect(data.hours_today).toBe(0.1);
    expect(data.person.goal).toBe("shift");
    expect(runEngine).not.toHaveBeenCalled();
  });

  it("never sends an unknown goal: it scores as average", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    await loadDashboardData({ goal: "nonsense" as never });
    expect(fetchHealthspanWeek).toHaveBeenCalledWith("http://localhost:8010", "2026-09-12", "average");
  });
});

describe("cache", () => {
  it("shares one backend load between concurrent callers on the same key", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    const [a, b] = await Promise.all([loadDashboardData({ goal: "average" }), loadDashboardData({ goal: "average" })]);
    expect(a).toBe(b);
    expect(fetchHealthspanWeek).toHaveBeenCalledTimes(1);
  });

  it("keys on the goal and the api base", async () => {
    loadDayInputs.mockResolvedValue({ days: [day()], source: source() });
    await Promise.all([
      loadDashboardData({ goal: "average" }),
      loadDashboardData({ goal: "athlete" }),
      loadDashboardData({ goal: "average", apiBase: "http://localhost:8016" }),
    ]);
    expect(fetchHealthspanWeek).toHaveBeenCalledTimes(3);
    expect(loadDayInputs).toHaveBeenCalledWith("http://localhost:8016");
    expect(fetchHealthspanWeek).toHaveBeenCalledWith("http://localhost:8010", "2026-09-12", "athlete");
  });
});
