/**
 * `python brian_score.py --forecast` — the engine's `forecast_tonight` on a
 * set of leading indicators, for the counterfactual sliders on the Tonight
 * panel. The request is the same `profile` the score request carries plus
 * `today_obs` with only leading indicators; the response is the engine's
 * `Forecast` dataclass.
 */
import "server-only";
import { parseEngineJson } from "./engine";
import { runPythonScript, SCORE_SCRIPT } from "./spawn";
import type { EngineForecast, EngineProfile } from "./types";

/** Keys `forecast_tonight` reads from `today_obs`. Anything else is dropped so the route cannot be used to score. */
export const LEADING_KEYS = ["last_caffeine_hh", "night_screen_min", "alcohol_drinks", "planned_bed_shift_min"] as const;
export type LeadingKey = (typeof LEADING_KEYS)[number];

export interface ForecastRequest {
  profile?: EngineProfile;
  today_obs: Partial<Record<LeadingKey, number>>;
  /** Habitual sleep the forecast starts from (engine default 7.5). */
  baseline_sleep_h?: number;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Body of `POST /api/forecast` → a request with only the fields the engine reads, or a reason it was rejected. */
export function parseForecastRequest(body: unknown): { ok: true; request: ForecastRequest } | { ok: false; error: string } {
  if (body === null || typeof body !== "object") return { ok: false, error: "body must be an object {profile, today_obs, baseline_sleep_h}" };
  const b = body as Record<string, unknown>;
  const rawObs = b.today_obs;
  if (rawObs === null || typeof rawObs !== "object") return { ok: false, error: "today_obs must be an object" };
  const today_obs: ForecastRequest["today_obs"] = {};
  for (const key of LEADING_KEYS) {
    const v = (rawObs as Record<string, unknown>)[key];
    if (v === undefined || v === null) continue;
    if (!finite(v)) return { ok: false, error: `today_obs.${key} must be a number` };
    today_obs[key] = v;
  }
  const request: ForecastRequest = { today_obs };
  if (b.profile !== undefined) {
    if (b.profile === null || typeof b.profile !== "object") return { ok: false, error: "profile must be an object" };
    request.profile = b.profile as EngineProfile;
  }
  if (b.baseline_sleep_h !== undefined) {
    if (!finite(b.baseline_sleep_h)) return { ok: false, error: "baseline_sleep_h must be a number" };
    request.baseline_sleep_h = b.baseline_sleep_h;
  }
  return { ok: true, request };
}

export async function runForecast(request: ForecastRequest, timeoutMs = 8_000): Promise<EngineForecast> {
  const out = await runPythonScript(SCORE_SCRIPT, ["--forecast"], JSON.stringify(request), timeoutMs);
  return parseEngineJson<EngineForecast>(out);
}
