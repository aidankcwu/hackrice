# APP_NATIVE_NOTES.md — the Zeroist iPhone app (ios/Brian), as built on `demo`

What the `demo-native` loop (stories D-001 to D-010, `ralph/demo-native/prd.json`) left
behind, and how to build, run and screenshot it. Product spec: [DEMO_UI_PRD.md](DEMO_UI_PRD.md)
(it wins); why: [REDESIGN_NOTES.md](REDESIGN_NOTES.md); look and copy:
`.claude/skills/brian-ios-design/SKILL.md`; the web side of the embed: [APP_WEB_NOTES.md](APP_WEB_NOTES.md).

## What is on screen

Three tabs (Home `house`, Analysis `chart.bar`, Protocol `pills`) under one header. No
hamburger, no Calendar tab. Every tab is on `Brian.page` (white / black) with 24 pt gutters;
only the sheets (Connect, Settings, the hero sheet, Add / Edit item) use grouped lists.

- **Header** (`Screens/StatusPill.swift`, `AppHeader`), the same on every tab and fixed
  while the page scrolls:
  - Navigation bar: the **status pill** leading (battery "82%" when known, `eyeglasses` in
    ink when worn / connected and muted otherwise, the `ConnectionStatus` dot and line;
    tap opens Connect), then **Preview** (`camera.viewfinder`, disabled unless the glasses
    are connected) and the **Settings** gear trailing. Bar text is capped at the default
    size with the large-content viewer on long press, as system bar items are.
  - Under it, a `safeAreaBar` with **Start watching / Stop**: the only `.glassProminent`
    control, a capsule sized to its label and centred, capped at xxxLarge. A hard top
    scroll edge (`scrollEdgeEffectStyle(.hard)`) keeps rows from scrolling legibly beside
    it. It could not join the top bar: even beside "Glasses off", "Start watching" folds
    Preview and the gear into a "•••" menu at 402 pt. Disabled until `ConnectRows.canStart`;
    Stop is never disabled; the consent sheet gates the first start.
  - All of it derives from `HeaderState` (pure, tested).
- **Home** (`Screens/HomeView.swift`), one scroll in this order: hero hours with the
  provenance chip (Glasses / Seeded / Unmeasured) and an ⓘ that opens **How this is
  measured** (`MeasuredView`: one paragraph, then each healthspan factor with dose, signed
  chip, provenance word and citation); Daylight and Screens tiles (stacked at accessibility
  sizes); "Watched 43 min today"; the **Today** summary panel (`SummaryCard`, backend
  `/api/recap` prose shown as written, Refresh, "Updating…"); the **Protocol** card
  ("Protocol · 3 of 5", tap opens the tab, "Set one up" when empty); **Sessions** (one
  collapsed row, expanded rows push **Session detail**: duration, recap or "Summary still
  writing…", the ledger rows inside the session); **Today's stats** (`TodayStats`, eight
  cells with empty words); **Log** (collapsed, coalesced by `LedgerCoalescer`, "Held back
  N today", hidden when empty).
- **Analysis**: `WebScreen` on `<web base>/analysis?token=T&embed=1` (+ `zeroist_embed=1`
  cookie). In embed mode the web page hides its menu, its `<h1>` and the "Find my
  protocol" pill. The native inline title is hidden by the system beside the wide
  "Watching · 14 min" pill; the tab bar names the tab. Error states use a `.glass` button
  (the header already holds the one prominent button).
- **Protocol** (`Screens/ProtocolView.swift`): a plain `List` on `Brian.page`, rows inset
  24 pt, full-width `Brian.line` hairlines like Home's Log (a List still, for swipe
  actions). "3 of 5 today" + `.glass` Add; each item has a leading ink checkbox (toggles
  Mark done / Undo), kind symbol, name, window and the state word ("Seen 8:42 AM", "Done",
  "Missed", "Open until 10 PM", "Later"), the evidence thumbnail when the image loads; tap
  the name to edit (`AddItemView`, `PUT /api/protocol/{id}`); swipe for Delete / Undo /
  Mark done. Then **Templates**: six groups from `ProtocolTemplates.swift`, each
  expanding into items with an Add (or "Added").
- **Preview** sheet ("Glasses view", `PreviewView` + `PreviewFeed`): the latest camera
  frame at most twice a second while open, and "Live · 0.7 frames/s" / "Last frame 12 s
  ago" / "No frames yet". Frames reach AppState only through `onPreviewFrame`, which is
  nil unless the sheet is open; nothing is written to disk.
- **Connect** (sheet from the pill; full screen on first launch): Invite link, Glasses and
  Stream rows, the Start / Stop button, Live panel while watching, Permissions collapsed.
- **Settings** (sheet): invite link + Change, Connect row, Speak through the glasses,
  Permissions, Record corpus, Debug, Version.

Views read only AppState, Models and Theme. Pure, tested derivations: `HeaderState`,
`ConnectionStatus`, `ConnectRows`, `WebSource`, `ProtocolSummary`, `ProtocolTemplates`,
`SessionsSummary`, `SummaryCard`, `TodayStats`, `LedgerCoalescer`, `PreviewFeed`
(174 Swift Testing tests in `Tests/`).

## Build and test

Xcode 26 with the iOS 26 SDK, `xcodegen` on PATH (`~/.local/bin`). From `ios/Brian`:

```sh
cp Local.xcconfig.example Local.xcconfig     # optional (#include?); the Meta CLIENT_TOKEN, gitignored
xcodegen generate                            # Brian.xcodeproj is generated; never hand-edit it
DEV=6C6A1567-D1EB-41FB-AF32-5FD183C5E60C     # iPhone 17 Pro, iOS 26.5 on the loop's Mac
xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" -configuration Debug build -quiet
xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" test -quiet
```

The product is `Zeroist.app`, bundle id `com.zeroist.app`; the Swift module stays `Brian`.
A merge conflict in `project.pbxproj`: take either side and re-run `xcodegen generate`.
The two MacLink "main actor-isolated static property" warnings predate this branch.

## Demo mode, fixtures and launch arguments

Demo mode reads `ios/Brian/Fixtures/*.json` and uses mock glasses; nothing touches the
network or the DAT SDK. It says "Seeded", never "demo". The fixture day is
`today_episodes.json` (episodes, 3:26–4:09 PM), `today_decisions.json`,
`today_healthspan.json`, `recap_today.json` (the Today summary), `sessions_today.json` +
`recaps.json` (three sessions), `protocol_today.json` (five items), `status.json` and
`preview.jpg` (a drawn desk scene, no people). `empty_*.json` are the `-empty` day.

| Argument | Effect |
|---|---|
| `-demo` (or env `BRIAN_DEMO=1`) | Fixtures, mock glasses connected (82%, worn), LAN link `10.0.0.5:8010` reachable, watching for 14 min, fake 1.5 s frame cadence. |
| `-screen <id>` | Opens on `home` (`today` still works), `analysis`, `protocol`, `connect`, `settings`, `preview`, `measured` (the hero sheet), `session` (newest session's detail pushed), `protocol-templates` (Templates, Doses open) or `protocol-edit` (first item's edit sheet). |
| `-showSetup` | Older spelling of `-screen connect`. |
| `-scrollTo <section>` | Demo: Home scrolls to `metrics`, `summary`, `protocol`, `sessions` (expanded), `stats` or `log` (expanded) once loaded; `<section>-end` puts the section's bottom in view (`log-end` keeps the Log collapsed). |
| `-glassesOff` | Demo: glasses unavailable (red pill, muted glasses symbol, no battery, Preview disabled). |
| `-fresh` | Demo: first launch. No link, glasses not registered, not watching, clipboard offer, Start watching disabled; Preview says "No frames yet". |
| `-empty` | Demo: the empty day. Link reachable, glasses connected, not watching, `empty_*.json`. Every Home layer shows its empty word, Log hidden; the Protocol tab can still add items (in memory). Use `-scrollTo stats-end`, not `log-end`. |
| `-tokenRejected` | Demo: the server refused the link (red Invite link row), not watching. |
| `-webBase <url>` (or env `BRIAN_WEB_BASE`) | Demo: where Analysis loads from. Without it the tab shows the Seeded placeholder. |

## Screenshots

```sh
xcrun simctl boot $DEV                        # "already booted" is fine
APP=$(xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" -showBuildSettings \
      | awk -F' = ' '/ BUILT_PRODUCTS_DIR/{print $2; exit}')/Zeroist.app
xcrun simctl install $DEV "$APP"
xcrun simctl ui $DEV appearance light         # or dark
xcrun simctl launch $DEV com.zeroist.app -demo -screen protocol
sleep 3; xcrun simctl io $DEV screenshot Screenshots/D-010-protocol.png
```

- Dynamic Type: `xcrun simctl ui $DEV content_size accessibility-extra-extra-extra-large`
  (XXXL) or `accessibility-extra-extra-large` before launching; reset with `content_size large`.
- Analysis needs the phone app's fixtures server. Run it from a scratch copy, not from
  `phone/`: `git archive --format=tar HEAD phone | tar -x -C $SCRATCH`, symlink
  `phone/node_modules` into the copy, then from the copy
  `TURBOPACK_ROOT=/ NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100 -H 0.0.0.0` and launch with
  `-demo -screen analysis -webBase http://Aidan-mini.local:3100` (`*.local` is already in
  `allowedDevOrigins`; with `127.0.0.1` add it there first). Wait about 12 s.
- The final set is `Screenshots/D-010-*.png`: home, home-summary, home-dark, home-off,
  home-empty, home-xxl, home-xxxl, analysis, analysis-dark, analysis-xxxl, protocol,
  protocol-templates, protocol-dark, protocol-edit-dark, protocol-empty, protocol-xxxl,
  connect, connect-dark, connect-xxxl, settings, settings-xxxl, preview, preview-xxxl,
  session, session-xxxl, hero-sheet, hero-sheet-xxxl. Earlier stories' shots
  (`D-001`…`D-009`) show the full-width Start / Stop bar and the inset-card Protocol that
  D-010 replaced.

## Design pass (D-010)

Checked every screen above against the brian-ios-design rubric, light, dark and XXXL.
Changed:
- Header: Start / Stop is a compact capsule, not a full-width bar; hard scroll edge.
- Protocol: off the grey grouped background onto `Brian.page` with Home's hairline rows;
  Templates is a title row, not a grey section header; the empty sentence has room.
- Dark mode: Connect's Start / Stop label takes `Brian.page`, as the header's and
  AddItemView's do (the ink tint is near-white in dark, the label was white on white).
- XXXL: the Protocol checkbox is capped and no longer overlaps the name; state words
  wrap inside the gutter ("Seen" / "8:42 AM"); Connect puts each " · " part on its own
  line and shrinks an address rather than breaking "10.0.0.5:801 / 0"; Settings stacks
  Change under the link ("Cha / nge" before) and scales its status dot.
- Analysis error states: `.glass` instead of a second `.glassProminent`.

Known and web-side (APP_WEB_NOTES.md): the embedded week chart leaves an empty band on
its right; the web page ignores Dynamic Type.

## Untested on hardware

Everything here was verified in the simulator in demo mode and in unit tests only:
- Header battery / worn from DAT `DeviceState` on the real glasses, and the Preview button
  enabling with the real link state.
- The Glasses view sheet with real DAT frames (rate, orientation, memory of real frames).
- Connect's live rows against a real DAT session (Registering…, Connecting…, Glasses off
  after a power-cycle), and Open Meta AI / Register round trips.
- Session start / end calls, `/api/sessions`, `/api/recaps` and `/api/recap` against the
  hosted backend; the summary and session recaps have only been read from fixtures.
- Protocol add / edit / delete / mark done against the hosted backend, and evidence
  thumbnails from the real evidence route.
- Clipboard detection with a real invite link on a device.
- Frame counters, "Server acknowledged" and "Bryan last said" fed by the real
  `CapturePacketSender` and `MacLink` over the hosted `wss://` link; Test voice.
- Analysis against the hosted app: `?token=` + `embed=1` + cookie over HTTPS, the
  401/403 → token-rejected state, token rotation.
- Amber "Reconnecting…" after a Wi-Fi drop: only seen through the derivation tests.
