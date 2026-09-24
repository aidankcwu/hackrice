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

ADDED 15:20: main moves while you work (a Brian -> Bryan spelling pass and Stage B config
landed in ios/Brian). At the START of every iteration run `git fetch -q origin && git merge
--no-edit origin/main`. If it conflicts, resolve keeping both intents (the spelling "Bryan"
wins in strings and comments; your feature code wins in logic), make sure it builds, commit
the merge, push, then continue with the story.

ORCHESTRATOR REVIEW 15:25 (fold into N-006, or earlier if you touch ConnectView anyway):
- "Use the link on your clipboard" reads as plain text; make it an obvious button (.glass)
  with a clipboard symbol.
- "Start watching" must be disabled (dimmed) until the invite link is reachable and the
  glasses are at least registered; a tap while disabled is impossible, so the rows above
  already say what is missing.
- docs/APP_WEB_NOTES.md is now on origin/main (merged): use its URL forms in N-004.

ORCHESTRATOR REVIEW 15:30 (must be fixed in N-006 if not before): in N-004-calendar.png and
N-004-analysis.png the web content runs underneath the native tab bar (the last rows of the
page are hidden behind it). The WKWebView must respect the tab bar: keep its bottom edge above
the tab bar, or add the tab bar height plus the bottom safe area as a bottom content inset,
so the page's last row is fully visible when scrolled to the end. Re-take both screenshots.
