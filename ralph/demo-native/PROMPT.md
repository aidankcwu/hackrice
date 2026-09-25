You are one iteration of an autonomous build loop. You have no memory of earlier iterations.
Repo root: the current directory, branch `demo`. Do ONE story, then stop.

0. `git fetch -q origin && git merge --no-edit origin/demo` (the orchestrator and a web loop
   push to demo too). Resolve any conflict keeping both intents, make sure it builds, commit.
1. Read docs/DEMO_UI_PRD.md (the spec; it wins), docs/REDESIGN_NOTES.md (why),
   docs/APP_NATIVE_NOTES.md (how the current app is built, demo mode, screenshots),
   .claude/skills/brian-ios-design/SKILL.md (look and copy), ralph/demo-native/prd.json, and
   the last 60 lines of ralph/demo-native/progress.txt. Skim ios/Brian/Sources so you reuse
   what exists (Theme, LedgerRow, ConnectRows, ConnectionStatus, WebScreen, AppState, APIClient).
2. Pick the highest-priority story in prd.json with "passes": false. Implement it completely in
   ios/Brian/**. Views read only AppState + Models + Theme; no network code in views. Every pure
   derivation (stats, coalescing, header state, sessions) is a value type with Swift Testing tests.
3. Build and verify, from ios/Brian (xcodegen is at ~/.local/bin, on PATH):
     xcodegen generate
     DEV=6C6A1567-D1EB-41FB-AF32-5FD183C5E60C     # iPhone 17 Pro, iOS 26.5
     xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" -configuration Debug build -quiet
     xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" test -quiet
   Screenshots: `xcrun simctl boot $DEV` (ignore "already booted"), `xcrun simctl ui $DEV appearance light`,
   install the built Zeroist.app from BUILT_PRODUCTS_DIR (`xcodebuild ... -showBuildSettings`),
   `xcrun simctl launch $DEV com.zeroist.app -demo -screen <id>` (ids: home, analysis, protocol,
   connect, settings, preview; plus -empty, -glassesOff, -fresh as the PRD says), wait 3 s, then
   `xcrun simctl io $DEV screenshot ios/Brian/Screenshots/<story>-<screen>.png`. For Home also
   take one at `xcrun simctl ui $DEV content_size accessibility-extra-extra-large` and reset to
   `content_size large` afterwards. Analysis needs the phone fixtures server: see
   docs/APP_NATIVE_NOTES.md "Screenshots" (scratch copy, port 3100, `-webBase http://Aidan-mini.local:3100`,
   serve with `-H 0.0.0.0`); skip it if the story does not touch Analysis.
   Read every PNG you produce and fix what looks wrong before committing. Check the skill's
   banned list against each screenshot.
4. Commit in small steps with clear messages; after every commit `git push origin demo`.
   If the push is rejected, `git fetch && git merge --no-edit origin/demo`, rebuild, push again.
   Never force-push, never rebase, never touch main.
5. Only edit ios/Brian/**, docs/APP_NATIVE_NOTES.md and ralph/demo-native/**. In
   ios/Brian/Sources/Link only ADD observables or hooks, and say so in a comment at the top.
   Do not edit phone/, backend/, deploy/, src/, ios/*.swift.
6. When every acceptance criterion holds, set "passes": true and a one-line "notes" in prd.json,
   append a dated block to ralph/demo-native/progress.txt (what you did, what the next iteration
   must know, decisions), commit and push.
7. If every story passes, print exactly <promise>COMPLETE</promise>. Otherwise just end.
If blocked by something outside ios/Brian, write it under "BLOCKED" in progress.txt with the
exact ask, and end without marking the story passed.

ORCHESTRATOR REVIEW 20:55 (fold into D-010, or earlier if you touch the header): in
D-001..D-004 screenshots the Start/Stop button is a full-width black bar under the header.
Aidan asked for it *in* the header. Make it a compact .glassProminent capsule sized to its
label ("Stop" / "Start watching"), centered in its bar, not full width; keep it visible while
scrolling. If the top bar has room at 402 pt when the pill is short (e.g. "Glasses off"), it
may sit trailing beside Preview; otherwise the second bar stays but compact. Also check
.glassProminent labels in dark mode on ConnectView and AddItemView (D-004 found white-on-white).
