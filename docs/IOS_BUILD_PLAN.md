# Building ios/Brian by Friday (Jobs 1 + 2 of docs/IOS_SPEC.md, hosted)

Read docs/IOS_SPEC.md first; it is the product spec and wins on anything it names.
Then .claude/skills/brian-ios-design/SKILL.md (visual + strings; it wins on look).
This file adds what changed since the spec was written and how the work is split.

## What changed since the spec
- Hosted backend: the "Mac" row is now "Server". A tester pastes ONE URL,
  wss://DOMAIN/t/NAME/ws/glasses?token=TOKEN (the LAN form ws://IP:8010/ws/glasses still
  works). The HTTPS API base is derived from it (https://DOMAIN/t/NAME) and the token is
  sent as X-Access-Token on every request. Test = GET /healthz (not /health).
- Consent: ios/ConsentView.swift exists and must be shown before the first stream; MacLink
  and CapturePacketSender already refuse to run without it. Keep it.
- Sessions: the backend opens and closes its own session from the frames. The phone never
  calls /api/session/*.
- Jobs 3 and 4 (Health, Calendar/Screen Time) are OUT for Friday.
- Deployment target: keep iOS 26.0 from project.yml; the simulators here are 26.5 and 27.0.

## Split (disjoint ownership)
- Coder A -- App + API + Theme: ios/Brian/project.yml (finish it), Sources/App/*
  (BrianApp, RootView, AppState bodies, ServerURL parsing), Sources/API/* (APIClient with
  live + fixture modes, Models coding keys verified against Fixtures/*.json),
  Sources/Theme/*, Tests/ModelsTests.swift, Tests/ServerURLTests.swift.
- Coder B -- Glasses + Link: Sources/Glasses/GlassesSession.swift (DAT, ported from the
  CameraAccess sample: the WearablesViewModel + CameraViewModel code is in
  ios/xcode-project.patch), Sources/Glasses/MockGlasses.swift (MWDATMockDevice, DEBUG),
  Sources/Link/* = ios/MacLink.swift, CapturePacketSender.swift, PhoneSensors.swift,
  QuestionListener.swift, ConsentView.swift, CorpusRecorder.swift copied UNCHANGED, plus the
  glue that feeds frames to CapturePacketSender and audio/speak back out.
- Coder C -- Screens: Sources/Screens/* (SetupView, StatusStrip, TodayView, LedgerRow,
  DecisionDetailView, ProtocolView, AddItemView, SettingsView) written ONLY against
  AppState + Models + Theme. No network code in views.
The contract files already exist: Sources/API/Models.swift, Sources/Glasses/GlassesSessioning.swift,
Sources/App/AppState.swift (surface). Do not rename anything in them; add if you must and say so.

## Build + verify
- Generate: cd ios/Brian && xcodegen generate   (Coder A owns project.yml and runs this;
  B and C type-check their own files with swiftc against the iOS SDK and, once
  Brian.xcodeproj exists, may run xcodebuild).
- Build: xcodebuild -project ios/Brian/Brian.xcodeproj -scheme Brian
  -destination 'platform=iOS Simulator,name=iPhone 17' -configuration Debug build
- Run in demo mode on the simulator with launch argument -demo: Setup all green, Today
  shows Watching with the "Seeded" chip and the ledger from the fixtures.
- The supervisor integrates, runs the simulator, and screenshots each screen.

## Status, 23 Sept

Built, tested, and run in `-demo` on the iPhone 17 simulator (iOS 26.5 and 27.0).
Every screen was screenshotted. 20 unit tests pass. Not yet done: a real phone with
real glasses (DAT cannot run on the simulator), signing under the Refleo team, TestFlight.

What differs from the split above, so nobody re-derives it:
- Swift 5 language mode (`SWIFT_VERSION: 5.0`). Swift 6 mode rejects the unchanged
  `QuestionListener.swift`; `SWIFT_STRICT_CONCURRENCY: minimal` only applies in Swift 5.
- `Healthspan.measured` and `provenance` are objects on the wire, not scalars; a custom
  decoder projects them to `measuredCount`/`measuredTotal` and a one-word chip.
  `seenAt` maps from `seen_t`. Protocol kinds are `dose | meal | winddown | walk`;
  undo yields `undone`.
- `Link` (Sources/Link/LinkGlue.swift) is `@Observable`. The five copied Link files are
  byte-identical to `ios/*.swift`; `MacLink.swift` carries one additive whisper-routing
  hook (glasses off or "Speak through the glasses" off routes a whisper to a local
  notification instead of playback, never both).
- `AppState.consentGiven` reads and writes `StreamingConsent` (`streamingConsent.v2`),
  not a separate flag. Corpus recording is off by default and only runs when the
  Settings toggle is on.
- Ledger rule: a chip appears only for `said` (spoke or handed off), `asked` (pending
  ask), `acted` (an `act` action), or `held back` (a proposed ask/speak that was
  suppressed). Rows with only annotate/log/watch actions and no episode are dropped.
- The scheme's `-demo` argument is present but unticked; pass it from simctl or tick it.
- Two `Brian-*` folders under DerivedData means a stale scratch build is around; the real
  one is the newest. Delete the other before `simctl install` or you screenshot old code.

Build and run:
    cd ios/Brian && xcodegen generate
    xcodebuild -project Brian.xcodeproj -scheme Brian \
      -destination 'platform=iOS Simulator,name=iPhone 17' -configuration Debug build
    xcodebuild ... test
    xcrun simctl install <udid> <DerivedData>/Build/Products/Debug-iphonesimulator/Brian.app
    xcrun simctl launch <udid> com.zeroist.app -demo
