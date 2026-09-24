/**
 * `GET /healthz` — liveness for the container and the proxy. Answers without
 * the access token (src/proxy.ts lets it through) and without touching the
 * backend, so "app up, backend down" is distinguishable from "app down".
 */
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET(): NextResponse {
  return NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
}
