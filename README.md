# hackrice — lifestyle tracking on Ray-Ban Meta glasses

Architecture and contracts: [SPEC.md](SPEC.md). API and seam: [docs/API.md](docs/API.md).
Build narrative for the stage script: [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## Run the whole system (one process)

As of S10, Person A's capture pipeline (`src/longevity`) runs inside Person B's
FastAPI process — one backend, one port, no separate capture service.

```bash
# backend — real glasses, live Gemini tagger, live GPT reasoner
cd backend
cp .env.example .env          # then set OPENAI_API_KEY and GEMINI_API_KEY
uv sync
uv run python -m pipeline.main --source glasses --vlm gemini --reasoner openai --port 8010
```

The phone connects to `ws://<mac-lan-ip>:8010/ws/glasses`.

```bash
# dashboard
cd dashboard
npm install
echo 'NEXT_PUBLIC_API_BASE=http://localhost:8010' > .env.local
npm run dev                   # http://localhost:3000
```

No hardware ever leaves you stuck — fall back in this order:

1. `--source webcam` — uses the Mac's own camera. Needs camera permission for
   the terminal app: System Settings → Privacy & Security → Camera.
2. `--source replay --dir <corpus> --loop` — replays a directory of
   timestamped JPEGs at 1 Hz. Use Aidan's 143-frame glasses corpus, or
   `data/corpus_smoke` for a quick smoke test.
3. `--source sim --speed 5` — fully synthetic scripted scenario, no hardware
   or corpus at all.

Env vars (`backend/.env`): `OPENAI_API_KEY` (T1 reasoner), `GEMINI_API_KEY`
(T0 tagger — pinned to `gemini-2.5-flash-lite`, see FINDINGS.md; do not
"upgrade" to the `-latest` alias), `TICK_INTERVAL_S=1.5` (glasses cadence),
plus the optional Fitbit vars (`FITBIT_CLIENT_ID`, `FITBIT_CLIENT_SECRET`,
`FITBIT_REDIRECT_URI`, `FITBIT_TOKEN_PATH`, `FITBIT_POLL_S`) for live
wearable data. The `/api/healthspan` profile comes from `PROFILE_AGE`,
`PROFILE_SEX`, `PROFILE_GOAL` (`average|athlete|shift|genetic_risk`) and
`PROFILE_CYP1A2_SLOW`, plus the optional `PROFILE_HEIGHT_M` (reserved; leave
it commented out rather than empty).

`backend/` now requires **Python 3.11** — it shares a venv with `longevity`
(Person A's package at the repo root, `requires-python = ">=3.11,<3.12"`).

Other useful flags: `--reasoner fake` / `--vlm fake` / `--vlm off` (no API
keys, rule-based or tag-free), `--camera <n>` (webcam device index),
`--headless` (print the decision feed, no HTTP), `--no-demo-mode`
(production cooldowns), `--tick-interval` (seconds between ticks; glasses
emit at 1.5 s). `npm run dev:mock` renders the dashboard with fake data and
no backend.

Tests: `cd backend && uv run pytest -q` (1087). `src/longevity` has its own
suite: `uv run pytest tests -q` from the repo root (46 tests). Dashboard:
`npm run build`.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/pipeline/{config,models,db,bus,frames}.py` | B | contracts, storage, tick bus, 90 s frame store |
| `backend/pipeline/sim/` | B | synthetic tick source (scripted scenario, placeholder JPEGs) |
| `backend/pipeline/gate/`, `episodes/` | B | trigger gate, episode builder |
| `backend/pipeline/reasoner/`, `actions/` | B | T1 GPT call, envelope, evidence, actions, speech limiter |
| `backend/pipeline/scoring/`, `seed/` | B | SPEC §8 scorer, 7-day seeded integration data |
| `backend/pipeline/scoring/{brian_score,healthspan}.py` | B | dose-response hazard engine (healthy-life hours, LE delta with CI, levers, tonight forecast, weekly ledger) and its adapter behind `/api/healthspan` |
| `backend/pipeline/api/` | B | FastAPI app, dashboard routes, wiring |
| `dashboard/` | B | Next.js dashboard |
| `backend/pipeline/capture/` | B/A seam | the bridge: mounts A's `T0Loop`, `TickBus`, `FrameRing`, and `/ws/glasses` + `/frames` routes into B's FastAPI app; no edits to A's code |
| `src/longevity/` | A | T0 capture pipeline: `CaptureSource` adapters (`glasses`, `webcam`, `replay`), sensor fields, frame ring buffer, Gemini VLM tagger, tick assembly |
| `ios/` | A | Swift side of the glasses path: socket link to the Mac (`MacLink.swift`), corpus recorder (`CorpusRecorder.swift`); lives outside the repo's build, kept here for version control |
