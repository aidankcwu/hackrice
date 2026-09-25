# APP_NATIVE_NOTES.md — the Bryan iPhone app (ios/Brian), as built on `app/native`

What the `app/native` loop (stories N-001 to N-006, `ralph/native/prd.json`) left behind,
and how to build, run and screenshot it. Product intent is in [APP_PRD.md](APP_PRD.md);
look and copy in `.claude/skills/brian-ios-design/SKILL.md`; the web side of the embed
contract in [APP_WEB_NOTES.md](APP_WEB_NOTES.md).

## What is on screen

- **Tabs**: Today · Calendar · Analysis · Protocol (`list.bullet`, `calendar`, `chart.bar`,
  `pills`). Every tab has the **status pill** (leading toolbar) and the Settings gear.
- **Status pill**: one `ConnectionStatus` (grey / amber / green / red + one line:
  "Watching · 14 min", "Reconnecting…", "Glasses off"). Tap opens Connect.
- **Connect**: three rows (Invite link, Glasses, Stream), each a dot + one line of live
  state + at most one button; red rows add the fix in `Brian.cost`. Then the one primary
  button (Start watching / Stop), a Live section while watching (frames sent, server
  acknowledged, what Bryan last said, Test voice), Permissions collapsed at the bottom.
  Start watching is disabled until the invite link is reachable and the glasses are at
  least registered (`ConnectRows.canStart`); Stop is never disabled. A likely link on
  the clipboard (`detectedPatterns`, no paste prompt until the tap) shows the glass
  button "Use the link on your clipboard". Full screen on first launch, a sheet from
  the pill and from Settings.
- **Calendar / Analysis**: `WebScreen` (WKWebView) on `<web base>/calendar` and
  `/analysis` with `?token=T&embed=1` plus a `zeroist_embed=1` cookie. The web base comes
  from the invite link (`wss://DOMAIN/t/NAME/ws/glasses?token=T` → `https://DOMAIN/t/NAME/app`).
  States: loading, page, unreachable + Try again, token rejected + Open Connect, no link
  (points to Connect), LAN link (the Mac serves no web app), Seeded placeholder (demo).
- **Settings**: invite link label + Change (opens Connect's paste field), Connect row
  with the live status, Speak through the glasses, Permissions (collapsed), Record corpus
  (off by default), Debug (DEBUG builds), Version / Build. No server field anywhere else.

Status logic is pure and tested: `ConnectionStatus.derive`, `ConnectRows.derive`,
`WebSource.derive` (see `Tests/`). Views read only AppState, Models and Theme.

## Build and test

Xcode 26 with the iOS 26 SDK, `xcodegen` on PATH (`~/.local/bin`). From `ios/Brian`:

```sh
cp Local.xcconfig.example Local.xcconfig     # optional (#include?); the Meta CLIENT_TOKEN, gitignored
xcodegen generate                            # Brian.xcodeproj is generated; never hand-edit it
DEV=$(xcrun simctl list devices available | grep -E 'iPhone 1[5-7]' | head -1 \
      | sed -E 's/^ *(.*) \(([0-9A-F-]{36})\).*/\2/')
xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" -configuration Debug build -quiet
xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" test -quiet
```

The product is `Zeroist.app`, bundle id `com.zeroist.app`; the Swift module stays `Brian`.
A merge conflict in `project.pbxproj`: take either side and re-run `xcodegen generate`.
The two MacLink "main actor-isolated static property" warnings predate this branch.

## Demo mode and launch arguments

Demo mode uses fixtures and mock glasses; nothing touches the network or the DAT SDK.
It shows "Seeded", never "demo".

| Argument | Effect |
|---|---|
| `-demo` (or env `BRIAN_DEMO=1`) | Fixtures, mock glasses connected, LAN link `10.0.0.5:8010` reachable, watching for 14 min, fake 1.5 s frame cadence. |
| `-screen <id>` | Opens on `home` (`today` still works), `analysis`, `protocol`, `connect`, `settings` or `preview`. |
| `-showSetup` | Older spelling of `-screen connect`. |
| `-scrollTo <section>` | Demo: Home scrolls to `metrics`, `summary`, `protocol`, `sessions` (expanded), `stats` or `log` (expanded) once loaded; `summary-end` puts the section's bottom in view instead (`log-end` keeps the Log collapsed). |
| `-screen session` | Demo: Home with the newest session's detail pushed. |
| `-screen protocol-templates` / `-screen protocol-edit` | Demo: the Protocol tab scrolled to Templates with Doses open / with the first item's edit sheet up. |
| `-glassesOff` | Demo: glasses unavailable (red pill, red Glasses row). |
| `-fresh` | Demo: first launch. No link, glasses not registered, not watching, clipboard offer shown, Start watching disabled. |
| `-tokenRejected` | Demo: the server refused the link (red Invite link row), not watching. |
| `-webBase <url>` (or env `BRIAN_WEB_BASE`) | Demo: where Calendar and Analysis load from. Without it they show the Seeded placeholder. |

## Screenshots

```sh
xcrun simctl boot $DEV                        # "already booted" is fine
APP=$(xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" -showBuildSettings \
      | awk -F' = ' '/ BUILT_PRODUCTS_DIR/{print $2; exit}')/Zeroist.app
xcrun simctl install $DEV "$APP"
xcrun simctl ui $DEV appearance light
xcrun simctl launch $DEV com.zeroist.app -demo -screen connect
sleep 3; xcrun simctl io $DEV screenshot Screenshots/N-006-connect.png
```

- Dynamic Type: `xcrun simctl ui $DEV content_size accessibility-extra-extra-large` before
  launching; reset with `content_size large`.
- Web tabs need the phone app's fixtures server. Run it from a scratch copy, not from
  `phone/`: `git archive --format=tar HEAD phone | tar -x -C $SCRATCH`, symlink
  `node_modules` to a checkout that has one, add `"127.0.0.1"` to `allowedDevOrigins` in
  the copy's `next.config.ts` (Next 16 dev otherwise refuses its own scripts to that
  origin and the page sticks on "Loading…"), then
  `TURBOPACK_ROOT=/ NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100` and launch with
  `-demo -screen calendar -webBase http://127.0.0.1:3100`; wait about 12 s.
- The set in `Screenshots/N-006-*.png`: today, connect, connect-empty, calendar, analysis,
  protocol, settings, today-xxl, connect-xxl.

## Design pass (N-006)

Checked against the brian-ios-design rubric. Changed:
- Connect: the clipboard offer is a `.glass` button with `doc.on.clipboard`; Start
  watching dims until the rows above are ready.
- Accessibility sizes: Connect rows, Today's hero label + Seeded chip, Today's error row
  and ledger rows stack vertically instead of squeezing text to one word per line
  ("Change" used to break mid-word). The Connect dot scales with the text.
- Ledger: titles sentence case ("Meal, rice bowl"), 16 pt between symbol and title.
- Protocol: "Done (you)" → "Marked done".

Known and not native: the embedded web pages still show their own ☰ menu, and the
Analysis week chart is clipped on the left (web side, noted in APP_WEB_NOTES.md).

## Untested on hardware

Everything here was verified in the simulator in demo mode and in unit tests only:
- Connect's live rows against a real DAT session (Registering…, Connecting…, Glasses off
  after a power-cycle), and Open Meta AI / Register round trips.
- Clipboard detection with a real invite link on a device.
- Frame counters, "last frame N s ago", Server acknowledged and "Bryan last said" fed by
  the real `CapturePacketSender` and `MacLink` over the hosted `wss://` link.
- Test voice through the glasses speakers from the Connect screen.
- The web tabs against the hosted app: `?token=` + `embed=1` + cookie over HTTPS, the
  401/403 → token-rejected state, and what the pages do when the token rotates.
- Amber "Reconnecting…" after a Wi-Fi drop: only seen through the derivation tests.
