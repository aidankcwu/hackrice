import { afterEach, describe, expect, it, vi } from "vitest";
import { authInit, BackendOffline, daysEnding, fetchHealthspanWeek, fetchJson, loadDayInputs, pivotSeeded, WINDOW_DAYS, withToken } from "./backend";

const BASE = "http://localhost:8016";
const TICK_T = new Date(2026, 8, 12, 18, 0, 0).getTime() / 1000;
const TODAY = "2026-09-12";

/** Route table keyed by path prefix; a route set to `null` is a non-2xx. */
type Routes = Record<string, unknown>;

function stubFetch(routes: Routes): void {
  vi.stubGlobal("fetch", (url: string) => {
    const path = url.slice(BASE.length);
    const key = Object.keys(routes).find((k) => path.startsWith(k));
    if (key === undefined) return Promise.resolve(new Response("no route", { status: 404 }));
    const body = routes[key];
    if (body === null) return Promise.resolve(new Response("boom", { status: 500 }));
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
}

const OK: Routes = {
  "/api/status": { last_tick_t: TICK_T, tick_count: 1200, source: "glasses", demo_mode: false },
  "/api/seeded": [{ day: TODAY, metric: "steps", value: 5500 }],
  "/api/episodes": [],
  "/api/wearables/status": { metrics: [], live_connected: false, live_devices: [], catalogue: {} },
};

afterEach(() => vi.unstubAllGlobals());

describe("pivotSeeded", () => {
  it("pivots long rows and drops anything unusable", () => {
    const out = pivotSeeded([
      { day: TODAY, metric: "steps", value: 5500 },
      { day: TODAY, metric: "sleep_hours", value: 7.4 },
      { day: TODAY, metric: "broken", value: Number.NaN },
      { day: "2026-09-11", metric: "steps", value: 9100 },
    ]);
    expect(out[TODAY]).toEqual({ steps: 5500, sleep_hours: 7.4 });
    expect(out["2026-09-11"]).toEqual({ steps: 9100 });
  });
});

describe("daysEnding", () => {
  it("returns the window oldest first, inclusive of the end date", () => {
    expect(daysEnding(TODAY, 3)).toEqual(["2026-09-10", "2026-09-11", TODAY]);
    expect(daysEnding(TODAY)).toHaveLength(WINDOW_DAYS);
    // Month boundaries go through the Date constructor, not arithmetic on the day number.
    expect(daysEnding("2026-03-02", 3)).toEqual(["2026-02-28", "2026-03-01", "2026-03-02"]);
  });
});

describe("loadDayInputs", () => {
  it("carries the live device facts from /api/wearables/status onto the source", async () => {
    stubFetch({
      ...OK,
      "/api/wearables/status": {
        metrics: [{ metric: "hrv_rmssd", source: "whoop", origin: "live", count: 42, last_t: TICK_T }],
        live_connected: true,
        live_devices: ["whoop"],
        catalogue: {},
      },
    });
    const { days, source } = await loadDayInputs(BASE);
    expect(source).toMatchObject({
      mode: "live",
      api_base: BASE,
      day: TODAY,
      tick_count: 1200,
      capture_source: "glasses",
      live_connected: true,
      live_devices: ["whoop"],
    });
    expect(source.live_metrics).toEqual([{ metric: "hrv_rmssd", source: "whoop", origin: "live", count: 42, last_t: TICK_T }]);
    expect(days).toHaveLength(WINDOW_DAYS);
    expect(days[days.length - 1]).toMatchObject({ date: TODAY, isToday: true, seeded: { steps: 5500 } });
  });

  it("reports no device rather than guessing when the status route is unavailable", async () => {
    stubFetch({ ...OK, "/api/wearables/status": null });
    const { source } = await loadDayInputs(BASE);
    expect(source).toMatchObject({ live_connected: false, live_devices: [], live_metrics: [] });
  });

  it("throws BackendOffline when a required route fails, with no partial source", async () => {
    stubFetch({ ...OK, "/api/status": null });
    await expect(loadDayInputs(BASE)).rejects.toBeInstanceOf(BackendOffline);
    stubFetch({ ...OK, "/api/seeded": null });
    await expect(loadDayInputs(BASE)).rejects.toBeInstanceOf(BackendOffline);
  });
});

describe("fetchHealthspanWeek", () => {
  const PROFILE = { age: 20, sex: "M", goal: "athlete", bedtime_hh: 23, bedtime_source: "seeded" };
  const WEEK = { day: TODAY, days: [{ day: TODAY, hours_today: 0.95 }], today: { day: TODAY, hours_today: 0.95, profile: PROFILE } };

  it("pins the day, asks for the seven-day window and passes the goal", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      seen.push(url);
      return Promise.resolve(new Response(JSON.stringify(WEEK), { status: 200 }));
    });
    const week = await fetchHealthspanWeek(BASE, TODAY, "athlete");
    expect(seen).toEqual([`${BASE}/api/healthspan?day=${TODAY}&days=${WINDOW_DAYS}&goal=athlete`]);
    expect(week.today.hours_today).toBe(0.95);
  });

  it("is BackendOffline on a failed route or a body without today, never a partial score", async () => {
    stubFetch({ "/api/healthspan": null });
    await expect(fetchHealthspanWeek(BASE, TODAY, "average")).rejects.toBeInstanceOf(BackendOffline);
    stubFetch({ "/api/healthspan": { day: TODAY, days: [] } });
    await expect(fetchHealthspanWeek(BASE, TODAY, "average")).rejects.toBeInstanceOf(BackendOffline);
  });

  it("refuses a score that does not say whose it is, rather than guessing an age or sex for the header", async () => {
    for (const profile of [undefined, { ...PROFILE, age: undefined }, { ...PROFILE, age: "41" }, { ...PROFILE, age: null }, { ...PROFILE, sex: null }]) {
      stubFetch({ "/api/healthspan": { ...WEEK, today: { ...WEEK.today, profile } } });
      await expect(fetchHealthspanWeek(BASE, TODAY, "average"), JSON.stringify(profile ?? null)).rejects.toThrow(/no profile \{age, sex\}/);
    }
  });
});

describe("NEXT_PUBLIC_API_TOKEN (docs/DEPLOY.md)", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("sends nothing extra when unset", () => {
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "");
    expect(authInit()).toEqual({});
    expect(withToken(`${BASE}/api/evidence/d/f`)).toBe(`${BASE}/api/evidence/d/f`);
  });

  it("puts a Bearer header on fetches and ?token= on header-less URLs when set", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_TOKEN", "t k");
    const seen: Array<RequestInit | undefined> = [];
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
      seen.push(init);
      return Promise.resolve(new Response("{}", { status: 200 }));
    });
    await fetchJson(BASE, "/api/status");
    expect(seen[0]?.headers).toEqual({ authorization: "Bearer t k" });
    expect(withToken(`${BASE}/api/evidence/d/f`)).toBe(`${BASE}/api/evidence/d/f?token=t%20k`);
    expect(withToken(`${BASE}/api/protocol/export.csv?days=14`)).toBe(`${BASE}/api/protocol/export.csv?days=14&token=t%20k`);
  });
});
