/**
 * Access gate for a hosted tester. With `ACCESS_TOKEN` unset (local dev) this
 * is a no-op. With it set, every page and every dashboard `/api/*` route needs
 * the token, because the server renders the tester's own data with it:
 *
 *   ?token=…            first visit (the link the VC is given); sets the cookie
 *   bryan_token cookie   every visit after that
 *   X-Access-Token       scripts / curl
 *
 * The browser separately keeps the token in localStorage for its direct
 * backend calls (lib/runtime.ts).
 */
import { NextResponse, type NextRequest } from "next/server";
import { sameToken, TOKEN_COOKIE, TOKEN_HEADER, TOKEN_PARAM } from "@/lib/runtime";

// Node runtime so ACCESS_TOKEN is read from the container env at request time
// (verified in the standalone image: the Node middleware runs there).
//
// The bare "/" entry is load-bearing. Next prefixes every matcher with basePath,
// and the catch-all below then compiles to `^/t/NAME/dashboard(?:/(...))$` with a
// REQUIRED slash after the base path. The dashboard root is served at
// `/t/NAME/dashboard` (no trailing slash; `/t/NAME/dashboard/` 308s to it), so
// without "/" the one page that renders the tester's data was never gated.
// "/" compiles to `^/t/NAME/dashboard[/#?]?$`, which covers exactly that root.
export const config = {
  runtime: "nodejs",
  matcher: ["/", "/((?!_next/static|_next/image|favicon.ico|healthz).*)"],
};

const THIRTY_DAYS_S = 60 * 60 * 24 * 30;

function denied(req: NextRequest): NextResponse {
  const headers = { "Cache-Control": "no-store" };
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "missing or wrong access token" }, { status: 401, headers });
  }
  const body =
    '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bryan</title>' +
    '<body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 16px">' +
    "<h1>Access link needed</h1><p>Open the dashboard with the link you were given; it ends in " +
    "<code>?token=…</code>.</p></body>";
  return new NextResponse(body, { status: 401, headers: { ...headers, "Content-Type": "text/html; charset=utf-8" } });
}

export function middleware(req: NextRequest): NextResponse {
  const expected = process.env.ACCESS_TOKEN?.trim();
  if (!expected) return NextResponse.next();

  const fromQuery = req.nextUrl.searchParams.get(TOKEN_PARAM)?.trim();
  const presented = fromQuery || req.headers.get(TOKEN_HEADER)?.trim() || req.cookies.get(TOKEN_COOKIE)?.value;
  if (!presented || !sameToken(presented, expected)) return denied(req);

  const res = NextResponse.next();
  if (fromQuery && req.cookies.get(TOKEN_COOKIE)?.value !== fromQuery) {
    const proto = req.headers.get("x-forwarded-proto") ?? req.nextUrl.protocol.replace(":", "");
    res.cookies.set(TOKEN_COOKIE, fromQuery, {
      httpOnly: true,
      sameSite: "lax",
      secure: proto === "https",
      path: req.nextUrl.basePath || "/",
      maxAge: THIRTY_DAYS_S,
    });
  }
  return res;
}
