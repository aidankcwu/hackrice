You are one iteration of an autonomous build loop. You have no memory of earlier iterations.
Worktree root: the current directory, branch `demo-web`. Do ONE story, then stop.

1. Read docs/DEMO_UI_PRD.md (sections "Analysis tab", "Removed from the app", "Verification",
   "Ownership"), docs/APP_WEB_NOTES.md (the embed contract as built), ralph/demo-web/prd.json,
   and the last 40 lines of ralph/demo-web/progress.txt.
2. Pick the highest-priority story in prd.json with "passes": false. Implement it completely.
3. Verify from phone/: `npm run lint && npx tsc --noEmit && npm test && npm run build`
   (TURBOPACK_ROOT=/ if the symlinked node_modules trips Turbopack; see APP_WEB_NOTES.md).
   Screenshots: start the fixtures server `NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3101` in the
   background, then `node scripts/shot.mjs "<path>?embed=1" phone/screenshots/<story>-<screen>.png`
   with PHONE_URL=http://localhost:3101 (see phone/README.md). Kill the server after.
   Read each PNG you produce and fix what is wrong before committing.
4. Commit in small steps with clear messages; after every commit `git push -u origin demo-web`.
   Never force-push, never rebase, never touch main or demo.
5. Only edit phone/**, docs/APP_WEB_NOTES.md and ralph/demo-web/**. node_modules is a symlink;
   do not delete it. Do not edit ios/, backend/, deploy/, dashboard/, src/.
6. When the story's acceptance criteria all hold, set "passes": true and a one-line "notes" in
   prd.json, append a dated block to ralph/demo-web/progress.txt, commit and push.
7. If every story passes, print exactly <promise>COMPLETE</promise>. Otherwise just end.
Rules: .claude/skills/brian-ios-design/SKILL.md for look and copy; keep the design tokens. Do not
delete routes or components; embed mode hides and unlinks, it does not remove. If blocked, write
it under "BLOCKED" in progress.txt and end without marking the story passed.
