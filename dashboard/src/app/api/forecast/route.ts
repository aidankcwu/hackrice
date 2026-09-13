/**
 * `POST /api/forecast` — the counterfactual behind the Tonight sliders.
 *
 * Body: `{profile, today_obs, baseline_sleep_h}` with only the engine's four
 * leading indicators in `today_obs` (`lib/score/forecast.ts` drops anything
 * else, so this route cannot be used to score a day). Response: the engine's
 * own `Forecast` — sleep hours, HRV %, SRI points, clock minutes, drivers —
 * already inside its clamps, because `forecast_tonight` clamps before it
 * returns and nothing here recomputes it.
 *
 * The python spawn is `lib/score/spawn.ts`, shared with `--registry`; there is
 * no second spawner.
 */
import { NextResponse } from "next/server";
import { EngineError } from "@/lib/score/engine";
import { parseForecastRequest, runForecast } from "@/lib/score/forecast";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (body: unknown, status = 200): NextResponse =>
  NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });

export async function POST(req: Request): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "request body must be JSON" }, 400);
  }
  const parsed = parseForecastRequest(body);
  if (!parsed.ok) return json({ error: parsed.error }, 400);
  try {
    return json(await runForecast(parsed.request));
  } catch (e) {
    if (e instanceof EngineError) return json({ error: e.message, stderr: e.stderr }, 502);
    return json({ error: e instanceof Error ? e.message : String(e) }, 500);
  }
}
