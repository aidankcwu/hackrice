# phone

The Bryan phone app as a mobile web app (docs/PLAN.md Job F). Spec: `docs/IOS_SPEC.md`.
Look and copy: `.claude/skills/brian-ios-design/SKILL.md`.

```bash
npm run dev                                   # against the backend
NEXT_PUBLIC_FIXTURES=1 npm run dev            # fixtures mode, no backend
npm run lint && npx tsc --noEmit && npm run build
```

Environment: `NEXT_PUBLIC_API_BASE` (default: derived at runtime, see below; `http://localhost:8010`
in local dev), `NEXT_PUBLIC_API_TOKEN` (dev only: the bearer token when no `?token=` was opened),
`NEXT_PUBLIC_FIXTURES=1`.

Hosted (deploy/README.md): one container per tester, built with `NEXT_BASE_PATH=/t/NAME/app`
(`phone/Dockerfile`, standalone output), no build-time secrets. The VC opens
`https://DOMAIN/t/NAME/app/?token=TOKEN`; `src/lib/runtime.ts` derives the backend from the page URL
(`https://DOMAIN/t/NAME`), keeps the token in localStorage per `/t/NAME`, scrubs it from the address
bar and sends `Authorization: Bearer` on every request (`?token=` on `backendImageUrl` image URLs).
`src/proxy.ts` gates the pages when `ACCESS_TOKEN` is set (401 without the token; cookie after the
first visit); `/healthz` is open.

```bash
docker build -t hackrice-phone:alice --build-arg NEXT_BASE_PATH=/t/alice/app phone/
docker run -p 3000:3000 -e ACCESS_TOKEN=... hackrice-phone:alice   # http://localhost:3000/t/alice/app?token=...
```

Fixtures mode serves `fixtures/*.json` (copies of `ios/Brian/Fixtures/`). Screenshot URLs,
`/?screen=<name>&mode=<light|dark>&scale=<1|2>`, read `fixtures/design/*.json` instead.
`node scripts/shot.mjs "/?screen=today&mode=light" <out.png>` takes a true 390 x 844 screenshot with
headless Chrome; a path is resolved against `PHONE_URL` (default `http://localhost:3100`, the fixtures
dev server: `NEXT_PUBLIC_FIXTURES=1 npx next dev -p 3100`).
`node scripts/overflow.mjs [out.txt]` loads every embedded screen at 390 px and fails on page-level
horizontal overflow (and on /analysis, a chart whose latest day or caption is cut off).

On the iPhone: `npm run dev -- -H 0.0.0.0`, open `http://<this PC's Wi-Fi IP>:3000` in Safari,
Share → Add to Home Screen.
