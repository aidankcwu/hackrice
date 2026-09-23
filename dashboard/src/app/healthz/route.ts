/**
 * `GET /healthz` — liveness for the container and the proxy. Answers without
 * the access token (the middleware lets it through) and without touching the
 * backend, so "dashboard up, backend down" is distinguishable from "dashboard down".
 */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET(): NextResponse {
  return NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
}
