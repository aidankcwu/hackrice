# hackrice — lifestyle tracking on Ray-Ban Meta glasses

Architecture and contracts: [SPEC.md](SPEC.md). API and seam: [docs/API.md](docs/API.md).
Build narrative for the stage script: [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## Run the Person B stack (laptop only, no glasses needed)

```bash
# backend — simulated glasses, live GPT reasoner, 5x simulated time
cd backend
cp .env.example .env          # then set OPENAI_API_KEY
uv sync
uv run python -m pipeline.main --reasoner openai --speed 5 --port 8010
```

```bash
# dashboard
cd dashboard
npm install
echo 'NEXT_PUBLIC_API_BASE=http://localhost:8010' > .env.local
npm run dev                   # http://localhost:3000
```

Useful flags: `--reasoner fake` (no API key, rule-based decisions),
`--headless` (print the decision feed, no HTTP), `--no-demo-mode`
(production cooldowns), `--speed 1` (real time). `npm run dev:mock` renders the
dashboard with fake data and no backend.

Tests: `cd backend && uv run pytest -q` (885). Dashboard: `npm run build`.

## Layout

| Path | Owner | What |
|---|---|---|
| `backend/pipeline/{config,models,db,bus,frames}.py` | B | contracts, storage, tick bus, 90 s frame store |
| `backend/pipeline/sim/` | B | synthetic tick source (scripted scenario, placeholder JPEGs) |
| `backend/pipeline/gate/`, `episodes/` | B | trigger gate, episode builder |
| `backend/pipeline/reasoner/`, `actions/` | B | T1 GPT call, envelope, evidence, actions, speech limiter |
| `backend/pipeline/scoring/`, `seed/` | B | SPEC §8 scorer, 7-day seeded integration data |
| `backend/pipeline/api/` | B | FastAPI app, dashboard routes, wiring |
| `dashboard/` | B | Next.js dashboard |
| `backend/pipeline/capture/` (to come) | A | iOS bridge ingest, real ring buffer, T0 VLM, TTS |
