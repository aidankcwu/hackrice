You are one iteration of an autonomous build loop. You have no memory of earlier iterations.
Worktree root: the current directory (branch `app/web`). Do ONE story, then stop.

1. Read docs/APP_PRD.md (the product and the native/web contract), ralph/web/prd.json, and the
   last 40 lines of ralph/web/progress.txt.
2. Pick the highest-priority story in prd.json with "passes": false. Implement it completely.
3. Verify exactly as docs/APP_PRD.md "Verification" says for web: from phone/ run
   `npm run lint && npx tsc --noEmit && npm test && npm run build`. Screenshots: start the
   fixtures server `NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100` in the background, then
   `node scripts/shot.mjs "<path>?embed=1..." phone/screenshots/<story>-<screen>.png`
   (see phone/README.md; PHONE_URL default is the fixtures server). Kill the server after.
   Look at each screenshot you produce (Read the PNG) and fix what is wrong before committing.
4. Commit in small steps with clear messages (one story or less per commit); after every commit
   run `git push -u origin app/web`. Never force-push, never rebase, never touch main.
5. Only edit phone/** , docs/APP_WEB_NOTES.md and ralph/web/**. node_modules is a symlink; do not
   delete it. Do not edit ios/, backend/, deploy/, dashboard/, src/.
6. When the story's acceptance criteria all hold, set its "passes": true and a one-line "notes"
   in prd.json, append a dated block to ralph/web/progress.txt (what you did, what the next
   iteration must know, any decision), commit and push.
7. If every story passes, print exactly <promise>COMPLETE</promise>. Otherwise just end.
Rules: follow .claude/skills/brian-ios-design/SKILL.md for look and copy. Keep the design
tokens. If you hit a blocker you cannot fix inside phone/, write it in progress.txt under
"BLOCKED" and end the iteration without marking the story passed.
