/**
 * Access gate for a hosted tester (the dashboard's middleware, for this app).
 * With `ACCESS_TOKEN` unset (local dev, fixtures mode) it is a no-op. With it
 * set, every page and this app's own `/api/*` need the tester's token:
 *
 *   ?token=…                        first visit (the link the VC is given); sets the cookie
 *   brian_app_token cookie          every visit after that, and client navigations
 *   X-Access-Token / Bearer         scripts and curl
 *
 * The browser separately keeps the token in localStorage and sends it to the
 * backend itself (src/lib/runtime.ts); the backend does its own check.
 *
 * Next 16: the `middleware` file is now `proxy`, always on the Node runtime, so
 * `ACCESS_TOKEN` is read from the container env per request (no `runtime` key:
 * setting one in a proxy file is an error).
 */
import { NextResponse, type NextRequest } from "next/server";
import { sameToken, TOKEN_COOKIE, TOKEN_HEADER, TOKEN_PARAM } from "@/lib/runtime";

// The bare "/" entry is load-bearing (the dashboard's lesson). Next prefixes every
// matcher with basePath, so the catch-all compiles to `^/t/NAME/app(?:/(...))$`
// with a REQUIRED slash after the base path; the app root is served at
// `/t/NAME/app` with no slash, and without "/" the Today screen was never gated.
// Left open: build assets, /healthz, and the Add to Home Screen manifest and icons
// (iOS fetches those without the page's cookie; none of them holds data).
export const config = {
  matcher: [
    "/",
    "/((?!_next/static|_next/image|favicon\\.ico|healthz|manifest\\.webmanifest|icon\\.png|apple-icon\\.png|icon-192\\.png|icon-512\\.png).*)",
  ],
};

const THIRTY_DAYS_S = 60 * 60 * 24 * 30;

function denied(req: NextRequest): NextResponse {
  const headers = { "Cache-Control": "no-store" };
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "missing or wrong access token" }, { status: 401, headers });
  }
  const body =
    '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Brian</title>' +
    '<body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 16px">' +
    "<h1>Access link needed</h1><p>Open Brian with the link you were given; it ends in " +
    "<code>?token=…</code>.</p></body>";
  return new NextResponse(body, { status: 401, headers: { ...headers, "Content-Type": "text/html; charset=utf-8" } });
}

function bearer(req: NextRequest): string | undefined {
  const m = /^Bearer\s+(.+)$/i.exec(req.headers.get("authorization") ?? "");
  return m?.[1]?.trim() || undefined;
}

export function proxy(req: NextRequest): NextResponse {
  const expected = process.env.ACCESS_TOKEN?.trim();
  if (!expected) return NextResponse.next();

  const fromQuery = req.nextUrl.searchParams.get(TOKEN_PARAM)?.trim();
  const presented =
    fromQuery || req.headers.get(TOKEN_HEADER)?.trim() || bearer(req) || req.cookies.get(TOKEN_COOKIE)?.value;
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
