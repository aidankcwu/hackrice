# Link design — glasses ↔ phone ↔ Mac, made robust

Status: proposed 2026-09-12 (Aidan's session). Batch 1 is in flight; the rest is
ordered work. Read `hardware_software.md` §23–§27 first: DAT availability is
transient, and there are two independent links, not one.

## 1. Root causes, in plain terms

There are two links and neither is supervised.

```
LINK A                          LINK B
glasses ──DAT/Bluetooth──▶ phone ──WebSocket/LAN──▶ Mac
```

Each one drops on its own, for its own reasons, and each drop is currently a
one-way door. The code treats "connected" as a thing you do once at startup
(tap Connect, tap Start Session) rather than a state to keep true.

Specifically:

1. **Nothing retries.** `MacLink` on any send or receive failure sets
   `connected = false` and stops. The DAT session, when it drops to
   "Device unavailable", stays dropped until a human taps. Both links need a
   loop whose only job is "if we are not in the desired state, get there".

2. **Liveness is inferred from the wrong signal.** The Mac believes a phone
   is connected as long as the TCP socket has not errored — and a Wi-Fi drop
   does not error a socket for minutes. The phone believes it is connected
   because `resume()` was called. `CapturePacketSender.isConnected` is derived
   by prefix-matching a status *string*. Nobody measures the one thing that
   matters: **did a message arrive in the last N seconds?** Consequences:
   ghost sockets in `link.clients`, speech sent to nobody while the dashboard
   shows `spoke: true`, and `n_idle` climbing with no alarm.

3. **Addresses are baked in.** The Mac's LAN IP is a Swift constant; a venue
   network change is an Xcode rebuild. Two ports (8000 standalone, 8010
   integrated) have already caused one confusion.

4. **No notion of a run.** The phone's `hello` is fire-and-forget; the Mac
   never answers it, so the phone cannot learn the tick interval, the server's
   clock, or whether the process it is talking to is the one that just
   restarted. The database accumulates every demo's ticks, decisions and
   annotations, so the second demo's context envelope contains the first
   demo's memory. Reset is "delete the file by hand".

5. **Backpressure is solved; failure is not.** The system is excellent at
   "drop, never queue" and has no story for "the link is down". A dead link
   looks like a quiet one.

6. **Two of everything at the seams.** Two speak paths on the Mac, two
   `isConnected` sources on the phone, two port conventions. Each duplicate is
   a place where the truth can diverge.

None of this is a bug in DAT or in the WebSocket. It is the absence of a
supervisor on each end and of a liveness signal between them.

## 2. Design

Three ideas, applied on both sides.

**(a) Desired state, reconciled forever.** Each end holds a *desired* state
(`streaming`) and a small loop that compares it with reality and acts.
Failures are inputs to the loop, not terminal events.

**(b) Liveness is message age.** "Connected" means "I heard from you in the
last N seconds". Every message refreshes it — capture packets, pings, pongs.
No message in N seconds means the link is dead regardless of what the socket
says, and the side that notices closes it.

**(c) One active phone, one run.** The Mac tracks exactly one active phone
connection (newest hello wins, the old socket is closed). Each backend start
has a `run_id`; each phone connection carries a `session_id`. The `hello` gets
an answer.

### 2.1 Wire additions (`src/longevity/wire.py`)

| Message | Direction | Fields | Purpose |
|---|---|---|---|
| `hello` | phone → Mac | `session_id`, `app_build`, `t` | (exists) announce a new connection |
| `hello_ack` | Mac → phone | `run_id`, `tick_interval_s`, `server_t` | phone adopts the interval, computes clock offset, learns whether the Mac restarted |
| `ping` | either | `t` | liveness (exists) |
| `pong` | either | `t`, `echo_t` | liveness reply (exists) |
| `capture`, `speak`, `audio` | as today | | unchanged |

Nothing else changes. Old phones that ignore `hello_ack` keep working.

### 2.2 Mac side (`src/longevity/server/ingest.py`, `backend/pipeline/capture/`)

- **`GlassesLink.active: PhoneConn | None`** replaces the `clients` set. A
  `PhoneConn` holds the socket, `session_id`, `connected_at`, `last_msg_t`,
  `packets`. A new `hello` supersedes: the previous socket is closed with
  code 1000 "superseded" and the new one becomes active. Speech goes to the
  active connection only.
- **Idle close.** `receive()` is wrapped in a timeout (`INGEST_IDLE_TIMEOUT_S`,
  default 30 s in batch 1, tighten to 15 s once the phone pings every 5 s).
  On timeout: close 1001, `active = None`, count `n_idle_closes`.
- **`ping` → `pong`** reply, logged at DEBUG.
- **`hello` → `hello_ack`** carrying `run_id`, the configured
  `tick_interval_s`, and `time.time()`.
- **`phone_state`** derived, not stored: `disconnected` (no active),
  `connected` (active, no packet in 10 s), `streaming` (packet within 10 s).
  Exposed in `/api/status.health.phone` (batch 1 adds the block).
- **Honest speech delivery.** `speak()` returns delivered count. If it is 0,
  or the active connection's `last_msg_t` is older than 5 s, the decision row
  gets `spoke=false` and `speech_status="undelivered"` instead of pretending.
  Dashboard reads it; nobody narrates a line the glasses never played.
- **One speak path.** `src/longevity/speak.py`'s `Speaker` becomes a thin
  wrapper over `backend/pipeline/capture/speak.py` (or is deleted; the `t0`
  CLI is the only caller).

### 2.3 Phone side (`ios/`)

**`MacLink`** becomes a small state machine:

```
disconnected ──connect()──▶ connecting ──hello_ack (or hello send ok)──▶ open
     ▲                          │ fail                                   │
     │                          ▼                                        │ no pong ×2 / send fail / recv fail
     └───── backoff 1,2,4,8,10s ── reconnecting ◀────────────────────────┘
                                   (only while wantConnected)
```

- `wantConnected` is the desired state; `disconnect()` is the only thing that
  clears it.
- Keepalive: `ping` every 5 s while open; two missed `pong`s → cancel the task
  and go to `reconnecting`. (Batch 1 ships 10 s pings with no pong check;
  batch 2 adds the pong check when the Mac replies.)
- `host`/`port` in `UserDefaults`, editable in the app UI, `configure(host:port:)`
  reconnects live. Optional later: Bonjour (`_hackrice._tcp`) with the Mac
  advertising via `zeroconf`, so the field autofills.
- On `hello_ack`: adopt `tick_interval_s` into the sender, compute
  `clock_offset = server_t - now`, and if `run_id` changed, log "Mac
  restarted" and re-announce.
- `state` is an enum. **`CapturePacketSender.isConnected` reads
  `link.state == .open`** through observation. The string-prefix
  `apply(macLinkStatus:)` is deleted.

**`GlassesSession`** (new, wraps the sample's DAT code) supervises Link A:

- Desired state `streaming`. Observes `session.stateStream()`; if the state
  leaves `.started` while desired, wait 2 s and restart the session, with the
  same backoff. "Put on your glasses" / "Device unavailable" become a status
  line, not a stop.
- Frame watchdog: if no `VideoFrame` in 5 s while the stream claims to be
  running, stop and restart the stream.
- Surfaces `lastFrameAt`, `restarts`, `state` for the UI health line.

**UI health line** (one `Text` in the sample view):

```
Mac 10.135.100.6:8010 · open · 0.6 pkt/s · ack 1s ago
Glasses · streaming · last frame 0.4s ago · restarts 0
```

### 2.4 Run lifecycle (Mac)

- `run_id` = UUID per backend start; in `/api/status` and in `hello_ack`.
- `--fresh` (batch 1) deletes the DB before start.
- `POST /api/run/reset` (batch 2): between demos without a restart. Deletes
  today's ticks, episodes, decisions, insights, pending checks and scores;
  resets gate cooldowns and last-escalation time, the speech limiter, the
  `spoken` list, and the reasoner's counters; reseeds the 7-day fixtures and
  the planted HR series on the current clock; bumps `run_id`. The phone sees
  the new `run_id` on its next ping's `hello_ack`-style reply (or simply on
  reconnect) and re-announces.

### 2.5 What this deliberately does not do

- No TLS, no auth beyond the optional ingest token. LAN demo.
- No multi-phone. One active phone; the newest wins.
- No offline buffering on the phone. Drop, never queue still holds.
- No change to the tick schema or to anything downstream of the tick.

## 3. Ordered work

| Batch | Side | Work | Test without glasses? |
|---|---|---|---|
| 1 (in flight) | phone | `MacLink` reconnect + backoff, 10 s ping, `configure(host:port:)` | typecheck only; socket test with the phone, no glasses needed |
| 1 (in flight) | Mac | idle close, `ping` accepted, `health` block, `--fresh`, preflight script | yes — `tools/fake_phone.py` |
| 2 | Mac | single active `PhoneConn` (supersede), `pong` + `hello_ack` replies, `run_id`, honest `speech_status`, `/api/run/reset`, collapse speak paths | yes — fake phone, plus a pytest for supersede/idle/ack |
| 2 | phone | `hello_ack` handling (adopt interval, clock offset, run change), pong watchdog, `state` enum, sender reads link state directly | typecheck; socket test with phone |
| 3 | phone | `GlassesSession` supervisor + frame watchdog + UI health line | needs the glasses back |
| 4 (optional) | both | Bonjour discovery so the host field autofills | phone + Mac on one LAN |

Batch 2's Mac half is the highest value per hour after batch 1 lands: it
kills the ghost-socket and the lying-`spoke` problems for good and makes
between-demo resets a button. Batch 3 is written now and verified the moment
the glasses return.

## 4. How to know it works

Run these before every live demo, in this order:

1. `cd backend && uv run python scripts/preflight.py` — keys, one real call to
   each API, the `ws://` address to dial, port state.
2. Start the backend with `--fresh`. `/api/status.health.ok` is true with
   `problems: ["phone_disconnected"]` only.
3. Connect the phone. `health.phone.state` goes `connected` then `streaming`.
4. Kill Wi-Fi on the phone for 20 s, restore it. The phone status line shows
   `reconnecting`, then `open`; the Mac shows one `idle_close`, one new
   connection, and `streaming` again. Nobody touched anything.
5. `POST /api/speak {"text": "test"}`. The glasses play it; the decision or
   speak log shows `delivered`.
6. Toggle the glasses off and on. The phone shows `Glasses · restarting`,
   then `streaming`. (Batch 3.)
