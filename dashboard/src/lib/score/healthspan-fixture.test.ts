/**
 * The dashboard's numbers against a real backend response.
 *
 * `fixtures/healthspan-8019.json` is every route the loader reads, captured from
 * `pipeline.main --source sim --vlm fake --reasoner fake --port 8019 --fresh
 * --speed 0.01`: the seeded seven days, one tick, and no episode today. Only
 * `fetch` is stubbed; backend.ts, the loader and shape.ts all run for real.
 */
import { readFileSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { layerViewRows } from "@/components/brian/Layers";
import { instrumentSource, instrumentTiles } from "./instruments";
import { clearLoaderCache, loadDashboardData } from "./loader";
import { chipFor, factorProvenance, glassesGap, measuredOnPage } from "./provenance";
import type { DashboardData, HealthspanPayload, HealthspanWeek, PipelineEpisode } from "./types";

interface Fixture {
  day: string;
  routes: Record<string, unknown>;
}

const FIXTURE = JSON.parse(readFileSync(new URL("./fixtures/healthspan-8019.json", import.meta.url), "utf8")) as Fixture;
const BASE = "http://localhost:8019";
const DAY = FIXTURE.day;
const WEEK = FIXTURE.routes[`/api/healthspan?day=${DAY}&days=7&goal=average`] as HealthspanWeek;
/** `curl localhost:8019/api/healthspan` — the single-day route, the backend's headline. */
const SINGLE = FIXTURE.routes[`/api/healthspan?day=${DAY}`] as HealthspanPayload;
const episodesOn = (d: string): PipelineEpisode[] => FIXTURE.routes[`/api/episodes?day=${d}`] as PipelineEpisode[];

/** Glasses keys the engine sums from episodes, and so would read 0 on a day with none. */
const GLASSES_ZEROS = ["day_light_min", "alcohol_drinks"] as const;

const requested: string[] = [];

beforeAll(() => {
  // The capture's clock is read in the test runner's timezone: anchor it at local
  // noon of the captured day so every date the loader derives is that day.
  const [y, m, d] = DAY.split("-").map(Number);
  const status = { ...(FIXTURE.routes["/api/status"] as object), last_tick_t: new Date(y, m - 1, d, 12).getTime() / 1000 };
  vi.stubGlobal("fetch", (url: string) => {
    const path = url.slice(BASE.length);
    requested.push(path);
    const body = path === "/api/status" ? status : FIXTURE.routes[path];
    if (body === undefined) return Promise.resolve(new Response("not captured", { status: 404 }));
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
});

afterAll(() => {
  vi.unstubAllGlobals();
  clearLoaderCache();
});

async function load(): Promise<DashboardData> {
  clearLoaderCache();
  return loadDashboardData({ apiBase: BASE, goal: "average" });
}

describe("the captured no-episode day", () => {
  it("really is one: seeded rows, glasses episodes on earlier days, none today", () => {
    expect(episodesOn(DAY)).toEqual([]);
    expect(WEEK.days.map((d) => d.day).slice(0, -1).some((d) => episodesOn(d).length > 0)).toBe(true);
    expect(WEEK.today.window.uncovered_days).toContain(DAY);
  });
});

describe("dashboard numbers are the backend's", () => {
  it("shows the backend's hours_today as the headline, and its hours for every day of the week", async () => {
    const data = await load();
    expect(data.hours_today).toBe(SINGLE.hours_today);
    expect(data.hours_today).toBe(WEEK.today.hours_today);
    expect(data.hours_ci).toEqual(SINGLE.hours_ci);
    expect(data.overall).toBe(SINGLE.overall);
    expect(data.years_delta).toBe(SINGLE.years_delta);
    expect(data.years_ci).toEqual(SINGLE.years_ci);
    expect(data.factors).toEqual(SINGLE.factors);
    expect(data.week.map((d) => [d.date, d.hours])).toEqual(WEEK.days.map((d) => [d.day, d.hours_today]));
    // One score request, for the day the rows were read for, and nothing scored locally.
    expect(requested.filter((p) => p.startsWith("/api/healthspan"))).toContain(`/api/healthspan?day=${DAY}&days=7&goal=average`);
  });

  it("never counts a glasses zero on a day the glasses filed nothing", async () => {
    const data = await load();
    const obs = WEEK.today.observations;
    const seededToday = (FIXTURE.routes["/api/seeded?days=7"] as { rows: Array<{ day: string; metric: string; value: number }> }).rows.filter(
      (r) => r.day === DAY,
    );
    const row = (metric: string) => seededToday.find((r) => r.metric === metric)?.value;
    for (const key of GLASSES_ZEROS) {
      const p = WEEK.today.provenance[key];
      // Not a glasses measurement: the backend read it from another stream's row.
      expect(p.basis, key).not.toMatch(/^glasses/);
      expect(factorProvenance(key, true, data.source), key).not.toBe("glasses");
      expect(chipFor(p), key).toBe("seeded");
    }
    // Bright light is the phone's seeded row, not the engine's default 0 from no outdoor episode;
    // drinks are the WHOOP journal's answer.
    expect(obs.day_light_min).toBe(row("daytime_light_minutes"));
    expect(obs.day_light_min).not.toBe(0);
    expect(obs.alcohol_drinks).toBe(row("journal_alcohol"));
    // Everything only the glasses could have measured today is unmeasured, with the backend's reason.
    const today = Object.entries(WEEK.today.provenance).filter(([key, p]) => /^glasses/.test(p.basis) && !key.endsWith("_wk"));
    expect(today.map(([key]) => key).sort()).toEqual(["last_caffeine_hh", "med_adherence", "night_screen_min", "social_index"]);
    for (const [key, p] of today) {
      expect(p.source, key).toBe("missing");
      expect(obs[key], key).toBeUndefined();
      expect(glassesGap(key, data.source), key).toBe(p.detail);
    }
    const tiles = instrumentTiles(
      instrumentSource({ factors: data.factors, source: data.source, observations: data.observations, forecast: data.forecast, bedtime_hh: data.person.bedtime_hh }),
    );
    const byKey = Object.fromEntries(tiles.map((t) => [t.key, t]));
    expect(byKey.clock.measured).toBe(false);
    expect(byKey.people.measured).toBe(false);
    expect(byKey.light).toMatchObject({ measured: true, chip: "seeded" });
    expect(byKey.light.number).not.toMatch(/^0\b/);
  });
});

describe("the page's glasses gate agrees with the backend's provenance", () => {
  it("derives the same coverage from the backend's window as from the episodes the page read", async () => {
    const data = await load();
    const days = WEEK.today.window.factor_days;
    expect(data.source.glasses_coverage).toEqual({
      today: episodesOn(DAY).length > 0,
      week: days.some((d) => episodesOn(d).length > 0),
    });
    expect(data.source.glasses_coverage).toEqual({ today: false, week: true });
  });

  it("measures a factor on the page exactly when the backend measured it, and names the backend's stream", async () => {
    const data = await load();
    const provenance = data.source.provenance ?? {};
    for (const f of data.factors) {
      const p = provenance[f.key];
      expect(p, f.key).toBeDefined();
      expect(f.measured, f.key).toBe(p.source !== "missing");
      expect(measuredOnPage(f.key, f.measured, data.source), f.key).toBe(f.measured);
      expect(factorProvenance(f.key, f.measured, data.source), f.key).toBe(chipFor(p));
      // A glasses value is only ever measured inside the window the backend covered.
      if (/^glasses/.test(p.basis) && p.source !== "missing") {
        expect(f.key.endsWith("_wk") ? data.source.glasses_coverage.week : data.source.glasses_coverage.today, f.key).toBe(true);
      }
    }
    // The By-layer rows read the same chips.
    for (const row of layerViewRows(data)) {
      if (row.measured === 0) expect(row.provenance, row.name).toBe("imputed");
      else expect(row.provenance, row.name).not.toBe("imputed");
    }
  });
});
