# PLAN.md — Brian iOS app on the existing pipeline

Thesis: Bryan got healthy by removing decisions, not by adding data. The app's unit of
work is a decision the wearer no longer makes. Detect the moment → act, or one whisper →
verify → show it in the ledger.

What exists (read `docs/STATE.md` after Job 0 for file:line detail): glasses → iPhone
(Meta DAT) → WebSocket → Mac backend (`backend/pipeline`, `src/longevity`): Gemini tags
each frame, a gate fires triggers, episodes, a reasoner, ElevenLabs, audio back to the
glasses. 40 routes, healthspan score, persona, conversation agent, wearables ingest.
The iOS side is a patched copy of Meta's sample living outside the repo. That is what
this plan replaces.

## 0. Orchestrator contract (read at the start of every session)

You are the ORCHESTRATOR. You run on Opus with effort high. Rules:

1. You never open source files and never edit code. You dispatch the subagents in
   `.claude/agents/` and read their reports. The only files you touch are `PLAN.md`
   (ticking boxes) and git.
2. Branch is `brian-ios` (created by `setup.sh` from `origin/reactive-glasses`). Every
   session starts with `git status` and `git log --oneline -3` to confirm.
3. One task per subagent call. The dispatch prompt is exactly:
   ```
   Task <id> from PLAN.md. Read only: PLAN.md section "<id>", docs/IOS_SPEC.md
   (if ios), docs/STATE.md, and the files the task names. Do the task. Run the
   acceptance command. Report in the format your agent file requires.
   ```
   Do not paste the task text, do not summarise the codebase, do not add advice.
4. Reports are ≤ 15 lines. If a report is longer, read the first 15 lines only.
5. Subagents never commit. When a task's acceptance passes you commit:
   `git add -A && git commit -m "task <id>: <one line>"`. Then tick the box here.
6. A task that fails acceptance gets one retry with the failure's last 10 log lines
   added to the prompt. A second failure is a STOP: write what failed under the task
   and tell the human. Never work around a failure yourself.
7. Tasks marked [P] may run in parallel only with each other and only when their file
   lists are disjoint. Everything else is sequential.
8. At the end of each Job: run `verifier`, commit, `git tag job<N>-done`, then tell the
   human: "Job N done. Run the STOP gate. Then `/clear` and send: Start Job N+1."
   Never continue into the next Job in the same session.
9. STOP gates need a human. Do not skip one because it looks fine.
10. Effort: scout and verifier low, backend-builder medium, ios-builder high,
    design-critic medium. Set in the agent files; do not override.

## Effort and time (what this costs)

| Job | Agents | Claude Code wall time | Human time | Human does |
|---|---|---|---|---|
| 0 Recon | scout | 20–40 min | 15 min | read STATE.md, approve |
| 1 iOS foundation | ios-builder ×7, design-critic, verifier | 3–5 h | 1–2 h | sign in Xcode, run on device with glasses |
| 2 Dose by sight | backend-builder ×3, ios-builder ×2, design-critic | 2–4 h | 1 h | vial test on device |
| 3 Apple Health | ios-builder ×2, backend-builder ×1 | 1–2 h | 30 min | grant Health access, check dashboard chips |
| 4 Autopilot | backend-builder ×2, ios-builder ×3 | 2–4 h | 1 h | calendar + shield test |
| 5 Ship | verifier, design-critic, scout | 1 h | 1 h | run the Bryan demo end to end |

Token discipline that keeps this affordable: orchestrator reads nothing, one job per
session, `/clear` between jobs, logs go to files, reports are short.

## Job 0 — Recon (scout)

- [x] 0.1 Write `docs/STATE.md` (≤ 100 lines, every fact with `path:line`):
  1. Backend run command that needs **no API keys** (look at `backend/scripts/preflight.py --offline`, `backend/pipeline/main.py` flags, `--source sim`). Confirm it starts and `curl localhost:8010/health` answers. Record the exact command.
  2. Test commands for the root `tests/` and `backend/tests/` (root `pytest.ini`, `backend/pyproject.toml`), and the dashboard test/build command. Run them; record pass/fail counts and failing names only.
  3. The wire contract: every message type in `src/longevity/wire.py` with its fields, up and down.
  4. Exact JSON shapes the phone will render: with the backend running in sim mode, save `curl` output to `ios/Brian/Fixtures/today_healthspan.json` (`/api/healthspan`), `today_episodes.json` (`/api/episodes`), `today_decisions.json` (`/api/decisions?limit=50`), `status.json` (`/api/status`). Let sim run ≥ 3 minutes first so the ledger is not empty. List the top-level keys of each in STATE.md.
  5. Wearables ingest: the request schema `POST /api/wearables/ingest` accepts (`docs/WEARABLES.md`, `backend/pipeline/wearables/adapters.py`, `backend/pipeline/api/routes.py`). One example body.
  6. Where a trigger is defined and registered (`backend/pipeline/gate/triggers.py`), using `caffeine_seen` as the worked example: the fields it reads, the debounce, the episode it opens, the test that covers it.
  7. Evidence: how a decision's evidence frames are saved and served (`/api/evidence/{decision_id}`), file:line.
  8. The speak path a new backend feature should call to say one line through the glasses (`backend/pipeline/capture/speak.py`, rate limiter), file:line.
  9. `db.py`: how a table is declared and migrated (one existing table as the example).
  10. What is stale in the old `CLAUDE.md` (now at `docs/CLAUDE_OLD.md`): list claims that are no longer true on this branch (e.g. "nothing reconnects").
  Acceptance: `docs/STATE.md` exists with all 10 sections; the four fixture files exist and are valid JSON (`python3 -m json.tool`).
- [x] 0.2 (scout) `docs/CLAUDE_OLD.md` = the pre-bundle CLAUDE.md from git (`git show origin/reactive-glasses:CLAUDE.md > docs/CLAUDE_OLD.md`). Hardware gotchas from it that are still true go into a "Hardware gotchas" section at the end of `docs/STATE.md`.
  Acceptance: file exists; STATE.md has the section.
- STOP gate 0: human reads STATE.md (10 minutes) and confirms the no-key run command works on their Mac.

## Job 1 — iOS foundation (ios-builder unless noted)

Skills to read before starting each task are named in the task. `brian-ios-design` is
preloaded. Meta's DAT skills are in `.claude/skills/` (getting-started, camera-streaming,
session-lifecycle, permissions-registration, mockdevice-testing, dat-conventions,
debugging, sample-app-guide). Do not guess the SDK; read them or use the
`meta-wearables-docs` MCP.

- [ ] 1.1 Project. `ios/Brian/project.yml` is a starter. Finish it: verify the DAT package tag (`git ls-remote --tags https://github.com/facebook/meta-wearables-dat-ios`), fill `bundleIdPrefix` from `docs/STATE.md` if the human set one there, otherwise leave `edu.rice.brian`. Create `Sources/App/BrianApp.swift` + `RootView.swift` (empty TabView with two tabs and a Settings toolbar button), `Sources/Theme/Theme.swift` (verbatim from the skill), `Tests/` with one passing test. Run `xcodegen generate` in `ios/Brian`.
  Acceptance: `cd ios/Brian && xcodegen generate && xcodebuild -scheme Brian -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO > ../../.claude/logs/1.1.log 2>&1` exits 0. Record the exact build and test commands in `docs/STATE.md` under "iOS build".
- [ ] 1.2 Link layer. `git mv` the five Swift files from `ios/` into `ios/Brian/Sources/Link/`. Add them to the target (XcodeGen picks up the folder). Fix only what the compiler demands under the project's Swift version; no behaviour changes. `Tests/WireTests.swift`: encode a capture packet and decode `speak`, `audio`, `ask` messages against literal JSON copied from `src/longevity/wire.py`.
  Skills: swiftui-expert-skill (concurrency section) if `@Observable`/actor errors appear.
  Acceptance: build green; `xcodebuild test -scheme Brian -destination 'platform=iOS Simulator,name=<newest iPhone from xcrun simctl list>'` green.
- [ ] 1.3 Glasses session. Clone Meta's sample to a scratch dir outside the repo (`git clone --depth 1 https://github.com/facebook/meta-wearables-dat-ios /tmp/mwdat`), port `samples/CameraAccess/CameraAccess/ViewModels/WearablesViewModel.swift` and the streaming parts of `CameraViewModel.swift` into `Sources/Glasses/GlassesSession.swift` (one `@Observable` exposing registration state, availability, `startStream(fps: 2)`, `stopStream()`, a frame callback). Keep the sample's licence header. Add `MockGlasses.swift` (MockDeviceKit) under `#if DEBUG`. `Wearables.configure()` at launch. Info.plist keys per the `getting-started` skill are already in `project.yml`; verify.
  Skills: getting-started, session-lifecycle, camera-streaming, mockdevice-testing.
  Acceptance: build green; in the simulator with `-demo`, `GlassesSession` reports Connected via the mock; the frame callback fires (log line count > 0 in `.claude/logs/1.3.log`).
- [ ] 1.4 API + models + demo mode. `Sources/API/APIClient.swift`, `Models.swift` per `docs/IOS_SPEC.md`, field names taken from `ios/Brian/Fixtures/*.json`. `-demo` launch argument serves fixtures. `Tests/ModelsTests.swift` decodes every fixture.
  Acceptance: tests green; a test asserts `hoursToday` and `overall` decode to non-nil from the fixture.
- [ ] 1.5 Setup screen. `Sources/Screens/SetupView.swift` + `PermissionRow.swift` per the spec table. `AppState` holds setup state. Deep link `fb-viewapp://` via `LSApplicationQueriesSchemes` (already in plist). "Test" hits `/health`.
  Skills: ux-writing, sf-symbols.
  Acceptance: build green; simulator `-demo` shows all rows green; without `-demo` the Mac row shows "Not set" and Test against a wrong address shows "Unreachable" with the fix sentence (screenshot both to `ios/Brian/Screenshots/setup-demo.png`, `setup-unreachable.png` via `xcrun simctl io booted screenshot`).
- [ ] 1.6 Today screen. `StatusStrip`, primary button wiring (`GlassesSession` + `MacLink` + `CapturePacketSender` per `ios/INTEGRATION.md` section 3, which is now `ios/Brian/Sources/Link`), hero panel, ledger with `DecisionDetailView`, 30 s polling, pull to refresh, empty state.
  Skills: swiftui-liquid-glass (primary button, tab bar), swiftui-design-skill.
  Acceptance: build + tests green; screenshots `today-demo-light.png`, `today-demo-dark.png` (`xcrun simctl ui booted appearance dark`), `today-empty.png`, `today-xxxl.png` (`xcrun simctl ui booted content_size accessibility-extra-extra-extra-large`).
- [ ] 1.7 Settings + notifications. `SettingsView` per spec (Mac, Voice, Debug, About). `speak`/`audio` routing: glasses connected and Voice on → speak; otherwise `UNUserNotificationCenter` local notification. Notifications permission row in Setup.
  Acceptance: build + tests green; in `-demo`, "Say a test line" produces a notification in the simulator (screenshot `settings-debug.png`).
- [ ] 1.8 (design-critic) Grade every screenshot in `ios/Brian/Screenshots/` with the rubric in `brian-ios-design`. Report per screenshot: score /20 and the fixes, most damaging first.
  Then (orchestrator): dispatch ios-builder with the fix list as task 1.9; re-run 1.8. Maximum two rounds. Ships at ≥ 16/20 per screen, no zeros.
- [ ] 1.10 (verifier) full run.
- STOP gate 1 (human, on the real device): open `ios/Brian/Brian.xcodeproj`, set Team under Signing & Capabilities, run on the iPhone. Setup → Register → Test → Done. Start watching with the glasses on. On the Mac: `curl localhost:8010/ingest/stats` shows `received` climbing and `malformed 0`. Hold a coffee cup in view: a ledger row appears within a minute. Confirm a `speak` plays in the glasses (Settings → Debug → Say a test line, with the backend's echo-to-speak, or wait for a spoken decision).

## Job 2 — Dose by sight (the feature Bryan asked for on the call)

- [x] 2.1 (backend-builder) Protocol table and routes. `ProtocolItem {id, name, kind: dose|meal|winddown|walk, window_start "HH:MM", window_end, days [0-6], created_t}` in `backend/pipeline/db.py` following the pattern STATE.md §9 names. `ProtocolStatus` per item per day: `waiting|seen|done|missed|undone`, `seen_t`, `evidence_ref`. Routes in `backend/pipeline/api/routes.py`: `GET /api/protocol`, `POST /api/protocol`, `PUT /api/protocol/{id}`, `DELETE /api/protocol/{id}`, `GET /api/protocol/today` (items + today's status), `POST /api/protocol/{id}/done`, `POST /api/protocol/{id}/undo`, `GET /api/protocol/export.csv?days=14`. Seed on first run: "Morning dose" 07:00–10:00, "Evening dose" 19:00–22:00, "Lunch window" 11:30–14:00, "Wind‑down" 21:30–23:00, "Daylight walk" 07:00–16:00. Document in `docs/API.md`.
  Acceptance: `backend/tests/test_protocol.py` covers create/list/today/done/undo/csv; all pre-existing tests still green.
  Conventions fixed by 2.1 (2.2 and 2.3 must follow): `days` uses 0 = Monday … 6 = Sunday (Python `weekday()`; Swift `Calendar` weekday is 1 = Sunday, so convert on the phone). `evidence_ref` = `<decision_id>/<frame_ref>`; a thumbnail is `GET /api/evidence/<evidence_ref>`.
- [x] 2.2 (backend-builder) `medication_seen` trigger in `backend/pipeline/gate/triggers.py`, modelled on `caffeine_seen` (STATE.md §6): fires on 2 ticks within 10 s where `medication_visible` is true OR `in_hand` contains any of `vial, pen, syringe, injector, pill, capsule, tablet, bottle`; cooldown 20 min; opens episode `medication_sighting` with evidence saved through the existing evidence path (STATE.md §7). Adherence matcher (new `backend/pipeline/protocol/adherence.py`): a `medication_sighting` inside an open `dose` window marks that item `seen` with `evidence_ref`; a `dose` window that closes with no sighting marks `missed` and calls the speak path (STATE.md §8) once with: "Your <name> window just closed. Take it now, or mark it skipped in Brian." Meal/walk/winddown kinds only get `seen` from their existing triggers (`food_in_frame`, `outdoor_sustained`, `screen_sustained` after wind-down start) — no new whispers for them in this job.
  Acceptance: `backend/tests/test_adherence.py`: sighting in window → seen with evidence; sighting outside any window → episode only, no status change; window close without sighting → missed and exactly one speak call; a second close event does not speak again. Gate tests still green.
- [ ] 2.3 [P] (ios-builder) `ProtocolView`, `AddItemView`, swipe Undo / Mark done, thumbnails from `/api/evidence/...`, per `docs/IOS_SPEC.md`. `Fixtures/protocol_today.json` captured from the running backend after 2.1. `Tests/AdherenceStateTests.swift` for status → row rendering.
  Skills: ux-writing, sf-symbols.
  Acceptance: build + tests green; screenshots `protocol-demo-light.png`, `protocol-demo-dark.png`, `protocol-empty.png`, `additem.png`.
- [ ] 2.4 [P] (backend-builder) Dashboard adherence panel in `dashboard/src` using the existing Bryan design system (`design-system/brian/MASTER.md`): per item, 14-day grid of seen / done / missed / waiting, plus "Export CSV" linking to `/api/protocol/export.csv`. Follow the existing panel components; no new tokens.
  Acceptance: dashboard test/build command from STATE.md green; a vitest renders the grid from a fixture with 3 days of statuses.
- [ ] 2.5 (design-critic) grade Job 2 screenshots; one fix round via ios-builder.
- [ ] 2.6 (verifier) full run.
- STOP gate 2 (human, real device): add a dose window covering now. Hold a vial or a pen in view for 5 s. Protocol row flips to "Seen" with a thumbnail, no tap. Undo works. Let a 5-minute test window close without a sighting: one whisper, row reads "Missed". Dashboard grid shows both.

## Job 3 — Apple Health as a source (kills the Fitbit dependency)

- [ ] 3.1 (ios-builder) Preflight: add `com.apple.developer.healthkit` to `project.yml` entitlements, `xcodegen generate`, build with signing. If the human's team cannot provision it (log says so), STOP and report; the fix is the paid Apple Developer Program, not code.
- [ ] 3.2 (ios-builder) `Sources/Health/HealthSync.swift`: read last night's sleep (asleep stages total), resting HR, HRV (SDNN), today's steps; `HKObserverQuery` + background delivery; POST to `/api/wearables/ingest` in the body shape from STATE.md §5 with `source: "healthkit"`. Settings toggle + last sync line. Health row in Setup.
  Skills: healthkit, background-processing.
  Acceptance: build + tests green; a unit test builds the ingest body from sample values and matches the schema; on device (human) the dashboard's wearable chips show a `healthkit` source.
- [ ] 3.3 (backend-builder) If the ingest adapter does not already accept `source: "healthkit"`, add it in `backend/pipeline/wearables/adapters.py` mirroring the Health Auto Export path; tests.
- [ ] 3.4 (verifier) full run.
- STOP gate 3: human grants Health access on device; dashboard shows sleep/HRV from the phone within one sync.

## Job 4 — Autopilot: the system acts instead of nagging

- [ ] 4.1 (backend-builder) `act` action. In `src/longevity/wire.py` add down message `act {v, type:"act", id, kind, args}` and up `act_result {id, ok, detail}`. In `backend/pipeline/actions/handlers.py` add handler `act` that sends over the ingest socket (same path `speak` uses) and records the decision as `acted` when `act_result.ok` arrives, `act_failed` otherwise (then the existing speak path says one line). Two rule-based producers in a new `backend/pipeline/actions/autopilot.py`, checked on the tick clock: (a) at 16:00 local, if today's outdoor minutes (existing outdoor episodes) < `OUTDOOR_TARGET_MIN` (config, default 30) → `act calendar_block {minutes: 20, earliest: now, latest: sunset-30m}`; (b) at `WIND_DOWN_HHMM` (config, default 21:30) → `act screen_shield {until: "07:00"}`. Config in `backend/pipeline/config.py` env vars. Persona rules may veto (reuse how persona gates speak, STATE.md §8).
  Acceptance: `backend/tests/test_autopilot.py`: both producers fire once per day under a fake clock; `act_result` flips the decision; failure speaks exactly once.
- [ ] 4.2 (ios-builder) `Sources/Actions/CalendarAct.swift`: EventKit write-only access (`NSCalendarsWriteOnlyAccessUsageDescription`), finds the next free 20 min between `earliest` and `latest` on the default calendar, inserts "Walk outside (Brian)", replies `act_result`. Calendar row in Setup. `MacLink` routes `act` messages to an `ActRunner`.
  Skills: eventkit.
  Acceptance: build + tests green; test for slot finding against a fixture calendar; on device (human) an event appears.
- [ ] 4.3 (ios-builder) `Sources/Actions/ShieldAct.swift`: FamilyControls authorisation, `FamilyActivityPicker` in Settings to choose apps, `ManagedSettingsStore().shield.applications` from wind-down until `until`, cleared by a `DeviceActivitySchedule`. Preflight exactly like 3.1: the Family Controls (Development) capability does not provision on a free Personal Team; if signing fails, ship the job without it and leave the Settings line "Needs an Apple Developer account". Confirmed limitation, do not fight it.
  Acceptance: build green with the capability when provisioned; unit test for schedule bounds; on device (human) the chosen apps show the shield at wind-down.
- [ ] 4.4 (ios-builder) Ledger rows show `acted` with `checkmark.seal.fill` and the act detail in `DecisionDetailView`. Screenshots `today-acted.png`.
- [ ] 4.5 (design-critic) grade; one fix round. (verifier) full run.
- STOP gate 4: set wind-down to five minutes from now and outdoor target to 999; calendar event appears, shield engages (if provisioned), both rows read "acted" on the phone and the dashboard.

## Job 5 — Ship

- [ ] 5.1 (verifier) full run, every suite.
- [ ] 5.2 (design-critic) final pass over every screenshot, light and dark; report the single weakest screen.
- [ ] 5.3 (scout) `docs/DEMO_BRYAN.md`: a 3-minute script for a call with Bryan's team. Pair → Start watching → coffee cup → ledger row → vial in window → auto-logged with thumbnail → wind-down shield → adherence grid + CSV. Exact taps and the expected line on screen at each step. Plus a "what is real vs seeded" paragraph in the style of `docs/DEMO_SCRIPT.md`.
- [ ] 5.4 (orchestrator) `git tag v0.1-brian-ios`; open a PR from `brian-ios` into `reactive-glasses` with `gh pr create` using the job list above as the body. Do not merge.
- STOP gate 5: human runs DEMO_BRYAN.md end to end on device, then merges.

## Out of scope (do not build, do not propose)

Chat UI. Manual logging beyond Protocol undo/done. In-app dashboard. Frame or video
storage on the phone. New design tokens. Any trigger beyond `medication_seen`. Changes to
the healthspan engine. Cloud hosting. Android.

## Token rules (every agent, every task)

- Read only the files the task names. Grep before Read. No codebase tours.
- Never paste logs into a report. `> .claude/logs/<task>.log 2>&1`, then read the tail.
- Run the acceptance command yourself before reporting DONE.
- Report: `DONE` or `BLOCKED`; files touched; acceptance output (≤ 5 lines); one open
  question at most. Nothing else.
