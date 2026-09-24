You are one iteration of an autonomous build loop. You have no memory of earlier iterations.
Worktree root: the current directory (branch `app/native`). Do ONE story, then stop.

1. Read docs/APP_PRD.md (product, Connect screen, status pill, web tabs, and the native/web
   contract), docs/IOS_SPEC.md and docs/IOS_BUILD_PLAN.md (what exists and why),
   .claude/skills/brian-ios-design/SKILL.md (look and copy; it wins), ralph/native/prd.json,
   and the last 40 lines of ralph/native/progress.txt. If docs/APP_WEB_NOTES.md exists on
   origin/app/web (`git show origin/app/web:docs/APP_WEB_NOTES.md`), read it too.
2. Pick the highest-priority story in prd.json with "passes": false. Implement it completely
   in ios/Brian/**. Views read only AppState + Models + Theme; no network code in views.
3. Build and verify. xcodegen is at ~/.local/bin (on PATH). From ios/Brian:
     xcodegen generate
     DEV=$(xcrun simctl list devices available | grep -E 'iPhone 1[5-7]' | head -1 | sed -E 's/^ *(.*) \(([0-9A-F-]{36})\).*/\2/')
     xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" -configuration Debug build -quiet
     xcodebuild -project Brian.xcodeproj -scheme Brian -destination "id=$DEV" test -quiet
   Screenshots in demo mode: `xcrun simctl boot $DEV` (ignore "already booted"), install the
   built Zeroist.app from DerivedData (`xcodebuild -showBuildSettings` gives BUILT_PRODUCTS_DIR),
   `xcrun simctl launch $DEV com.zeroist.app -demo -screen <id>`, wait 3 s, then
   `xcrun simctl io $DEV screenshot ios/Brian/Screenshots/<story>-<screen>.png`.
   Story N-002 adds the `-screen <id>` launch argument (ids: today, calendar, analysis,
   protocol, connect, settings) so every later screenshot is automatable; until it exists,
   use -showSetup and the default tab.
   Read every PNG you produce and fix what looks wrong before committing.
   If `xcrun simctl list devices available | grep -q iPhone` finds nothing, the runtime is
   still installing: do the non-visual part of the story, build with
   `-destination 'generic/platform=iOS Simulator'` if that works, commit, append progress,
   do NOT mark the story passed, and print exactly <promise>WAITING_SIMULATOR</promise>.
4. Commit in small steps with clear messages; after every commit `git push -u origin app/native`.
   Never force-push, never rebase, never touch main.
5. Only edit ios/Brian/**, docs/APP_NATIVE_NOTES.md and ralph/native/**. Do not edit the
   files under ios/Brian/Sources/Link except by ADDING observables or hooks, and say so in a
   comment at the top of the file. Do not edit ios/*.swift, phone/, backend/, deploy/. You may
   RUN the phone fixtures server for the web-tab screenshots:
   `cd phone && NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100` (node_modules is a symlink).
6. When every acceptance criterion holds, set "passes": true and a one-line "notes" in
   prd.json, append a dated block to ralph/native/progress.txt (what you did, what the next
   iteration must know), commit and push.
7. If every story passes, print exactly <promise>COMPLETE</promise>. Otherwise just end.
If blocked by something outside ios/Brian, write it under "BLOCKED" in progress.txt and end
without marking the story passed.
