# Web app notes for the native side

Written 2026-09-24 by the `app/web` loop. What Zeroist's Calendar and Analysis web views
can rely on from `phone/`, built to the contract in docs/APP_PRD.md. Source of truth in
code: `phone/src/lib/embed.ts`, `phone/src/proxy.ts`, `phone/src/lib/runtime.ts`,
`phone/src/lib/screens.ts`.

## URLs

Web base, hosted: `https://DOMAIN/t/NAME/app` (same host and `/t/NAME` as the invite link
`wss://DOMAIN/t/NAME/ws/glasses?token=T`). Web base, demo: `http://127.0.0.1:3100` (the
fixtures dev server, no prefix). No trailing slash on any path.

| Tab / use | First load | Later loads |
|---|---|---|
| Analysis (demo) | `<base>/analysis?token=T&embed=1` | `<base>/analysis?embed=1` |
| Calendar (not in the demo) | `<base>/calendar?token=T&embed=1` | `<base>/calendar?embed=1` |
| Health | `GET <base>/healthz` → `200 {"ok":true}`, no token needed | same |

**The demo app loads only `/analysis`** (docs/DEMO_UI_PRD.md: the Calendar tab is gone).
`/calendar` still embeds the same way and is held to the same rules below.

Always send `embed=1` on the first load of each web view (sessionStorage is per web
view, so one tab's embed does not carry to the other). Order of params does not matter.

No other route is reachable from the embed. `/find`, `/library`, `/treatments`, `/tests`,
`/biomarkers`, `/devices`, `/concierge`, `/sources`, `/claims` and `/settings` stay in the
repo and still render (with a Back button, falling back to `/analysis`) if loaded by URL,
but nothing on the embedded `/analysis` or `/calendar` links or navigates to them.

## Embed (`embed=1`)

Embed is on when, checked in order:

1. the URL has `embed=1` (stored in sessionStorage for the web view's life; `embed=0`
   turns it off and clears the store);
2. sessionStorage `zeroist.embed` is `1` (so in-app links and reloads without the param
   keep it);
3. a cookie `zeroist_embed=1` is present. **Set it without HttpOnly**: it is read from
   `document.cookie`. Setting it in the web view's `WKHTTPCookieStore` for the web
   host makes embed independent of the query entirely.

An inline `<head>` script decides before first paint and sets `<html data-embed="1">`,
so the web tab bar never flashes.

Embedded, the web app (Shell, `phone/src/components/Shell.tsx`):

- on `/analysis` and `/calendar` renders **no** top pill (so no "Find my protocol" pill), no
  hamburger, no `<h1>` screen title and no bottom tab bar; the native inline title is the
  only title, and the content starts where the web title row used to (safe-area + 16 px);
- never mounts the Menu: there is no button, key or route that opens it;
- pushed screens (only reachable by typing their URL) keep a Back button; with no history
  Back goes to `/analysis`, never to the web Today or Protocol;
- shows no "Open in app" / Add to Home Screen prompts (there are none in the app).

What is left on `/analysis`, embedded: the range control (This week, 2 weeks, 30 days), the
chart, the cluster cards, the totals with the "Biggest lever" button (opens a sheet in
place), the insight cards. No anchors. On `/calendar`: previous/next day, Day/Week, and the
bars that open a day detail in place. No anchors.

### Guard: the reachability audit (W-103)

`phone/src/components/embedReach.test.tsx`, run by `npm test`:

- renders the embedded Shell around `Analysis` and `Calendar` with the seeded month (and
  every rule's lever sheet) and fails on any `href`, `action` or `formaction` whose path is
  one of the ten screens above, or on a Menu button;
- walks the import graph from `app/layout.tsx`, `app/analysis/page.tsx` and
  `app/calendar/page.tsx` (the Menu is not followed) and fails if any module names one of
  those screens (`"/tests"`, `` `/library…` ``, `SCREENS.find`, `SCREENS["settings"]`), so a
  button that `router.push`es there fails too. `Shell.tsx` (its Find pill and tab bar live
  in the non-embedded branch) and `lib/screens.ts` (the route table) are covered by the
  render checks instead.

A live crawl of the fixtures server (every button clicked; `m`, `/`, `?` and Escape pressed)
agreed on 2026-09-24: no anchors, no button leaves the page, no menu.

## Token

- `?token=T` works on **any** route. The server gate (`proxy.ts`, active only when the
  container has `ACCESS_TOKEN`) accepts it and sets cookie `brian_app_token=T`:
  HttpOnly, SameSite=Lax, Secure on https, `Path=/t/NAME/app`, 30 days.
- The page also stores T in localStorage (`brian.token:/t/NAME`) and sends it to the
  backend as `Authorization: Bearer`. It then removes `token` from the address bar with
  `history.replaceState`; `embed=1` and every other param survive.
- After that, loads without `?token=` pass on the cookie. A page load with no token and
  no cookie gets a 401 HTML page "Access link needed": re-open with `?token=T`.
- `/healthz`, build assets and icons are never gated.
- Demo/fixtures server has no `ACCESS_TOKEN`: the gate is off and any token is ignored
  except for being scrubbed from the URL.

Changing the invite link: load the new `?token=` (it overwrites both the cookie and the
stored copy). A different `NAME` is a different cookie path and storage key, so testers
never collide.

## Known gaps (web side, not fixed)

- Adopting a protocol (Find's last step, Library's review screen) navigates to the web
  `/protocol`. Neither is reachable from the demo embed; outside it, the pick is saved in the
  web view's localStorage and the native Protocol tab does not see it.

## Analysis chart (W-102)

The hour axis (6, 12, 18, 24) is pinned outside the chart's scroller, and the scroller is a
whole number of 40 px columns wide (CSS `round(down, …)`), so every range opens on the latest
day with only whole columns showing and the hours always on screen. `node scripts/overflow.mjs
"" <width>` fails if any day column is cut at either edge of the scroller or the hours leave the
screen; it passes at 375 and 393 px for all three ranges.

## Screenshots (`phone/screenshots/`, 390 × 844, fixtures, `embed=1`)

| File | Shows |
|---|---|
| `W-101-analysis.png` | `/analysis?embed=1`: no hamburger, no title, no Find pill, no tab bar |
| `W-101-calendar.png` | `/calendar?embed=1`: same |
| `W-001-*`, `W-003-*` | Superseded (pre-demo embed with hamburger, title and Menu) |
| `W-102-analysis-week.png` | `/analysis?embed=1` at 393 px, This week: hours pinned, seven days whole |
| `W-102-analysis-30d.png` | Same, 30 days: opens on the latest eight whole days |

Reproduce: from `phone/`, `TURBOPACK_ROOT=/ NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100`
(`TURBOPACK_ROOT` only in a worktree with a symlinked node_modules), then
`node scripts/shot.mjs "/analysis?embed=1" out.png`; add
`--click='[aria-label=Range] button:nth-child(3)'` for 30 days.
