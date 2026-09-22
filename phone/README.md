# phone

The Brian phone app as a mobile web app (PLAN.md Job F). Spec: `docs/IOS_SPEC.md`.
Look and copy: `.claude/skills/brian-ios-design/SKILL.md`.

```bash
npm run dev                                   # against the backend
NEXT_PUBLIC_FIXTURES=1 npm run dev            # fixtures mode, no backend
npm run lint && npx tsc --noEmit && npm run build
```

Environment: `NEXT_PUBLIC_API_BASE` (default `http://localhost:8010`), `NEXT_PUBLIC_API_TOKEN`
(sent as a bearer token when set), `NEXT_PUBLIC_FIXTURES=1`.

Fixtures mode serves `fixtures/*.json` (copies of `ios/Brian/Fixtures/`). Screenshot URLs,
`/?screen=<name>&mode=<light|dark>&scale=<1|2>`, read `fixtures/design/*.json` instead.
`node scripts/shot.mjs "/?screen=today&mode=light" <out.png>` takes a true 390 x 844 screenshot with
headless Chrome; a path is resolved against `PHONE_URL` (default `http://localhost:3100`, the fixtures
dev server: `NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100`).

On the iPhone: `npm run dev -- -H 0.0.0.0`, open `http://<this PC's Wi-Fi IP>:3000` in Safari,
Share → Add to Home Screen.
