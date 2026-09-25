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
| Calendar | `<base>/calendar?token=T&embed=1` | `<base>/calendar?embed=1` |
| Analysis | `<base>/analysis?token=T&embed=1` | `<base>/analysis?embed=1` |
| Health | `GET <base>/healthz` → `200 {"ok":true}`, no token needed | same |

Always send `embed=1` on the first load of each web view (sessionStorage is per web
view, so one tab's embed does not carry to the other). Order of params does not matter.

Pushed screens, reached from the Analysis hamburger (all same-origin, all keep embed):
`/find`, `/library`, `/treatments`, `/tests`, `/biomarkers`, `/devices`, `/concierge`,
`/sources`, `/claims`, `/settings` (menu card "Account").

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

Embedded, the web app:

- hides its bottom tab bar and its top pill/top bar chrome; keeps the hamburger button
  and the screen title; content uses the full height (safe-area padding still applies);
- menu drops **My protocol** (and Today is never in it), and shows a **Find my protocol**
  card first in place of the hidden top pill's button;
- pushed screens show a Back button; with no history (a cold load of a pushed URL) Back
  goes to `/analysis`, never to the web Today or Protocol;
- shows no "Open in app" / Add to Home Screen prompts (there are none in the app).

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
  `/protocol`: embedded, that shows the web Protocol screen (hamburger and title, no Back).
  The pick is saved in the web view's localStorage; the native Protocol tab does not see it.
- The menu still offers **Account** (`/settings`: backend, token, appearance), which
  overlaps native Settings.

## Analysis chart (W-102)

The hour axis (6, 12, 18, 24) is pinned outside the chart's scroller, and the scroller is a
whole number of 40 px columns wide (CSS `round(down, …)`), so every range opens on the latest
day with only whole columns showing and the hours always on screen. `node scripts/overflow.mjs
"" <width>` fails if any day column is cut at either edge of the scroller or the hours leave the
screen; it passes at 375 and 393 px for all three ranges.

## Screenshots (`phone/screenshots/`, 390 × 844, fixtures, `embed=1`)

| File | Shows |
|---|---|
| `W-001-calendar.png` | `/calendar?embed=1`: no tab bar, hamburger and title only |
| `W-001-analysis.png` | `/analysis?embed=1`: same |
| `W-003-menu.png` | Menu from Analysis, embedded: Find first, no My protocol |
| `W-003-treatments.png` | `/treatments?embed=1` with Back |
| `W-003-biomarkers.png` | `/biomarkers?embed=1` with Back |
| `W-003-devices.png` | `/devices?embed=1` with Back |
| `W-102-analysis-week.png` | `/analysis?embed=1` at 393 px, This week: hours pinned, seven days whole |
| `W-102-analysis-30d.png` | Same, 30 days: opens on the latest eight whole days |

Reproduce: from `phone/`, `TURBOPACK_ROOT=/ NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100`
(`TURBOPACK_ROOT` only in a worktree with a symlinked node_modules), then
`node scripts/shot.mjs "/calendar?embed=1" out.png`; add
`--click='button[aria-label=Menu]'` for the menu.
