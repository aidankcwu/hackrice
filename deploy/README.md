# Hosting the backend for testers

Each tester gets three containers: their own backend, their own dashboard
and their own phone web app (`phone/`, Lukas's Next.js app), behind one Caddy
reverse proxy that handles TLS. The backend is
single-user by design: one database, one persona, one session and one
conversation slot. Running one copy per person keeps all of that without a
code change.

```
glasses via iOS app ──wss──┐
                           ├─> Caddy :443 ─┬─ /t/alice/app*       ─> phone-alice:3000      (prefix kept)
phone browser (app) ─https─┤               ├─ /t/alice/dashboard* ─> dashboard-alice:3000  (prefix kept)
browser (dashboard) ─https─┘               └─ /t/alice/*          ─> backend-alice:8010    (prefix stripped)
```

The phone web app's browser code calls the backend itself, same origin, at
`https://DOMAIN/t/alice/api/...` (the last route), with the token as
`Authorization: Bearer`. The app container serves pages only.

Everything runs in demo mode with seeded data and a preset persona. There
is no Google Health or Fitbit login (see "What testers cannot do").

## Ten-minute runbook (fresh Ubuntu VPS, any provider)

Size: 2 vCPU / 4 GB is enough for 2 testers. 4 vCPU / 8 GB handles about
4 (see [memory](#memory-three-containers-per-tester) and
[cost](#cost-per-tester-per-hour)).

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
builds alice's dashboard and phone app (about a minute each, because Next
bakes in the `/t/alice/dashboard` and `/t/alice/app` base paths), starts all
three containers, reloads Caddy and prints:

```
phone app (the link to send the tester; open it in Safari on the phone):
  https://brian.example.com/t/alice/app/?token=<TOKEN>
iOS app (glasses socket):
  wss://brian.example.com/t/alice/ws/glasses?token=<TOKEN>
dashboard (the link to send the tester):
  https://brian.example.com/t/alice/dashboard/?token=<TOKEN>
backend API base (header X-Access-Token: <token>, or ?token=):
  https://brian.example.com/t/alice
health (no token needed):
  https://brian.example.com/t/alice/healthz
  https://brian.example.com/t/alice/app/healthz
```

**6. Verify:**

```bash
curl -fsS https://brian.example.com/t/alice/healthz                         # 200, no token
curl -s -o /dev/null -w '%{http_code}\n' https://brian.example.com/t/alice/api/status # 401
curl -s -o /dev/null -w '%{http_code}\n' https://brian.example.com/t/alice/api/persona   # 401
curl -fsS -H "X-Access-Token: $(sed -n 's/^ACCESS_TOKEN=//p' testers/alice.env)" \
     https://brian.example.com/t/alice/api/persona                                  # 200
curl -fsS https://brian.example.com/t/alice/app/healthz                     # 200, no token
curl -s -o /dev/null -w '%{http_code}\n' https://brian.example.com/t/alice/app      # 401
curl -s -o /dev/null -w '%{http_code}\n' \
     "https://brian.example.com/t/alice/app?token=$(sed -n 's/^ACCESS_TOKEN=//p' testers/alice.env)"  # 200
docker compose ps                 # backend-alice, dashboard-alice and phone-alice show (healthy)
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
Tester add/remove operations are serialized with the mkdir-based lock
`deploy/.tester-provision.lock`, which works on both macOS and Linux. If a
machine crash or `kill -9` leaves that lock behind, first verify that no
`new_tester.sh` or `remove_tester.sh` process is running (the lock's `pid` file
records the creator), then remove the stale lock directory and retry.
Removal stops and deletes all of that tester's containers (backend, dashboard,
phone app) and their two per-tester images. It uses `docker compose rm -sfv`,
so that tester's container logs and
anonymous volumes are removed. The default deletion is permanent and includes
the database, evidence JPEGs, wearable tokens and dashboard data. Use
`--archive` only when retention is explicitly intended. `purge_tester.sh NAME`
is an explicit alias for the permanent default.

To ship new code, run `git pull && docker compose build backend-image && docker compose up -d`.
For dashboard changes, also run `docker compose build dashboard-NAME` for each
tester; for phone app changes, `docker compose build phone-NAME`; then
`docker compose up -d` again.

A tester added before the phone app existed has no `phone-NAME`. To give them
one, remove and re-add them (`--archive` keeps their data, but the token
changes), or run `./new_tester.sh` for a new name.

## What a VC's phone needs

**The app.** One link, opened in Safari on the phone:

```
https://brian.example.com/t/alice/app/?token=<TOKEN>
```

- The app is built with no secret and no backend address. In the browser it
  derives the backend from its own URL (same origin, `/t/alice`, the `/app`
  segment dropped), stores the token in `localStorage` under a key namespaced
  by `/t/alice` (so two testers on one phone do not clobber each other), takes
  `?token=` out of the address bar, and sends `Authorization: Bearer <token>`
  on every backend request. Its own pages are gated by the same token (an
  httpOnly cookie set on the first visit, `path=/t/alice/app`); without it they
  answer `401` with a short "open the link you were given" page.
- Share → Add to Home Screen works. The Home Screen app starts at
  `/t/alice/app` without the token and its storage is separate from Safari's;
  when iOS carried the cookie over, the app fetches the token back from its own
  `/t/alice/app/api/token` (answered only to that cookie). If a Home Screen
  launch shows the 401 page, open the link in Safari again and re-add it.
- Open without a token: `/t/alice/app/healthz`, the manifest and the icons.

**The glasses.** One URL, pasted into the iOS app's backend field:

```
wss://brian.example.com/t/alice/ws/glasses?token=<TOKEN>
```

- It must be `wss://`. iOS App Transport Security blocks plain `ws://` to
  a public host, so the Caddy certificate is required. It is publicly
  trusted (Let's Encrypt), so there is nothing to install on the phone.
- The app can send the token as `?token=` in the URL, as an
  `X-Access-Token` header, or as `Authorization: Bearer <token>`. All three
  work (see the contract below for which one wins when several are sent).
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
| Token as bearer | `Authorization: Bearer <token>` (same token; any other scheme counts as absent) |
| Token query parameter | `?token=<token>` (for `<img src>` and pasted socket URLs) |
| Precedence | `X-Access-Token`, then `Authorization: Bearer`, then `?token=`. The first one present is the only one compared (constant-time); a later one never rescues a wrong earlier one. HTTP and the socket use the same order |
| Open without a token | `GET /healthz` only, plus CORS preflight |
| Socket refused | accepted, then closed with code `4401` (reason `unauthorized`) |
| HTTP refused | `401`, with CORS headers so the browser shows the real error |
| Wearable OAuth callbacks (`HOSTED=1`) | `409` `{"error": "wearable login is not available on hosted testers"}`, before the token check |
| Prefix | `/t/NAME/` is stripped before the backend; `/t/NAME/dashboard*` and `/t/NAME/app*` are not (they go to the dashboard and phone app containers), so no backend route may start with `/app` or `/dashboard` |
| URLs the backend returns | backend-relative paths (`/api/evidence/{id}/{ref}`); join them to `https://DOMAIN/t/NAME` and add `?token=` for image tags |
| Token format | `secrets.token_urlsafe(32)`: letters, digits, `-` and `_`, safe in a query string |

With `ACCESS_TOKEN` empty in local development (no `ROOT_PATH` and no
`HOSTED=1`), nothing is checked, the same as before. Hosted startup refuses an
empty token whenever `ROOT_PATH` is set or `HOSTED=1`.

`API_TOKEN` is read as a legacy alias of `ACCESS_TOKEN` (the name the
`brian-ios` branch's web app uses). Set only one. If both are set and differ,
the backend refuses to start and says so.

## What testers cannot do

- **Log in to Fitbit or Google Health.** The provider sends the browser back
  to `/api/wearables/{fitbit,google-health}/callback`, and that redirect
  cannot carry the access token, so on a hosted tester it could only ever be
  refused. With `HOSTED=1` those callbacks answer `409`
  `{"error": "wearable login is not available on hosted testers"}` instead of
  a bare `401`. Testers run in demo mode on seeded wearable data and never
  need a provider login. Unsupported until further notice. Making it work
  needs a signed OAuth `state` that stands in for the token, which is not
  built.

## Deploying the `brian-ios` branch's `fly.toml`

If anyone deploys that branch's Fly config against this backend, its health
check must probe **`/healthz`**, not `/docs`. With `ACCESS_TOKEN` set, every
route but `/healthz` answers `401` without the token, so a `/docs` probe
fails and Fly keeps restarting a healthy machine.

## Memory: three containers per tester

| Container | `mem_limit` (tester.yml) | Notes |
|---|---|---|
| `backend-NAME` | 1 GB | numpy, OpenCV and the pipeline |
| `dashboard-NAME` | 512 MB | Next server plus a python3 spawned per score request |
| `phone-NAME` | 256 MB | Next server for pages only; measured at about 40 MB idle |
| **per tester** | **1.75 GB** | caps, not reservations |

Size the VPS by the caps so no tester can push another into the OOM killer:
leave about 0.5 GB for the OS, Docker and Caddy, which gives **2 testers on
4 GB and 4 on 8 GB**. Real use is usually well under the caps, so more fit if
you watch `docker stats` and accept that risk. Each tester also costs about
two minutes of build and two per-tester images (`hackrice-dashboard:NAME`,
`hackrice-phone:NAME`, a few hundred MB of disk each, shared layers aside).

## Cost per tester per hour

These are estimates. The assumptions are listed so you can redo the math
against current price pages. An **active** hour means glasses streaming and
someone talking to Bryan.

| Item | Assumption | $/active hour |
|---|---|---|
| Gemini 2.5 Flash-Lite (T0 tagger) | 1 call per 1.5 s tick = 2,400 calls; ~1,300 input tokens (frame + prompt) and ~150 output each; $0.10/M in, $0.40/M out | ~0.45 |
| OpenAI (clerk + voice agent, `gpt-5.4-mini`) | ~90 calls (demo-mode wake-ups plus conversation turns); ~5k input and ~400 output tokens each; $0.75/M in, $4.50/M out (check the current rate) | ~0.50 |
| ElevenLabs | ~30 spoken lines × ~100 chars = 3k chars; $0.15–0.30 per 1k chars depending on plan and model | 0.45–0.90 |
| VPS | 4 vCPU / 8 GB at about $30–50/month shared by 4 testers (three containers each, see [memory](#memory-three-containers-per-tester)) | ~0.01 |
| Bandwidth | ~40 KB JPEG per 1.5 s ≈ 100 MB/h inbound, which is free on most providers | ~0 |
| **Total** | | **≈ $1.50–2.00** |

An **idle** tester (container up, glasses off) costs only the VPS share,
about $0.01/h. With no frames coming in, nothing calls a model.

## Files

| File | Role |
|---|---|
| `Dockerfile` | the backend image: `src/longevity` + `backend/pipeline`, uv-built, Python 3.11, no dev deps, runs as uid 10001, `HEALTHCHECK` on `/healthz` |
| `docker-compose.yml` | Caddy, plus the `backend-image` build target |
| `tester.yml` | the templates `backend`, `dashboard` and `phone` that every tester's services extend |
| `Caddyfile` | the site; imports `routes/*.caddy` |
| `new_tester.sh`, `remove_tester.sh`, `purge_tester.sh`, `_lib.sh` | the tester lifecycle; `_lib.sh` also writes each tester's compose services and Caddy route |
| `../phone/Dockerfile` | the phone web app image, built per tester with `NEXT_BASE_PATH=/t/NAME/app` |
| `.env.example` | shared keys, placeholders only; copy to `.env` |
| generated: `testers/`, `routes/`, `data/`, `docker-compose.override.yml` | per-tester state, git-ignored |

Backend settings that exist for hosting (all default to local-Mac behaviour):
`ACCESS_TOKEN` (legacy alias `API_TOKEN`), `HOSTED`, `PERSONA_FILE`, `DEMO_RESET_ON_START`,
`DEMO_RESET_ALL`, `CORS_ORIGINS`, `ROOT_PATH` and `PORT`. See
`backend/.env.example`.
