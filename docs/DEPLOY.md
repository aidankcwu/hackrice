# DEPLOY.md: the backend off the LAN (Fly.io)

One Fly machine runs the backend image (`Dockerfile` at the repo root). SQLite and the
OAuth token files sit on a Fly volume at `/data`. Fly terminates TLS, so clients use
`https://` and `wss://`, and the app itself speaks plain HTTP on port 8010. A bearer
token (`API_TOKEN`) protects every `/api/*` route, the `/ws/glasses` socket, `/frames`,
`/frames/stats` and `/ingest/stats`. The two OAuth callbacks are the only exemption (§3).

If `API_TOKEN` is unset or blank, auth is **off**, so a LAN demo with no token works
exactly as it did before.

> **Not build-tested.** Docker is not installed on the dev machine, so this image has
> never been built. What was run: both `uv sync` steps from the Dockerfile, in a scratch
> copy of the build context, and the image's `CMD` (`python -m pipeline.main`, from a
> directory with no `.env`, with `API_TOKEN` set). Results: `/api/status` gave 401 with
> no token and 200 with the Bearer header or `?token=`. `/ws/glasses` refused the
> handshake (403) without the token and accepted it with the token. The first `fly deploy`
> will be the first real build.

## 1. Steps

Run everything from the repo root. `<app>` is the name you choose.

```bash
# Install flyctl and log in
brew install flyctl                      # or: curl -L https://fly.io/install.sh | sh
# Windows: pwsh -Command "iwr https://fly.io/install.ps1 -useb | iex"
fly auth login

# Create the app, then set `app = "<app>"` in fly.toml
fly apps create <app>
# (or: fly launch --no-deploy --copy-config --name <app>, which keeps this fly.toml)

# The volume fly.toml mounts at /data (same region as primary_region)
fly volumes create brian_data --app <app> --region dfw --size 1 --yes

# Secrets. Keep the token: every client below needs it.
export API_TOKEN="$(openssl rand -hex 32)"; echo "$API_TOKEN"
fly secrets set --app <app> --stage \
  API_TOKEN="$API_TOKEN" \
  OPENAI_API_KEY=sk-... \
  GEMINI_API_KEY=... \
  ELEVENLABS_API_KEY=...

# Deploy one machine. Two would mean two separate SQLite files.
fly deploy --app <app> --ha=false
```

`fly.toml` runs the real mode: `SOURCE=glasses REASONER=openai VLM=gemini`. To do a smoke
deploy with no model keys, run
`fly deploy --ha=false -e SOURCE=sim -e REASONER=fake -e VLM=fake`.
In sim mode `/ws/glasses` is not mounted, so the WebSocket check below returns 403 even
with the token.

## 2. Verify

```bash
APP=https://<app>.fly.dev
curl -i "$APP/api/status"                                     # 401, WWW-Authenticate: Bearer
curl -H "Authorization: Bearer $API_TOKEN" "$APP/api/status"  # 200, JSON
curl "$APP/api/status?token=$API_TOKEN"                       # 200, JSON
curl -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer wrong" "$APP/api/status"  # 401

# The glasses socket (SOURCE=glasses)
websocat "wss://<app>.fly.dev/ws/glasses"                     # refused: 403
websocat -H "Authorization: Bearer $API_TOKEN" "wss://<app>.fly.dev/ws/glasses"
#   then type {"v":1,"type":"ping"}; `fly logs` shows "ingest: phone connected"
wscat -c "wss://<app>.fly.dev/ws/glasses?token=$API_TOKEN"    # same, query form
```

The server closes a socket that sends nothing for 30 s (`INGEST_IDLE_TIMEOUT_S`). The
phone pings every 10 s, so only a manual session hits this.

## 3. Where the token goes

**iPhone app** (describing only; `ios/` is not edited by this task). `MacLink` builds
`ws://<host>:<port>/ws/glasses` in `ios/MacLink.swift:156` (init) and `:198`
(`configure(host:port:)`), and opens it with `URLSession.shared.webSocketTask(with: url)`
at `:239`. To reach Fly it needs two changes:
- The scheme must be `wss://`, with host `<app>.fly.dev` and port 443. `force_https`
  redirects plain `ws://`, and a WebSocket client does not follow redirects.
- It must send the token, in one of two ways. Either append `?token=<API_TOKEN>` to the
  URL (path `/ws/glasses?token=…`), or build a `URLRequest`, call
  `setValue("Bearer <API_TOKEN>", forHTTPHeaderField: "Authorization")`, and pass it to
  `webSocketTask(with: request)`.

The app's host/port fields can express neither change today. `wss` to a public host needs
no ATS exception.

**Dashboard.** Put this in `dashboard/.env.local`, then restart `npm run dev` or rebuild,
because `NEXT_PUBLIC_*` values are compiled in:

```
NEXT_PUBLIC_API_BASE=https://<app>.fly.dev
NEXT_PUBLIC_API_TOKEN=<API_TOKEN>
```

With the token set, every fetch in `src/lib/api.ts` and `src/lib/score/backend.ts` sends
`Authorization: Bearer`. URLs that cannot carry a header get `?token=` appended: the
evidence thumbnails (`backend.ts`, `SessionReport.tsx`) and the protocol CSV export link.
With the token unset, nothing extra is sent.

If the dashboard runs anywhere other than `http://localhost:3000`, allow its origin:
`fly secrets set CORS_ORIGINS=https://dash.example.com,http://localhost:3000`.

**The token is public in the dashboard.** A `NEXT_PUBLIC_` variable is compiled into the
JavaScript sent to the browser. Anyone who can load the dashboard can read the token in
devtools and call the whole API with it. That is fine for a demo run from your own laptop
and not fine for a publicly hosted dashboard. To rotate, run `fly secrets set
API_TOKEN=<new>` (this restarts the machine) and update every client.

**Wearable pushers.**
- Health Auto Export: Automations → REST API → URL
  `https://<app>.fly.dev/api/wearables/ingest/health-auto-export?token=<API_TOKEN>`.
  If `WEARABLE_INGEST_TOKEN` is also set, add the header `X-Ingest-Token: <that value>`.
  Both checks apply; the new gate does not replace that one.
- WHOOP: `/api/wearables/ingest/whoop?token=…`. Any pusher that can set headers can send
  `Authorization: Bearer <API_TOKEN>` to `/api/wearables/ingest` instead.
- Fitbit / Google Health OAuth:
  1. Set `FITBIT_REDIRECT_URI` / `GOOGLE_HEALTH_REDIRECT_URI` to
     `https://<app>.fly.dev/api/wearables/<fitbit|google-health>/callback`, with no
     token in it, and register exactly that URI with the provider.
  2. Open `https://<app>.fly.dev/api/wearables/<fitbit|google-health>/authorize?token=<API_TOKEN>`
     in a browser. `/authorize` is behind the token.
  3. The provider redirects back to `/callback` with no token. Those two exact callback
     paths are the only ones outside the gate.

  They are safe to leave open because a callback returns 400, with no token exchange,
  unless its `state` is one that `/authorize` issued and has not been used yet:
  - Fitbit: `backend/pipeline/wearables/fitbit_routes.py:40-41` issues the state,
    `:46-47` rejects anything else.
  - Google Health: `google_health_routes.py:31-32` issues it, `:35-36` rejects anything
    else.

  Each state carries the PKCE verifier that its token exchange needs (`fitbit.py:91-97,113-115`,
  `google_health.py:88-94,103-106`), so a forged or replayed callback gets nowhere.
  Never put `API_TOKEN` in a redirect URI: it would end up in the provider's records and
  logs.

## 4. Environment variables

The container starts with no `.env`, and none is copied into the image:
- pydantic-settings skips a missing `.env`.
- `load_dotenv` on a missing file does nothing.
- `.dockerignore` excludes `**/.env`.

Every CLI flag the service needs now has an env default (a flag still wins). The flags
with no env var are not needed on Fly:
- `--speed`: 1.0 is real time.
- `--dir`, `--loop`, `--camera`: replay and webcam only.
- `--fresh`, `--no-seed`, `--headless`, `-v`.
- `--flow`: the `LONGEVITY_FLOW` env var covers it.

| Variable | Where set | Meaning |
|---|---|---|
| `API_TOKEN` | secret | Bearer token for `/api/*`, `/ws/glasses`, `/frames*` and `/ingest/stats` (§5). Unset or blank turns auth off. Read on each request. |
| `OPENAI_API_KEY` | secret | T1 reasoner. Required when `REASONER=openai`; startup refuses without it. |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | secret | T0 vision tagger. Required when `VLM=gemini` and `SOURCE` is not `sim`. |
| `ELEVENLABS_API_KEY` | secret | Voice. With `SPEECH_MODE=auto` and no key, speech goes out as text. |
| `WEARABLE_INGEST_TOKEN` | secret, optional | Adds an `X-Ingest-Token` check on `/api/wearables/ingest*`. |
| `PORT` | image + fly.toml: `8010` | Bind port (host is always `0.0.0.0`). Must equal `internal_port`. |
| `SOURCE` | fly.toml: `glasses` | `sim` / `glasses` / `webcam` / `replay` (was `--source`). |
| `REASONER` | fly.toml: `openai` | `openai` / `fake` (was `--reasoner`). |
| `VLM` | fly.toml: `gemini` | `gemini` / `fake` / `off` (was `--vlm`). |
| `DATA_DIR` | image: `/data` | The volume. When the container starts as root, the entrypoint chowns it and drops to user `app`. |
| `DB_PATH` | image: `/data/pipeline.db` | SQLite, plus its `-wal`/`-shm` files. Evidence frames are stored in this database. |
| `FITBIT_TOKEN_PATH`, `GOOGLE_HEALTH_TOKEN_PATH` | image: `/data/…json` | OAuth tokens. |
| `CORS_ORIGINS` | optional | Comma-separated browser origins. Default `http://localhost:3000`. |
| `FITBIT_CLIENT_ID/_SECRET/_REDIRECT_URI`, `GOOGLE_HEALTH_CLIENT_ID/_SECRET/_REDIRECT_URI` | optional | Live wearables. Each redirect URI must be the Fly URL. |
| `DEMO_MODE`, `TICK_INTERVAL_S`, `SPEECH_MODE`, `T1_MODEL`, `ELEVENLABS_VOICE_ID`, `KEYWORD_TRIGGERS_JSON`, `AIR_*`, `PROFILE_*`, `OUTDOOR_TARGET_MIN`, `WIND_DOWN_HHMM`, kill switches (`FAST_PATH`, …) | optional | Every other `Settings` field in `backend/pipeline/config.py`. Template: `backend/.env.example`. |
| `T0_VLM_MODEL`, `VLM_MAX_IN_FLIGHT`, `INGEST_IDLE_TIMEOUT_S`, `LONGEVITY_FLOW` | optional | Read from the environment by `longevity`. |

## 5. What the token covers, and what it does not

`_guarded()` in `backend/pipeline/api/app.py` is the only list. It covers:
- every `/api/*` route;
- `/ws/glasses`;
- `/frames`, `/frames/…` (so `/frames/stats` too) and `/ingest/stats`.

`/frames` is behind the token because it returns raw glasses frames from the 90 s ring
buffer, and frame refs are sequential (`f_00000410`, `f_00000411`, …), so anyone could
list them. No dashboard or iOS code calls `/frames` or `/ingest/stats`. For a manual
check, use `curl -H "Authorization: Bearer $API_TOKEN" $APP/ingest/stats`.

Open to anyone:
- The two OAuth callbacks, by exact path: `/api/wearables/fitbit/callback` and
  `/api/wearables/google-health/callback`. They only accept a state that `/authorize`
  issued (§3). Their siblings `/authorize`, `/status` and `/sync` stay behind the token.
- `/docs`, `/redoc`, `/openapi.json`: the API schema, with no data in it. The Fly health
  check uses `/docs`.

Also: a `?token=` request shows the token in uvicorn's access log (`fly logs`). Where a
client can send a header, prefer the header.
