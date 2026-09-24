# Zeroist app PRD: one app, easy to connect, obvious when it works

Written 2026-09-24 by Aidan's orchestrator session. Owners: two Ralph loops (native, web)
on branches `app/native` and `app/web`; the orchestrator merges to `main`.

## The problem

There are three phone codebases: Meta's CameraAccess sample plus our Link files (proven on
hardware, ugly), Rishi's native SwiftUI app Zeroist in `ios/Brian` (two tabs, simulator
only), and Lukas's web app in `phone/` (rich, no glasses). A tester today taps "Start
watching" and gets no feedback that anything is connected or working.

## The product

One app, **Zeroist** (`ios/Brian`, bundle `com.zeroist.app`). Native shell for the glasses,
connection and Today/Protocol; Lukas's screens embedded for everything else.

```
Zeroist
├─ Connect            full screen on first launch; one tap from anywhere (status pill)
├─ Tabs (exactly four, in this order)
│   ├─ Today          native: status pill · Start/Stop · healthy-life hours · ledger
│   ├─ Calendar       web view: Lukas's /calendar
│   ├─ Analysis       web view: Lukas's /analysis (+ his menu: treatments, tests,
│   │                 biomarkers, devices, library, find my protocol, concierge, sources)
│   └─ Protocol       native: today's items, mark done, undo, add
└─ Settings (gear)    invite link (change), voice on/off, Connect, permissions, version
```

Look and copy: `.claude/skills/brian-ios-design/SKILL.md` wins on anything visual or any
string. Vocabulary: earned / cost, healthy-life hours, "Bryan said" / "held back", seeded.

### Connect screen (the feedback fix)

Three rows. Each row: a colored dot (grey / amber / green), one line of live state, at most
one button. Below: one primary button. Below that, a live panel shown only while watching.

| Row | Grey | Amber | Green |
|---|---|---|---|
| Invite link | "Paste the link from your invite" (+ paste field) | "Checking…" | "Server reachable · <endpointLabel>" (+ Change) |
| Glasses | "Open Meta AI and tap Allow" (+ Open Meta AI / Register) | "Registering…" / "Connecting…" | "Connected" |
| Stream | "Not watching" | "Reconnecting…" / "Starting…" | "Watching 12 min · 0.7 frames/s · last frame 1 s ago" |

Primary button: **Start watching** → **Stop** while live (the one prominent control).
Live panel (only while watching): frames sent, "server acknowledged" yes/no, and
"Bryan last said, 14:02: '…'" (or "Bryan has not spoken yet"). A **Test voice** button
plays one line through the glasses (LinkGlue.sayTestLine exists).
Invite link on the clipboard when the screen opens (matches wss://…/ws/glasses?token=) →
offered with one tap ("Use the link on your clipboard").
Permissions rows move to a collapsed "Permissions" section at the bottom (still needed).
Red states (token rejected, glasses unavailable) show the fix in one sentence, `Brian.cost`.

### Status pill (every tab)

A capsule at the top of every tab: green "Watching · 12 min", amber "Reconnecting…" /
"Starting…", red "Glasses off" / "Not connected" / "Invalid invite link". Tap → Connect.
Replaces the three-row StatusStrip on Today.

### Web tabs

Calendar and Analysis are `WKWebView`s. URL = web base + path + query, where
web base = `https://DOMAIN/t/NAME/app` derived from the invite link (same host and
`/t/NAME` prefix; LAN `ws://ip:8010` links have no web app → tab shows "Not available on a
LAN link"). First load carries `?token=T&embed=1`; later navigation keeps `embed=1`
(the web app persists it). States: loading spinner, offline/unreachable with Retry,
no invite link → points to Connect. Demo mode (`-demo`): web base = `BRIAN_WEB_BASE`
env/launch arg if set (the loop runs Lukas's dev server with fixtures at
http://127.0.0.1:3100), else a "Seeded" placeholder panel.

### Contract between native and web (both loops build to this, no negotiation)

- Web app query `embed=1`: hide the web app's own tab bar and top bar chrome; keep the
  hamburger menu button and the screen title; content uses the full height; every
  in-app link keeps `embed=1` (persist in sessionStorage; also honor a cookie
  `zeroist_embed=1`). No `Open in app` prompts. No Today/Protocol links in the menu
  while embedded (those tabs are native).
- Token: `?token=T` on ANY route sets the auth cookie and is stripped from the URL
  (TokenCapture / proxy.ts already do this at `/`; must work on `/calendar` and
  `/analysis`).
- Health: `GET <web base>/healthz` → 200 without a token (exists).

## Verification (every story, no exceptions)

- Native: `xcodegen generate` then `xcodebuild … -destination 'platform=iOS Simulator,name=<an available iPhone>' build` and `test`.
  Screenshots via `xcrun simctl io <udid> screenshot` for every screen a story touches,
  in `-demo` mode, saved to `ios/Brian/Screenshots/<story>-<screen>.png` and committed.
  If `xcrun simctl list devices available | grep -q iPhone` is empty (runtime still
  downloading), build with `-destination 'generic/platform=iOS Simulator'`, commit the
  non-visual work, and stop the iteration with `<promise>WAITING_SIMULATOR</promise>`.
- Web: `npm run lint && npx tsc --noEmit && npm test && npm run build`; screenshots with
  `node scripts/shot.mjs` against the fixtures dev server, saved to
  `phone/screenshots/<story>-<screen>.png` and committed.
- The orchestrator reads every committed screenshot and runs the design checklist
  before merging a branch.

## Ownership and rules

- `app/native` loop may edit only `ios/Brian/**` and `docs/APP_NATIVE_NOTES.md`.
- `app/web` loop may edit only `phone/**` and `docs/APP_WEB_NOTES.md`.
- Neither touches `ios/*.swift` (the CameraAccess fallback), `backend/`, `deploy/`,
  `dashboard/`, or `main`. Granular commits, one story or less per commit, pushed to the
  loop's branch after every commit. Never force-push. Never rebase.
- Wire contract unchanged (`src/longevity/wire.py`); the Link files stay additive-only.

## Out of scope for this PRD

Health, Calendar write, Screen Time, on-device hardware test (needs Rishi's signing
checkboxes), TestFlight upload, deleting the CameraAccess project (after hardware proof).
