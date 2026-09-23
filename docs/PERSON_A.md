# Person A — capture and audio

**Goal: `--source glasses` emits a valid §12 tick object, once per second, continuously.**

Everything below serves that one sentence. Audio is a second, later track.

## How this decomposes

The goal has two halves that meet at the capture packet (SPEC §11.2):

```
   HALF 2 (Swift)                    HALF 1 (Python)
glasses → phone → packet   ──▶   packet → frame → tick → B
```

Half 1 does not need the glasses — it can be built and finished against a folder of
JPEGs. Half 2 does not need Half 1 — it can be built against a ten-line dummy receiver
that prints packet sizes. **Build Half 1 first**, because it is where the real work is,
because Person B is blocked on its output, and because iterating on Python is ten times
faster than iterating on a signed iOS build. Then connect the two.

Track C (audio) is independent of both and comes last, since the risky half of it is
already proven.

## Where each task lives

| Task | Editor | Notes |
|---|---|---|
| A1 sample `ticks.jsonl` | VS Code | |
| A2 §9 field set | — | decision, already made |
| A3 `CaptureSource` + replay | VS Code | |
| A4 record corpus | **Xcode** | throwaway save-to-Documents in `CameraAccess` |
| A5 sensor fields | VS Code | |
| A6 tick assembly | VS Code | |
| A7 ring buffer | VS Code | |
| A8 `GET /frames` | VS Code | |
| A9 webcam adapter | VS Code | |
| A10 T0 VLM call | VS Code | |
| A11 socket echo | **both** | Swift client + plist / Python server |
| A12 sample + encode | **Xcode** | |
| A13 phone sensors | **Xcode** | |
| A14 send packet | **Xcode** | |
| A15 glasses adapter | VS Code | the Mac end of A14 |
| A16 hardcoded speak | **both** | Python sends / Swift plays |
| A17 `speak()` for B | VS Code | Swift side already covered by A16 |
| A18 ElevenLabs | VS Code | |

Eleven tasks are VS Code only, four are Xcode only, two straddle. The Xcode work is one
contiguous block (A12–A14) plus the corpus hack and the audio handler — so it can be
done in a couple of focused sessions with the glasses on, rather than context-switching
between toolchains all weekend.

**The two straddling tasks are where the bugs will be**, because a failure on one side
looks exactly like a failure on the other. A11 is the worst of them: without the two
plist keys the socket fails silently and reads as a Python bug. When either misbehaves,
prove each half separately before debugging the pair.

**The Xcode project lives outside this repo.** Nothing in it is version-controlled
alongside the Python, so any Swift worth keeping — the packet sender especially — should
be copied in or committed separately before the weekend ends.

---

## Track 0 — unblock Person B (do this first, ~1 hour)

The tick *schema* is already specified in SPEC §12 and needs no further work. What B
lacks is **data** in that shape, and a settled answer on the one part of the schema that
is admittedly unfinished. Two tasks, both small.

### A1 · Hand B a sample `ticks.jsonl`
A few hundred synthetic ticks covering a plausible session — seated, a meal, time
outdoors, a screen block — with `seq` and timestamps that actually advance.
- **Done when:** B can parse the file and drive their gate and episode builder off it.
- **Include ticks with the `ai` block absent** — roughly 1 in 3, in irregular runs, not
  every third tick. §12.2 calls gap-handling the most likely source of silent bugs in
  B's gate and episode builder. If your sample data has no gaps, B will write code that
  works perfectly until real ticks arrive.
- Don't build a shared schema class for this. §12 says B must tolerate any field being
  absent, so B should be reading dicts defensively, not importing a model that throws
  on a missing key. JSON lines is the whole interface.

### A2 · AI field set — **decided: build against §9 as written**
§9's enums were not fully reasoned through, but it is settled for now and not worth
blocking on. Take the field list exactly as specified and move.
- **Done when:** nothing — this is a decision, already made.
- Keep the field list in **one** place in your code. §9 is the part of the tick most
  likely to change once B's triggers meet real data, and the cost of that change should
  be one edit, not a search across the pipeline.
- If you do trim it later, §10's five live demo metrics are the guide: fields serving
  none of them are cost and latency on every T0 call all weekend.

---

## Track A — Half 1: frame → tick (Python, no hardware)

### A3 · `CaptureSource` interface + `replay` adapter
One interface, async iterator of `(timestamp, jpeg_bytes, optional device block)`.
Replay reads a directory of timestamped JPEGs and emits at 1 Hz wall-clock.
- **Done when:** `--source replay --dir corpus/` emits frames at a steady 1 Hz.
- Get the interface shape right here. All three adapters hang off it and a wrong shape
  costs two rewrites. Add a speed multiplier for fast iteration.

### A4 · Record the replay corpus — **with the glasses, while they work**
Scripted sequence: seated → food appears → go outdoors → sit at a screen. 512px, q70.
- **Done when:** a directory of timestamped JPEGs exists and B has a copy.
- **Shoot this through the glasses, not a webcam.** A webcam corpus has the wrong field
  of view, the wrong mounting angle, no head motion, and different auto-exposure
  behaviour — and every threshold you tune against it has to be retuned later. DAT
  availability is transient (hardware_software.md §24), so record while the session is
  known good rather than assuming it will be tomorrow.
- Cheapest path: add a save-to-Documents call to the working `CameraAccess` build and
  pull the files off with AirDrop. Throwaway code, ~30 minutes, no networking involved.
  If A11–A14 are already done, skip that and just dump received packets on the Mac.
- Record more than you think you need, and record the sequence twice. Re-shooting costs
  a charge cycle and another window of working DAT.

### A5 · Sensor field computation
numpy over the decoded frame: `lux_proxy`, `cct`, `hist_spread`, `frame_delta`,
`flow_mag`, `sharpness`, `phash`. Always present (§12.1).
- **Done when:** all seven populate on every frame and the whole block is timed under
  ~5 ms. Print the timing; do not assume it.
- Must be synchronous and bounded — this is invariant 1. `flow_mag` is the one that
  will blow the budget; SPEC §11.5 says OpenCV "only if optical flow is actually
  needed," so treat it as optional from the start.
- **`phash` deserves real attention.** B selects the four escalation frames by phash
  distance (§4.3, §12.3), so a weak hash silently degrades T1's input with no error
  anywhere. Test it directly: near-identical frames → small Hamming distance, scene
  change → large.
- Name things honestly. `lux_proxy` is relative luminance from an auto-exposed JPEG,
  not lux, and nothing downstream may treat it as lux.

### A6 · Tick assembly and emission
Assign `tick_id` and `seq`, assemble the blocks, emit to B, mirror to SQLite.
- **Done when:** ticks arrive at a steady 1 Hz with no `seq` gaps and no clock drift
  over 15 minutes.
- Drive the loop off a monotonic clock target, not `sleep(1)` — `sleep(1)` in a loop
  accumulates drift and you will notice at minute twelve, not minute one.
- **Decision to settle with B (see below):** how B receives ticks.

### A7 · Frame ring buffer + 90 s TTL
`frame_ref` → jpeg bytes, in RAM, evicting past 90 s. ~90 entries, ~3.5 MB.
- **Done when:** steady-state size holds around 90 frames and memory is flat over a
  15-minute run.
- **T0 never writes a frame to disk** (§2.5). The only way a frame survives is being
  copied out at escalation. This is also the privacy story you tell judges.

### A8 · `GET /frames?refs=…`
Fetch frames by ref for escalation. Expired ref → 410, logged not crashed.
- **Done when:** B can fetch four refs and get bytes back.
- Return base64 JSON rather than multipart — B is base64-ing them into the Claude call
  anyway (§4.3), so this saves them a conversion.

### A9 · `webcam` adapter
- **Done when:** `--source webcam` produces identically shaped ticks, minus `device`.
- This proves the entire Python pipeline end to end while the iOS side is still being
  provisioned (§13.4).

### A10 · T0 VLM call, budget, and drop rule
Gemini Flash-Lite, structured output over the §9 field set, 1 s budget, self-scheduling.
- **Done when:** measured AI coverage sits in the 50–80% band, over-budget ticks carry
  **no** `ai` block (never a stale one, §11.7), and the tick stream holds exactly 1 Hz
  while the API is slow.
- **Test this by injecting a 3-second artificial delay into the call.** If the tick
  stream stutters, invariant 1 is broken. This is the single most likely thing to be
  quietly wrong in your whole half of the system, and it will not announce itself.
- Fire the next call when the previous returns or its budget expires — **not on a fixed
  timer** (§5.3). A timer accumulates backlog under API slowness; self-scheduling
  degrades gracefully.
- Log the coverage percentage. It is both a health metric and a good demo talking point.

---

## Track B — Half 2: glasses → packet (Swift)

Base is your working `CameraAccess` build. Everything here is additive.

### A11 · Prove the socket before adding payload
Add `NSLocalNetworkUsageDescription` and `NSAllowsLocalNetworking`, open a
`URLSessionWebSocketTask` to the Mac, echo a string both ways.
- **Done when:** the Mac prints a string sent from the phone, and vice versa.
- Do this as its own task. Without those two plist keys the socket fails **silently**,
  in a way that reads exactly like a backend bug, and you will debug the wrong half of
  the system for an hour. Use the Mac's LAN IP, not localhost.

### A12 · Sample and encode frames
Retain the latest `VideoFrame`, sample at 1 Hz, `makeUIImage()` → resize 512px → JPEG q70.
- **Done when:** ~40 KB JPEGs are produced at 1 Hz while the stream runs at 2 fps.
- Discard the second frame each second. Do not queue frames behind anything.

### A13 · Phone sensors
CoreMotion accelerometer, CoreLocation speed, attached to each packet.
- **Done when:** plausible values ride along in the packet.
- The phone computes nothing from them (§11.2) — raw values only, `accel_rms` is
  derived on the Mac.

### A14 · Assemble and send the capture packet
- **Done when:** the Mac receives well-formed packets at ~1 Hz, ~40 KB/s.

### A15 · `glasses` adapter + `device` fields
WebSocket ingest on the Mac, packet decode, `accel_rms` from the accel vector,
`gps_speed` passthrough.
- **Done when:** `--source glasses` produces full ticks with the `device` block present.
- **This is the goal.** Everything before it is scaffolding.

---

## Track C — audio return path

### A16 · Hardcoded string, laptop → phone → glasses
Mac sends `{"type": "speak", "text": "..."}` over the same socket; phone speaks it with
AVSpeechSynthesizer.
- **Done when:** typing a string on the Mac produces speech in the glasses.
- Lower risk than SPEC §13.4 implies, because you have already validated the hard part:
  AVSpeechSynthesizer out the Ray-Ban speakers, concurrent with camera streaming (§29).
  All that is new here is the message trigger.

### A17 · `speak(text, urgency)` for B
The in-process function B calls (§13.3). B decides *whether* and *what*; A owns *how*.
The rate limiter stays on B's side — it is logic, not plumbing.

### A18 · ElevenLabs (optional upgrade)
Generate audio on the Mac, ship bytes over the socket, stream rather than waiting for
the full file (§11.6).
- Add it **alongside** the AVSpeechSynthesizer path behind a flag. Never break the
  working fallback to chase better voice quality the night before judging.

---

## Decisions to settle with B in the first thirty minutes

1. **How B receives ticks.** In-process async callback, SQLite polling, or WebSocket
   push? Both halves are Python on one laptop, so an in-process subscription is
   simplest, with SQLite as the durable mirror. Confirm before A6.
2. **`GET /frames` response format.** Base64 JSON recommended, per A8.
3. **Whether `flow_mag` is actually used.** If B's triggers don't read it, drop it and
   save the OpenCV dependency and the milliseconds.

## Cut list, in the order to cut

`flow_mag` → ElevenLabs (A18) → SQLite tick mirror (in-memory survives 15 minutes) →
`webcam` adapter, but only if the glasses path is fully working first.

Never cut: the drop rule (A10), the 90 s TTL (A7), or gap-bearing sample data (A1).

## Two things that are nobody's task and will bite

- **Charging the glasses.** Continuous streaming drains them in well under an hour.
  Whoever holds them keeps them charged and powered off until judging.
- **Free provisioning expires after seven days.** A reinstall late Saturday can hit a
  signing prompt at the worst possible moment.
