/**
 * `POST /api/score` — the brief's contract: an `EngineRequest` body
 * `{profile, episodes, wearable_day, week_rows, history}` in, the engine's
 * payload out. The python spawn lives in `lib/score/engine.ts` so it works on
 * Windows too.
 *
 * `GET /api/score?goal=<Goal>` — the whole `DashboardData` the page renders, for
 * the client-side poll: the backend's `/api/healthspan` under that goal
 * (loader.ts). 502 when the backend is down; there is no offline stand-in.
 */
import { NextResponse } from "next/server";
import { EngineError, runEngine } from "@/lib/score/engine";
import { loadDashboardData, resolveGoal } from "@/lib/score/loader";
import type { EngineRequest } from "@/lib/score/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" } as const;

const json = (body: unknown, status = 200): NextResponse => NextResponse.json(body, { status, headers: NO_STORE });

const errorBody = (e: unknown): { error: string; stderr?: string } =>
  e instanceof EngineError ? { error: e.message, stderr: e.stderr } : { error: e instanceof Error ? e.message : String(e) };

export async function POST(req: Request): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "request body must be JSON" }, 400);
  }
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return json({ error: "request body must be an object {profile, episodes, wearable_day, week_rows, history}" }, 400);
  }
  try {
    return json(await runEngine(body as EngineRequest));
  } catch (e) {
    return json(errorBody(e), e instanceof EngineError ? 502 : 500);
  }
}

export async function GET(req: Request): Promise<NextResponse> {
  const params = new URL(req.url).searchParams;
  try {
    return json(await loadDashboardData({ goal: resolveGoal(params.get("goal")) }));
  } catch (e) {
    return json(errorBody(e), 502);
  }
}
