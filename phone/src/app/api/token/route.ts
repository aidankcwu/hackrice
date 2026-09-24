/**
 * `GET /api/token` — hands the tester's token back to a page that lost it:
 * an iOS Home Screen launch (its localStorage is separate from Safari's, the
 * cookie is not) or cleared storage. Only reachable through src/proxy.ts, so
 * the caller already proved it holds the token (cookie, header or query);
 * nothing is revealed that the caller does not have. `{ token: null }` when
 * no ACCESS_TOKEN is set (dev).
 */
import { NextResponse, type NextRequest } from "next/server";
import { sameToken, TOKEN_COOKIE } from "@/lib/runtime";

export const dynamic = "force-dynamic";

export function GET(req: NextRequest): NextResponse {
  const headers = { "Cache-Control": "no-store" };
  const expected = process.env.ACCESS_TOKEN?.trim();
  if (!expected) return NextResponse.json({ token: null }, { headers });
  // Belt and braces: answer only a caller whose cookie itself matches.
  const cookie = req.cookies.get(TOKEN_COOKIE)?.value;
  if (!cookie || !sameToken(cookie, expected)) {
    return NextResponse.json({ error: "missing or wrong access token" }, { status: 401, headers });
  }
  return NextResponse.json({ token: expected }, { headers });
}
