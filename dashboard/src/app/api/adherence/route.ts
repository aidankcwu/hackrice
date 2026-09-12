/**
 * Adherence log API.
 *
 *   GET  /api/adherence                  → {state, logs}
 *   POST /api/adherence {lever_keys: []} → log the levers just shown (once per open 24 h window)
 *   POST /api/adherence {id, done}       → settle one log by hand
 *   POST /api/adherence {resolve: true}  → settle open logs from the last days' glasses/WHOOP data
 */
import { NextResponse } from "next/server";
import { dayEvidence } from "@/lib/adherence-evidence";
import { apiBase, loadDayInputs } from "@/lib/score/backend";
import { fixtureDays } from "@/lib/score/fixture";
import { localIsoDate } from "@/lib/score/backend";
import { adherenceFile, autoResolve, logSuggestions, markDone, stateOf } from "@/lib/store/adherence";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const json = (body: unknown, status = 200): NextResponse => NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });

export async function GET(): Promise<NextResponse> {
  const file = await adherenceFile();
  return json({ state: stateOf(file.logs), logs: file.logs });
}

export async function POST(req: Request): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return json({ error: "request body must be JSON" }, 400);
  }
  if (body === null || typeof body !== "object") return json({ error: "body must be an object" }, 400);
  const b = body as Record<string, unknown>;

  if (Array.isArray(b.lever_keys)) {
    const keys = b.lever_keys.filter((k): k is string => typeof k === "string" && k.length > 0);
    const { logged, logs } = await logSuggestions(keys);
    return json({ logged, state: stateOf(logs) });
  }
  if (typeof b.id === "string" && typeof b.done === "boolean") {
    const log = await markDone(b.id, b.done);
    if (!log) return json({ error: `no log ${b.id}` }, 404);
    return json({ log, state: stateOf((await adherenceFile()).logs) });
  }
  if (b.resolve === true) {
    const nowT = Date.now() / 1000;
    let days;
    try {
      days = (await loadDayInputs(apiBase())).days;
    } catch {
      days = fixtureDays(localIsoDate(nowT), nowT);
    }
    const { resolved, state } = await autoResolve(dayEvidence(days));
    return json({ resolved, state });
  }
  return json({ error: "expected {lever_keys: []}, {id, done} or {resolve: true}" }, 400);
}
