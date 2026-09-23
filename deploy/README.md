# Hosting the backend for testers

Each tester gets their own backend container and their own dashboard
container, behind one Caddy reverse proxy that handles TLS. The backend is
single-user by design: one database, one persona, one session and one
conversation slot. Running one copy per person keeps all of that without a
code change.

```
phone (Ray-Ban Meta) ─wss─┐
                          ├─> Caddy :443 ── /t/alice/dashboard* ─> dashboard-alice:3000  (prefix kept)
browser (dashboard) ─https┘        │
                                   └──────── /t/alice/*  ─────────> backend-alice:8010    (prefix stripped)
```

Everything runs in demo mode with seeded data and a preset persona. There
is no Google Health login.

## Ten-minute runbook (fresh Ubuntu VPS, any provider)

Size: 2 vCPU / 4 GB is enough for 2–3 testers. 4 vCPU / 8 GB handles about
6 (see [cost](#cost-per-tester-per-hour)).

**0. DNS.** Point an A record (for example `brian.example.com`) at the VPS's
public IP. Open ports 80 and 443 in the provider's firewall. Caddy needs both
to get its Let's Encrypt certificate.

**1. Install Docker** (about 2 minutes):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
docker compose version          # must print v2.x
```

**2. Clone:**

```bash
git clone <this repo> hackrice && cd hackrice/deploy
```

**3. Fill in the keys:**

```bash
cp .env.example .env && nano .env   # DOMAIN, GEMINI_API_KEY, OPENAI_API_KEY,
                                    # ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
```

Optional: put the preset persona in `deploy/persona.txt`. Each new tester
gets a copy, loaded at startup only if their database has no persona yet.
Leave the file out and they get the built-in persona.

**4. Start the proxy and build the image** (the first build takes 3–5 minutes):

```bash
docker compose build backend-image
docker compose up -d
```

**5. Add a tester:**

```bash
./new_tester.sh alice
```

This allocates the next host port (8101, 8102, and so on), generates a
token, creates `data/alice/`, writes `testers/alice.{yml,env}` and
`routes/alice.caddy`, and regenerates `docker-compose.override.yml`. It then
builds alice's dashboard (about a minute, because Next bakes in the
`/t/alice/dashboard` base path), starts both containers, reloads Caddy and
prints:

```
iOS app (glasses socket):
  wss://brian.example.com/t/alice/ws/glasses?token=<TOKEN>
dashboard (the link to send the tester):
  https://brian.example.com/t/alice/dashboard/?token=<TOKEN>
backend API base (header X-Access-Token: <token>, or ?token=):
  https://brian.example.com/t/alice
health (no token needed):
  https://brian.example.com/t/alice/healthz
```

**6. Verify:**

```bash
curl -fsS https://brian.example.com/t/alice/healthz                         # 200, no token
curl -s -o /dev/null -w '%{http_code}\n' https://brian.example.com/t/alice/api/status # 401
curl -s -o /dev/null -w '%{http_code}\n' https://brian.example.com/t/alice/api/persona   # 401
curl -fsS -H "X-Access-Token: $(sed -n 's/^ACCESS_TOKEN=//p' testers/alice.env)" \
     https://brian.example.com/t/alice/api/persona                                  # 200
docker compose ps                 # backend-alice and dashboard-alice show (healthy)
```

**7. Watch it:**

```bash
docker compose logs -f backend-alice        # the pipeline; tokens in URLs are logged as ***
docker compose logs -f caddy                # certificates, proxy errors
curl -s -H "X-Access-Token: $(sed -n 's/^ACCESS_TOKEN=//p' testers/alice.env)" \
  https://brian.example.com/t/alice/api/status | python3 -m json.tool | grep -A3 problems
```

`health.problems` in `/api/status` uses the same names as on stage:
`phone_disconnected`, `no_packets_10s`, `ai_coverage_low`, `vlm_errors`,
`t1_errors` and `tts_failing`.

### Adding and removing testers

```bash
./new_tester.sh bob               # any time; the other testers keep streaming
./remove_tester.sh alice          # stops containers and permanently deletes all tester data
./remove_tester.sh --archive bob  # instead move bob's data under data/_removed/
docker compose restart backend-bob   # a fresh demo: DEMO_RESET_ON_START plus
                                     # DEMO_RESET_ALL clears the whole measured day
```

Adding or removing a tester reloads Caddy. `stream_close_delay` in each
route keeps the other testers' glasses sockets open across the reload.
Removal also uses `docker compose rm -sfv`, so that tester's container logs and
anonymous volumes are removed. The default deletion is permanent and includes
the database, evidence JPEGs, wearable tokens and dashboard data. Use
`--archive` only when retention is explicitly intended. `purge_tester.sh NAME`
is an explicit alias for the permanent default.

To ship new code, run `git pull && docker compose build backend-image && docker compose up -d`.
For dashboard changes, also run `docker compose build dashboard-NAME` for each tester.

## What a VC's phone needs

One URL, pasted into the iOS app's backend field:

```
wss://brian.example.com/t/alice/ws/glasses?token=<TOKEN>
```

- It must be `wss://`. iOS App Transport Security blocks plain `ws://` to
  a public host, so the Caddy certificate is required. It is publicly
  trusted (Let's Encrypt), so there is nothing to install on the phone.
- The app can send the token either as `?token=` in the URL or as an
  `X-Access-Token` header. Both work.
- A missing or wrong token: the server accepts the socket and then closes it
  with code **4401**, so the app can show "wrong token" instead of "cannot
  connect".
- The phone pings every 10 s. Caddy puts no idle timeout on the socket, and
  the backend closes a socket after 30 s with no traffic.

Send the dashboard link, `https://brian.example.com/t/alice/dashboard/?token=<TOKEN>`,
to the same person.

## The contract, for anyone building a client

| What | Value |
|---|---|
| Token header | `X-Access-Token: <token>` |
| Token query parameter | `?token=<token>` (for `<img src>` and pasted socket URLs) |
| Open without a token | `GET /healthz` only, plus CORS preflight |
| Socket refused | accepted, then closed with code `4401` (reason `unauthorized`) |
| HTTP refused | `401`, with CORS headers so the browser shows the real error |
| Prefix | `/t/NAME/` is stripped before the backend; `/t/NAME/dashboard*` is not |
| URLs the backend returns | backend-relative paths (`/api/evidence/{id}/{ref}`); join them to `https://DOMAIN/t/NAME` and add `?token=` for image tags |
| Token format | `secrets.token_urlsafe(32)`: letters, digits, `-` and `_`, safe in a query string |

With `ACCESS_TOKEN` empty in local development (no `ROOT_PATH` and no
`HOSTED=1`), nothing is checked, the same as before. Hosted startup refuses an
empty token whenever `ROOT_PATH` is set or `HOSTED=1`.

## Cost per tester per hour

These are estimates. The assumptions are listed so you can redo the math
against current price pages. An **active** hour means glasses streaming and
someone talking to Bryan.

| Item | Assumption | $/active hour |
|---|---|---|
| Gemini 2.5 Flash-Lite (T0 tagger) | 1 call per 1.5 s tick = 2,400 calls; ~1,300 input tokens (frame + prompt) and ~150 output each; $0.10/M in, $0.40/M out | ~0.45 |
| OpenAI (clerk + voice agent, `gpt-5.4-mini`) | ~90 calls (demo-mode wake-ups plus conversation turns); ~5k input and ~400 output tokens each; $0.75/M in, $4.50/M out (check the current rate) | ~0.50 |
| ElevenLabs | ~30 spoken lines × ~100 chars = 3k chars; $0.15–0.30 per 1k chars depending on plan and model | 0.45–0.90 |
| VPS | 4 vCPU / 8 GB at about $30–50/month shared by 6 testers (each backend capped at 1 GB, each dashboard at 512 MB) | ~0.01 |
| Bandwidth | ~40 KB JPEG per 1.5 s ≈ 100 MB/h inbound, which is free on most providers | ~0 |
| **Total** | | **≈ $1.50–2.00** |

An **idle** tester (container up, glasses off) costs only the VPS share,
about $0.01/h. With no frames coming in, nothing calls a model.

## Files

| File | Role |
|---|---|
| `Dockerfile` | the backend image: `src/longevity` + `backend/pipeline`, uv-built, Python 3.11, no dev deps, runs as uid 10001, `HEALTHCHECK` on `/healthz` |
| `docker-compose.yml` | Caddy, plus the `backend-image` build target |
| `tester.yml` | the templates `backend` and `dashboard` that every tester's services extend |
| `Caddyfile` | the site; imports `routes/*.caddy` |
| `new_tester.sh`, `remove_tester.sh`, `_lib.sh` | the tester lifecycle |
| `.env.example` | shared keys, placeholders only; copy to `.env` |
| generated: `testers/`, `routes/`, `data/`, `docker-compose.override.yml` | per-tester state, git-ignored |

Backend settings that exist for hosting (all default to local-Mac behaviour):
`ACCESS_TOKEN`, `HOSTED`, `PERSONA_FILE`, `DEMO_RESET_ON_START`,
`DEMO_RESET_ALL`, `CORS_ORIGINS`, `ROOT_PATH` and `PORT`. See
`backend/.env.example`.
