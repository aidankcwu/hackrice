# Zeroist demo UI — PRD

Branch: `demo`. Product spec for the revamped phone app that goes to the zfellows tester.
Decisions and their reasoning are in [REDESIGN_NOTES.md](REDESIGN_NOTES.md). Look and copy:
`.claude/skills/brian-ios-design/SKILL.md` wins on anything visual or any string, with the
additions in "Design additions" below. Build notes: [APP_NATIVE_NOTES.md](APP_NATIVE_NOTES.md),
[APP_WEB_NOTES.md](APP_WEB_NOTES.md).

## The product in one screen

Three tabs and a fixed header. No hamburger menu anywhere. No Calendar tab for the demo.

```
Header (fixed, every tab)   [● 82% ⌐■ Watching · 14 min]   [Stop]   [Preview]   [⚙]
Tabs
├─ Home       metrics · daily summary · protocol card · sessions · stats sheet · log
├─ Analysis   Lukas's /analysis web page, embedded, no menu, no web title
└─ Protocol   today's steps with checkboxes · templates · add / edit / delete
Pushed / sheets: Connect (from the pill), Preview, Hero explanation, Session detail, Settings
```

Home is what a tester screenshots and sends to Bryan Johnson. Everything on it must
either show real data from the glasses or say plainly that nothing was watched yet.

## Header (D-001)

A bar pinned above the tab content on every tab; it does not scroll with the page.
Native toolbar with Liquid Glass; no custom chrome.

- **Status pill** (leading): battery percent when known ("82%"; nothing when unknown),
  then one `eyeglasses` symbol that is `Brian.ink` when the glasses are worn or at least
  connected, `Brian.muted` when off or not worn, then the existing one-line
  `ConnectionStatus` text with its coloured dot. Tap opens Connect (unchanged).
- **Start watching / Stop** button: the only `.glassProminent` control on screen. Same
  behaviour and consent gate as today's Connect button. Disabled until
  `ConnectRows.canStart`. This replaces the floating Stop button on Today.
- **Preview** button (`camera.viewfinder`, `.glass`): opens the Preview sheet (D-002).
  Disabled unless the glasses are connected.
- **Settings gear** (trailing), as now.

Data: `GlassesSessioning` gains `deviceState: GlassesDeviceState?` (`batteryLevel: Int?`,
`charging: Bool`, `worn: Bool?`) and `onDeviceStateChange`. The real session resolves
`Wearables.shared.deviceForIdentifier(id)` and uses `addDeviceStateListener` (DAT docs,
"Device state"); values are only trusted while the device's `linkState` is connected.
Mock glasses report 82%, worn. Demo fixtures the same. `-glassesOff` reports nil.

## Preview sheet (D-002)

A sheet titled "Glasses view": the most recent camera frame, updated at most twice a second
while the sheet is open, plus one line under it: "Live · 0.7 frames/s" or "No frames yet".
`GlassesSession` already receives every `VideoFrame`; it keeps the latest `UIImage` in
`AppState.previewFrame` only while `previewOpen` is true (frames are never stored otherwise:
CLAUDE.md invariant 4). Demo mode shows `Fixtures/preview.jpg` (add one: any indoor photo,
not a person). Nothing is written to disk.

## Home (D-003 … D-008)

`ScrollView` on `Brian.page`, gutters 24, section gaps 32, in this order.

### 1. Metrics (D-003)

- Hero: `SignedHours` at `BrianType.hero`, word under it (`Hours.word`), provenance chip.
  Tapping the hero opens the **"How this is measured"** sheet: one paragraph in plain words
  ("Each thing the glasses saw today is matched to a published dose-response study and
  turned into hours of healthy life gained or lost. Anything not seen counts as average,
  never as a gain."), then a list of `healthspan.factors` rows: label, dose with unit,
  signed hours chip, and a provenance word (Glasses / Seeded / Missing) in `Brian.muted`.
  Source citation under each row in `BrianType.caption`.
- Two tiles beside or under the hero (side by side at normal sizes, stacked at accessibility
  sizes): **Daylight** (minutes outdoors today) and **Screens** (minutes of sustained screen
  time seen). Each tile: value in `BrianType.number`, label in `BrianType.secondary` muted.
  Tiles are `Brian.surface` panels, 20 pt radius.
- One line under the metrics: "Watched 3 h 10 min today" (sum of today's sessions, or of
  the current watching span when no sessions exist), or "Not watched yet today".

### 2. Daily summary (D-004)

One panel. Header row: "Today" in `BrianType.title` and "as of 6:12 PM" muted. Body:
`headline` in `BrianType.body` semibold, then `paragraphs` in `BrianType.body`, then
`suggestions` as a plain list with `arrow.turn.down.right` symbols, muted. A `.glass`
"Refresh" button at the bottom right.

Data: `POST /api/recap` with `{"from": <local midnight>, "to": <now>, "speak": false}`
returns `headline`, `paragraphs`, `suggestions` (and `spoken`, unused). Fetch on first
appearance of Home when there is at least one episode today, then only on Refresh or when
a session ends. Cache the last result in memory with its time. While loading, the previous
text stays and the header says "Updating…". Before any episode today: the body is
"Nothing watched yet today. Put the glasses on and the summary writes itself." and no
Refresh. Demo mode: `Fixtures/recap_today.json` with a realistic headline, two
paragraphs and three suggestions written in Bryan's voice about the fixture day.

### 3. Protocol card (D-005)

One panel: header "Protocol · 3 of 5" (`BrianType.title`), then one row per item in
window order: kind symbol, name, and a trailing state word with its symbol: "Seen 8:42 AM"
(`checkmark.circle.fill`, ink), "Done" (`checkmark.circle`, ink), "Missed"
(`xmark.circle`, `Brian.cost`), "Open until 10 PM" (muted), "Later" (muted). Tapping the
card opens the Protocol tab. No editing on the card. Empty: "No protocol yet." and a
`.glass` "Set one up" button that opens the Protocol tab.

### 4. Sessions (D-006)

One collapsed row: "Sessions · 3 · 3 h 10 min" with a chevron; expanded, one row per
session today, newest first: start–end ("2:10–3:25 PM"), duration, and the recap headline
if it exists. Tapping a session pushes **Session detail**: duration, the recap (headline,
paragraphs, suggestions) or "Summary still writing…" with a Refresh, then the ledger rows
that fall inside the session. No sessions: "No sessions yet today."

Data:
- Start watching calls `POST /api/session/start` (`{"name": ""}`); Stop calls
  `POST /api/session/end` (the backend then writes the recap by itself). Both are fire-and-
  forget from the phone's point of view; a failure is logged, never shown.
- `GET /api/sessions?limit=50` (new on `demo`, added by the orchestrator: returns
  `db.list_sessions`, newest first, each `{id, name, started_t, ended_t}`).
- Recap per session: `GET /api/recaps` lists `{id, session_id, generated_at, headline}`;
  `GET /api/recaps/{id}` returns the body. Match by `session_id`.
- Demo: `Fixtures/sessions_today.json` (three sessions) and `Fixtures/recaps.json`.

### 5. Stats sheet (D-007)

Header "Today's stats" (`BrianType.title`). A two-column grid of small stat cells
(`Brian.surface`, radius 14): value in `BrianType.number`, label in `BrianType.secondary`
muted. Derived on the phone from today's episodes (`Episode.kind`, `startT`, `endT`,
`durationS`, `label`, `dominant`), so no backend change:

| Stat | Rule | Empty word |
|---|---|---|
| First daylight | start of the earliest `outdoor_block` ("8:52 AM") | "Not yet" |
| Eating window | first `food_sighting` start → last `food_sighting` end ("12:10–7:45 PM · 7 h 35 min") | "No meals seen" |
| Last caffeine | start of the latest `caffeine_sighting` ("2:40 PM") | "None seen" |
| Screens | sum of `screen_block` durations ("2 h 05 min") | "None seen" |
| Longest focus | the longest single `screen_block` ("48 min") | "—" |
| Alcohol | count of `alcohol_sighting` ("1 sighting") | "None seen" |
| Time with people | sum of episodes whose kind or label contains people/social/conversation | "Not tracked yet" |
| Hydration | count of episodes whose label contains water/bottle/drink | "Not tracked yet" |

A stat whose rule finds nothing shows its empty word in `Brian.muted`; the cell stays so
the sheet keeps its shape. Kinds that the backend does not emit today ("Time with people",
"Hydration") stay on the sheet and say "Not tracked yet". The rules live in one pure
`TodayStats.derive(episodes:now:)` with tests.

### 6. Log (D-008)

A `DisclosureGroup` titled "Log", collapsed by default, at the bottom. Inside: the
existing ledger rows, **coalesced**: consecutive rows with the same label within 10 minutes
merge into one (earliest time, one row; if any merged row had an outcome chip, keep the
strongest: said > asked > acted > held back). The "Held back N today" footer stays.

### Empty state (D-009)

Fresh install with a reachable link and nothing watched: metrics show "0.0 h", tiles
show "—", the watched line says "Not watched yet today", summary says its empty
sentence, protocol card offers "Set one up", sessions "No sessions yet today", stats show
their empty words, Log hidden. Nothing on the page may look broken or say "error".
Demo launch argument `-empty` renders exactly this.

## Protocol tab (D-005)

The existing ProtocolView, reworked:

- Header row under the title: "3 of 5 today" and a `.glass` "Add" button (moves out of the
  toolbar; the toolbar is the shared header).
- Each row gets a **checkbox** on the leading edge (`circle` / `checkmark.circle.fill`,
  ink, 44 pt target). Tap toggles `markDone` / `undo`. Rows marked seen by the camera show
  the checkmark filled and the evidence thumbnail (real image only; no placeholder box
  when the image is missing) and "Seen 8:42 AM".
- Swipe actions stay (Delete, Undo / Mark done).
- A **Templates** section under the list: the six groups from the web library (Doses,
  Meals, Movement, Light, Sleep, Screens), each a row that expands into template items
  with name and window; tapping "Add" on one creates it through `addProtocolItem`.
  Template data is a static Swift table (`ProtocolTemplates.swift`) copied from
  `phone/src/lib/protocol.ts` and the library fixtures; kinds map to the backend's four
  (dose, meal, walk, winddown). At least 3 templates per group.
- Edit: tapping a row's name opens AddItemView pre-filled (name, kind, window, days), saved
  through a `PUT /api/protocol/{id}` (exists; add `updateProtocolItem` to APIClient and AppState).

## Analysis tab (D-010, web W-101 … W-103)

`WebScreen` on `/analysis` as now. Web side, in embed mode: hide the hamburger button and
the page's own `<h1>` (the native inline title "Analysis" is the only title), remove the
"Find my protocol" pill, and fix the week chart's left clipping. Nothing in the embedded
page may navigate to treatments, tests, biomarkers, devices, library, sources, claims,
concierge or settings; those routes stay in the repo but are unreachable from the embed.

## Removed from the app

Calendar tab (web view), the hamburger and everything behind it, the floating Stop button
on Today, the "Today" ledger as the main content, the standalone status pill on Today's
old layout. `AppScreen` ids become `home`, `analysis`, `protocol`, `connect`, `settings`,
`preview`; `today` stays as an alias of `home` for old scripts.

## Design additions (beyond the skill)

- Stat cells and tiles are the only new surface: `Brian.surface`, radius 14 (cells) / 20
  (tiles and panels). No borders, no shadows.
- The header pill may hold a number (battery). It is the only place a percent appears.
- Checkboxes are SF Symbols in ink, never the system blue.
- Summary and recap prose is shown as the backend wrote it; never rewritten on the phone.
- Every new screen must pass the skill's rubric at default and accessibility XXXL sizes.

## Verification (every story)

Native: `xcodegen generate`, build, and `xcodebuild test` exit 0; Swift Testing suites for
every pure derivation (`TodayStats`, ledger coalescing, sessions grouping, header state);
screenshots in demo mode for every screen the story touches, saved to
`ios/Brian/Screenshots/<story>-<screen>.png`, at default size and, for Home, at XXXL;
every PNG read by the author before commit. Web: `npm run lint && npx tsc --noEmit &&
npm test && npm run build`, screenshots via `scripts/shot.mjs` in embed mode.

## Ownership

- Native loop: `ios/Brian/**`, `docs/APP_NATIVE_NOTES.md`, `ralph/demo-native/**`.
- Web loop: `phone/**`, `docs/APP_WEB_NOTES.md`, `ralph/demo-web/**`.
- Orchestrator: `backend/**` (the `/api/sessions` route), `deploy/**`, merges, pushes to
  `origin/demo`, final visual review.
- Never force-push, never rebase, never touch `main`.
