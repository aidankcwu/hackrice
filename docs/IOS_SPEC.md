# IOS_SPEC.md — the Brian iPhone app

What the app is: the thing the wearer opens. It pairs the glasses, streams to the backend,
plays whispers, and shows what the system handled today. The Mac (backend) still makes
every decision. The phone adds three things the Mac cannot: it is on the body, it holds
Apple Health, and it can act on the phone (calendar, Screen Time).

Look and copy: `.claude/skills/brian-ios-design/SKILL.md`, which inherits the dashboard's
`brian-ui` skill and its `references/voice.md`. They win over this file on anything visual
or any string. Vocabulary: earned / cost, healthy‑life hours, "Bryan said" / "held back",
seeded (never "demo" on screen), unmeasured.

## Structure

```
BrianApp
└─ RootView
   ├─ SetupView            first launch, and from Settings. A sheet the user can close.
   └─ TabView (2 tabs, Liquid Glass tab bar)
      ├─ TodayView         status strip · primary button · hero · ledger
      └─ ProtocolView      (Job 2) today's items · add · undo
   Toolbar (both tabs): Settings (gearshape) → SettingsView
```

No tab for settings. No chat. No in-app dashboard. No manual logging beyond Protocol's
Undo / Mark done.

## Project layout (`ios/Brian/`)

```
project.yml                 XcodeGen spec (starter provided; Job 1 finishes it)
Sources/
  App/        BrianApp.swift, RootView.swift, AppState.swift (one @Observable)
  Link/       MacLink.swift, CapturePacketSender.swift, PhoneSensors.swift,
              QuestionListener.swift, CorpusRecorder.swift   ← moved from ios/, unchanged
  Glasses/    GlassesSession.swift (DAT registration, availability, stream; ported
              from Meta's CameraAccess sample: WearablesViewModel + CameraViewModel)
              MockGlasses.swift (MockDeviceKit wrapper, DEBUG only)
  API/        APIClient.swift, Models.swift (Healthspan, Episode, Decision, ProtocolItem)
  Theme/      Theme.swift (from the skill)
  Screens/    SetupView, TodayView, LedgerRow, DecisionDetailView, ProtocolView,
              AddItemView, SettingsView, StatusStrip
  Actions/    (Job 4) CalendarAct.swift, ShieldAct.swift
  Health/     (Job 3) HealthSync.swift
Fixtures/     today_healthspan.json, today_episodes.json, today_decisions.json,
              protocol_today.json  ← captured from the real backend in sim mode (Job 0)
Tests/        WireTests.swift, ModelsTests.swift, AdherenceStateTests.swift
Screenshots/  written by the design-critic, committed
```

## Wire contract (unchanged, do not "improve")

WebSocket `ws://<host>:<port>/ws/glasses`, JSON, `v: 1`.
Up: `capture` (t, image, device, accel, accel_burst, gps_speed, clock_offset_s, caps),
`echo`, `ping` (every 10 s), `answer`, `hello`.
Down: `speak` (text, urgency), `audio` (mp3 base64), `ask` (question, listen_s,
answer_kind), `echo`.
Job 4 adds down `act` {id, kind, args} and up `act_result` {id, ok, detail}.
Source of truth: `src/longevity/wire.py`. `MacLink.swift` already implements the current
set including reconnect with backoff. Port it, do not rewrite it.

## Screens

### SetupView

Purpose: a person who has never seen the app gets to a working stream without help.
Three sections, each row shows live state and one button.

| Row | State text | Button | Backed by |
|---|---|---|---|
| Glasses | "Not registered" / "Registered" / "Connected" | "Open Meta AI" (deep link `fb-viewapp://`), then "Register" (DAT registration flow) | `GlassesSession` |
| Mac | "Not set" / "Unreachable" / "Reachable · 10.0.0.5:8010" | text field (remembers last value) + "Test" → `GET /health` | `APIClient` |
| Bluetooth · Local network · Microphone · Speech · Motion · Location · Notifications | "Allowed" / "Not yet" / "Denied" | "Allow" (system prompt) or "Open Settings" when denied | one `PermissionRow` |

Footer: "Done" enabled when Glasses ≥ Registered and Mac = Reachable. "Later" always
available; Today then shows the blocking problem in its status strip.
Job 3 adds a Health row. Job 4 adds a Calendar row and, if provisioned, Screen Time.

Copy on first open, one line at the top: "Three things, then it runs itself."

### TodayView

Order, top to bottom:

1. **StatusStrip**: three rows, `eyeglasses` "Glasses connected" / "Glasses off",
   `desktopcomputer` "Backend 10.0.0.5" / "Backend unreachable", `record.circle`
   "Watching 14 min" / "Not watching". If anything blocks watching, the strip collapses
   to that one problem in `Brian.cost` with a fix button ("Retry", "Open Setup").
2. **Primary button**: "Start watching" / "Stop". The one `.glassProminent` control.
   Start = DAT stream start (2 fps) + `MacLink.connect()` + `CapturePacketSender.start`.
   Stop = reverse. The backend opens and closes its own session from the frames
   (`reactive-glasses`), the phone never calls `/api/session/*`.
3. **Hero panel**: `hours_today` as `SignedHours(font: .hero)`, label "healthy‑life hours
   today". Second line: "Score 71 · +0.3 years" (`overall`, `years_delta`). Source:
   `GET /api/healthspan`, polled every 30 s while watching and on foreground.
   A provenance chip sits beside the label: Glasses · WHOOP · Health · Seeded, from the
   payload's `provenance`/`measured` fields. All seeded → chip "Seeded", no other change.
4. **Ledger panel**: "Today" title. Rows = episodes (`GET /api/episodes`) merged with
   decisions (`GET /api/decisions?limit=50`), newest first, deduped on episode id.
   Row: family symbol · time · `label` · outcome chip ("held back" muted, "said", "asked",
   "acted"). Panel footer: "Held back N today" (brian-ui law 4: restraint is visible). Tap → **DecisionDetailView**: interpretation text, actions taken, evidence
   thumbnail from `GET /api/evidence/{decision_id}` when present, "wearer reported" note
   when `reported` is set.
   Empty: "Put the glasses on. Bryan starts counting light, people, and air the moment the
   camera is up." (voice.md's exact string), with the primary button directly above it.
5. Pull to refresh. Nothing else on this screen.

### ProtocolView (Job 2)

List of today's items from `GET /api/protocol/today`. Row: kind symbol (`pills.fill`,
`fork.knife`, `moon.fill`, `figure.walk`) · name · window "8:00–10:00" · status:
"Seen 8:42" with 44 pt thumbnail · "Waiting" · "Missed" · "Done (you)".
Swipe actions: "Undo" (on seen/done) and "Mark done" (on waiting/missed) →
`POST /api/protocol/{id}/undo|done`. Toolbar "+" → **AddItemView**: name, kind, window
start/end, days (weekday toggles) → `POST /api/protocol`. Delete via swipe → `DELETE`.
Empty: "No items yet. Add the first dose window."

### SettingsView

Grouped list. **Mac**: address field + Test. **Voice**: "Speak through the glasses" toggle
(off = local notification only). **Health** (Job 3): "Sync Apple Health" toggle + last
sync time. **Wind‑down** (Job 4): time picker; "Shield apps at wind‑down" toggle with the
app picker, shown only when the entitlement is provisioned, otherwise a muted line "Needs an
Apple Developer account". **Debug** (DEBUG builds only): ingest stats line from
`CapturePacketSender.statusLine`, "Record corpus" toggle, "Say a test line" button
(sends `echo`, expects `speak` back), "Use mock glasses" toggle. **About**: version.

### Whispers and notifications

`speak` → `AVSpeechSynthesizer` on the current route (the glasses when connected; the
`OSStatus -50` rule in `ios/README.md` applies). `audio` → `AVAudioPlayer`. When the
glasses are not connected, or Voice is off, post a `UNUserNotification` with the text
instead. Never both. Never a modal.

### Demo mode

Launch argument `-demo` (or env `BRIAN_DEMO=1`): `APIClient` serves `Fixtures/*.json`,
`GlassesSession` reports Connected, Setup shows all rows green, Today shows Watching and
the hero chip reads "Seeded". The word "demo" never appears on screen.
This is how the simulator gets screenshotted with no hardware. Fixtures are real captures
from the backend in `--source sim`, taken in Job 0.

## Data layer

`APIClient`: `URLSession`, base URL from `UserDefaults` (`backendHost`, `backendPort`),
15 s timeout, decodes with `JSONDecoder` set to ignore unknown keys; every model field the
spec does not name is optional. Models carry only what the screens show:

- `Healthspan { overall: Double, hoursToday: Double, yearsDelta: Double?, day: String, measured: Bool? }`
- `Episode { id, label, startT, endT?, kind, reported? }`
- `Decision { id, t, trigger, interpretation, actions: [String], spoke: Bool, silent: Bool, episodeId? }`
- `ProtocolItem { id, name, kind, windowStart, windowEnd, days, status, seenAt?, evidenceRef? }`

Exact field names come from the fixtures captured in Job 0; the builder matches them and
writes `ModelsTests` that decode every fixture.

`AppState` (one `@Observable`, `@MainActor`): setup state, link status, glasses state,
watching since, today payloads, protocol items, last error. Views never own network code.

## Rules for the builder

- The phone decides nothing about health. It streams, plays, shows, and (Job 4) executes
  an act the backend asked for.
- Never write a frame or thumbnail to disk.
- Keep `MacLink`'s wire behaviour byte-for-byte. Add, don't change.
- Every network failure lands in `AppState.lastError` as a sentence with a fix, shown in
  the StatusStrip, never as an alert.
- Free Personal Team signing must keep working through Job 2. Entitlements that need a
  paid account arrive in Jobs 3–4 behind a preflight.
