# Architecture spec

Lifestyle tracking on Ray-Ban Meta glasses. §1–§6 cover the processing
architecture — capture, tiering, storage, and the escalation model. §7–§13 cover
metric sources, reference thresholds, the T0 field set, scoring, tech stack, the
tick schema, and the two-person work split.

---

## 1. Overview

Three layers, decoupled clocks.

| Layer | Cadence | Cost | Blocking |
|---|---|---|---|
| **T0 — Tick producer** | every 1.5 s, always | Free + one small VLM call | Never |
| **Trigger gate** | Every tick | Free (plain code) | Never |
| **T1 — Reasoner** | On escalation only, ~20×/day | One LLM call | Async |

T0 runs unconditionally and produces one timestamp object per second. The trigger
gate inspects each tick and decides whether to escalate. T1 is the only component
that reasons, and the only one that can act.

Nothing in this pipeline queues. Every stage drops rather than backs up.

---

## 2. T0 — Tick producer

### 2.1 Responsibility

Emit exactly one timestamp object per second, forever, regardless of what any
other layer is doing. The tick stream is the system's ground truth — everything
downstream reads from it and nothing writes back into it.

### 2.2 Capture

- Camera streams at 2 fps via the DAT SDK (`StreamConfiguration`, valid frame
  rates are 2/7/15/24/30).
- T0 samples the most recent frame once per **tick interval** — **1.5 s** as
  shipped (`TICK_INTERVAL_S`, default 1.5), chosen so the VLM call usually
  returns inside the interval. Frames captured between samples are discarded.
  Everything downstream is keyed on `tick.t` seconds; the only cadence-aware
  values are the hit-count thresholds in §3, which are scaled from their 1 Hz
  reference values by `Timings.scaled_hits()`.
- Sampling and encoding happen **on the phone** (§11.3). The selected DAT
  `VideoFrame` is converted to a ~512px JPEG at quality ~70 (~40 KB) immediately
  and the original frame buffer is discarded. The underlying pixel format is an
  SDK implementation detail the architecture does not depend on.

### 2.3 Tick fields

Two groups, populated by different mechanisms:

**Non-AI fields** — computed locally from the frame buffer and phone sensors.
Always present, ~5 ms, zero cost. Includes pixel statistics (luminance, colour
temperature, histogram spread, frame delta, optical flow, sharpness, perceptual
hash) and phone sensors (accelerometer, GPS speed). No on-device ML — see §9.

**AI fields** — populated by a small VLM (Gemini Flash-Lite class) returning a
fixed set of booleans and enums. Carries `as_of` and `age_ms`.

### 2.4 The AI field timing rule

The VLM call is fired per tick but **not awaited**. Budget is 1 second.

- Returns under budget → AI fields populated on that tick.
- Exceeds budget → **the result is dropped**, the tick is still written with AI
  fields absent, and the next tick fires a fresh call immediately.
- A call in flight does not block the next tick's capture. New frames never queue
  behind an in-flight call — a stale frame has negative value.

Ticks between successful AI results may carry the last known AI values with an
incremented `age_ms`, or leave them null. Either is acceptable; the consumer must
check `age_ms` regardless.

**Expected coverage: 0.5–0.8 Hz of AI-populated ticks against a continuous 1 Hz
tick stream.** Flash-Lite typically returns in 300–600 ms, but the p99 tail
exceeds 2 s. The drop rule exists specifically to absorb that tail.

### 2.5 Storage and TTL

| Artifact | Location | TTL | Size |
|---|---|---|---|
| **Frames** | Ring buffer, **laptop RAM** | **90 seconds**, then evicted | ~40 KB each, ~3.5 MB total |
| **Ticks** | In-memory + SQLite mirror | Session (demo) / downsampled after 1 h (production) | ~300 bytes each |
| **Escalated frames** | Copied out of ring on escalation → durable store | Persistent | 4 frames per escalation |
| **Insights** | Durable store | Persistent | One row each |

Three rules govern this:

1. **Frames are never written to disk from T0.** The ring buffer is the only place
   a frame exists, and it lives 90 seconds. The window is sized by what escalation
   needs for context, nothing more.
2. **The only path by which a frame survives** is being copied out at escalation
   time. Everything else is gone in 90 seconds.
3. **Ticks contain no pixels.** They persist freely because they are tiny —
   a 15-minute demo produces ~900 ticks, under 300 KB.

This retention policy is also the privacy answer: raw imagery has a 90-second
lifetime in volatile memory and is never persisted except as consented evidence
attached to a logged event.

**Production note (not built for demo):** tick storage grows at ~26 MB/day
uncompressed. Downsample ticks older than one hour to 1/minute, retaining the
full-rate stream only inside escalation windows.

---

## 3. Trigger gate

Plain code. No model. Runs on every tick.

Reads the tick stream and decides whether to escalate to T1. Triggers are
condition-based and evaluated against the current tick plus recent tick history —
e.g. food present in frame, stillness sustained across N ticks, screen present
across N ticks.

Requirements:

- **Configurable per user**, with a shipped default set.
- **Stateful** — triggers evaluate over a window of ticks, not a single tick.
- **Debounced and rate-limited** so that a sustained condition escalates once
  rather than every second.
- **Drops on contention** — if T1 is busy, the escalation is dropped and logged,
  never queued.
- Passes the firing trigger's identity to T1. The reasoner should know why it was
  woken rather than inferring it.

Trigger definitions and tuning parameters are deferred.

---

## 4. T1 — Reasoner

### 4.1 Responsibility

One LLM call per escalation. Receives frames and context, returns interpretation
and decision **in a single structured response**. Not split into a perception call
followed by a decision call — splitting doubles latency and forces the decision to
work from a flattened prose description instead of the frames themselves.

### 4.2 Context envelope

Five parts, ordered by volatility so the stable prefix is cacheable:

**Stable prefix** (changes at most daily — cache this):
1. User personality and preference details
2. 7-day summary — trends and baselines
3. Objective and available action list

**Dynamic suffix** (changes per call):
4. Today's summary — accumulated from prior escalations
5. Recent frame window

### 4.3 Constructing part 5

Do not send the full frame window. Send:

- **A compact text table of tick fields** across the window. Cheap, and it carries
  the temporal shape — light dropped, motion stopped, screen appeared.
- **4 JPEG images** subsampled from the window, inline base64 in the same request.
  No Files API upload; frames are single-use and the upload round trip is wasted
  latency.

**Interleave text and images chronologically, oldest first.** Each image is
immediately preceded by a text label stating its timestamp and key tick fields.
The trigger frame goes last, closest to the question. A block of images followed
by a block of text gives the model no way to bind a frame to the tick it came from.

```
[text]  Trigger: food_in_frame at 12:31:04
[text]  Tick table (last 60s): <rows>
[text]  t-45s — motion 0.4, lux 210, seated
[image] <base64>
[text]  t-30s — motion 0.1, lux 205, seated
[image] <base64>
[text]  t-15s — motion 0.1, lux 205, seated
[image] <base64>
[text]  t-0s (trigger frame)
[image] <base64>
[text]  Decide the action.
```

**Subsample by change, not by even time spacing.** Select the frames where the
perceptual hash moved most across the window. Even spacing across a motionless
50 seconds yields four identical frames and misses the moment something happened.

Four images is the right number — the fifth adds latency and little information.

### 4.4 Actions

The response selects from a fixed set. Actions are **not mutually exclusive** —
one call may annotate and watch, or speak and log an insight.

| Action | Effect |
|---|---|
| `speak` | Utterance delivered via TTS, with urgency level |
| `log_insight` | Persistent record, feeds daily and weekly reports |
| `annotate` | One-line memory entry appended to today's summary |
| `watch` | Schedule a re-check in N minutes or on condition X |
| `nothing` | No action |

**`watch`** is a row in a pending-checks table that the trigger gate polls. It
lets the reasoner schedule its own follow-up rather than waiting for a trigger to
fire again, which turns it from purely reactive into something holding an
intention across time.

### 4.5 The write/speak asymmetry

**Always write, rarely speak.**

`annotate` is near-mandatory. Every escalation produces a memory line even when
the verdict is "nothing worth saying" — that line is what accumulates into part 4
of the next call's own context envelope. If skipped escalations write nothing, the
day summary develops holes exactly where the interesting moments were.

`log_insight` is for things worth surfacing in a report. `speak` is the rare one.

### 4.6 Speech gating

The model proposes speech; code disposes. A `speak` action passes through a rate
limiter before reaching TTS. The model never drives the speaker directly.

### 4.7 Episode identity

**Open question.** When a condition persists across many ticks — a 20-minute meal
— either:

- **(a)** the trigger gate suppresses re-escalation for the duration, or
- **(b)** T1 is told it already annotated this episode and decides for itself.

(a) is cheaper and more predictable. (b) lets the reasoner notice mid-episode
change. Decide before implementing the gate.

---

## 5. Concurrency rules

These are invariants, not optimisations.

1. **T0 never blocks.** Capture and non-AI field computation are synchronous and
   bounded. Nothing downstream can stall the tick stream.
2. **Drop, never queue.** At every stage. A queued frame is a stale frame, and a
   growing queue in a real-time system is a death spiral.
3. **The T0 VLM is self-scheduling.** Fire the next call when the previous returns
   or its budget expires, using the newest available frame — not on a fixed timer.
   Throughput then degrades gracefully under API slowness instead of accumulating
   backlog.
4. **T1 concurrency is capped.** Escalations arriving against a busy reasoner are
   dropped and logged.

---

## 6. Demo scope

Explicitly not built:

- Crash recovery, offline queue, sync conflict resolution. In-memory state plus
  SQLite. The system must survive 15 minutes, not 16 hours.
- Real 7-day history — seed it with synthetic data containing a pattern worth
  finding.
- Tick downsampling and long-horizon storage management.
- Cost optimisation.

Explicitly built despite looking optional:

- **Silent decisions are logged to the dashboard.** A system that correctly says
  nothing 90% of the time looks broken on stage. A visible feed of
  `12:31 · salad · healthy · no action` makes the reasoning legible during silence.
- **`DEMO_MODE` flag** shortening every cooldown and rate limit. Production timings
  will not let three triggers fire inside a four-minute demo.

---

## 7. Metric sources

Every metric the system scores comes from exactly one of two sources. The
scoring layer and dashboard do not distinguish between them.

| Source | What it is | Demo status |
|---|---|---|
| **Live** | Derived from the tick stream — VLM tags plus non-AI fields, aggregated into episodes (§10) | Built and running |
| **Seeded** | Phone / wearable / WHOOP integration data | Assumed to exist; **hardcoded** synthetic rows, including the 7-day pattern worth finding |

Rule: anything the camera can see is live. Anything it cannot is seeded. Nothing
is faked on the live side — if a VLM tag can't support a metric honestly, that
metric is seeded and labelled as such.

| Metric | Source | Derivation |
|---|---|---|
| Daytime light dose | Seeded | **Not derived from camera in the demo.** Absolute lux is not recoverable from an auto-exposed JPEG; treat as a wearable/phone integration metric |
| Evening light | Seeded | Same — out of demo scope on the live side |
| Nature dose | Live | Outdoor + `vegetation_visible` or `scene ∈ {park, trail}`, summed to weekly minutes |
| Screen / work hours | Live | `screen_present` sustained across ticks, integrated to hours |
| Social integration | Live | `people_present` sustained over a window → conversation episodes per day |
| Diet pattern | Live | `food_present` + `food_type` enum; T1 tags meals against a Mediterranean pattern |
| Caffeine cutoff | Live | `caffeine_visible` timestamp vs. seeded bedtime − 9 h |
| Alcohol | Live | `alcohol_visible` timestamp; nightly HRV drop comes from the seeded side |
| Resistance training | Live | `scene = gym` + `activity = exercising`, duration and session count |
| Sauna / cold plunge | Live | `scene ∈ {sauna, cold_plunge}` + duration. Cold plunge scores ~0 per §8 |
| Activity state | Live | `activity` enum, used as context for every other episode |
| Sleep duration, SRI | Seeded | Wearable sync |
| HRV recovery | Seeded | Wearable sync |
| Steps, VILPA, gait speed | Seeded | Phone pedometer / accelerometer |
| Night noise | Seeded | Phone mic |
| Balance, breathwork, purpose | Seeded | User-logged / questionnaire |

The wearable-only subset (WHOOP, Oura, Apple Watch), with device, native
resolution, and seeded shape, is specified in §14.

---

## 8. Reference thresholds

The numbers scoring is coded against. Grade is evidence quality (A strongest).

| Layer | Metric | Target | Source / grade |
|---|---|---|---|
| Light | Daytime melanopic EDI | ≥250 lux sustained daily dose; system target ≥30 min outdoors or bright-band time before 10:00 | Brown et al. 2022 PLOS Biology (consensus); Windred 2024 PNAS — A |
| Light | Evening (3 h pre-bed) | ≤10 lux melanopic | Brown 2022 — A |
| Sleep | Duration | 7–9 h (U-shaped risk) | Multiple cohorts — A |
| Sleep | Regularity (SRI) | Top quintiles ≈ 20–48% lower all-cause mortality; regularity beat duration. System target SRI ≥80 (bed/wake within ±30 min) | Windred 2024 Sleep (UK Biobank, n=60,977) — A |
| Movement | Steps | ~7,000/day meaningful; plateau ~8,000–10,000 under 60, ~6,000–8,000 over 60 | Paluch 2022 Lancet Public Health — A |
| Movement | VILPA | 3–4 min/day of vigorous bursts ≈ 26–30% lower all-cause mortality | Stamatakis 2022 Nature Medicine — A |
| Movement | Resistance training | 30–60 min/week, 2 sessions; 10–17% lower mortality; benefit fades above ~130 min/wk | Momma 2022 BJSM — A |
| Movement | Gait speed | ≥1.2 m/s good; each +0.1 m/s ≈ 12% lower mortality | Studenski 2011 JAMA — A |
| Movement | Balance | 10-s one-leg stand; failure → mortality HR 1.84 (ages 51–75) | Araujo 2022 BJSM — B |
| Social | Integration | Stronger ties → survival OR 1.50; complex integration OR 1.91; isolation OR 1.29, loneliness 1.26 | Holt-Lunstad 2010 / 2015 — A |
| Nature | Weekly dose | ≥120 min; peak 200–300 min; pattern doesn't matter | White 2019 Sci Rep — B |
| Heat | Sauna | 2–3×/wk moderate benefit; 4–7×/wk ~40% lower all-cause mortality; sessions >19 min | Laukkanen 2015 JAMA IM — B (single male cohort) |
| Cold | Cold plunge | No healthspan evidence; log it, score it near zero, say so | — C |
| Stress | Recovery adequacy | 7-day ln RMSSD ≥ 60-day baseline; flag if below by >1 SD for 3+ days | Standard HRV practice — B |
| Stress | Work hours | ≥55 h/week associated with higher stroke/IHD mortality | WHO/ILO 2021 — A |
| Stress | Breathwork | 5 min/day cyclic sighing improves mood, lowers respiratory rate (no HRV change) | Balban 2023 Cell Rep Med — B |
| Diet | Pattern | Mediterranean pattern ≈ 30% fewer major CV events | PREDIMED (republished 2018) — A |
| Diet | Caffeine cutoff | Caffeine 6 h before bed cut sleep by >1 h; system cutoff = bedtime − 9 h (CYP1A2 slow: −12 h) | Drake 2013 J Clin Sleep Med — B |
| Diet | Alcohol | No safe level; nightly HRV drop visible in WHOOP the same night | Zhao 2023 JAMA Netw Open — A |
| Noise | Night | <45 dB Lnight | WHO 2018 — A |
| Purpose | Life purpose | Lowest vs highest purpose → mortality HR 2.43 | Alimujiang 2019 JAMA Netw Open — B |

---

## 9. T0 AI field set

The fixed set of VLM outputs referenced in §2.3, sized to cover every live
metric in §7. Booleans default to false; enums include `unknown`.

| Field | Type | Values |
|---|---|---|
| `scene` | enum | `home`, `office`, `restaurant`, `gym`, `sauna`, `cold_plunge`, `park`, `trail`, `vehicle`, `street`, `unknown` |
| `activity` | enum | `seated`, `standing`, `walking`, `exercising`, `eating`, `unknown` |
| `food_present` | bool | |
| `food_type` | enum | `vegetables`, `fruit`, `grains`, `fish`, `poultry`, `red_meat`, `processed`, `sweets`, `mixed`, `none` |
| `caffeine_visible` | bool | Coffee, tea, energy drink in frame |
| `alcohol_visible` | bool | |
| `screen_present` | bool | |
| `vegetation_visible` | bool | |
| `people_present` | bool | |

Non-AI fields remain as in §2.3, computed in Python from the frame buffer (§11).

**Changed from an earlier draft:** the phone does no image processing under the
§11 topology, so on-device ML (Vision face detection, OCR) is not used.
`people_present` is VLM-only with no cross-check, and screen detection relies on
the `screen_present` tag rather than OCR text density. Both are acceptable; note
the reduced confidence when tuning triggers.

Phone **sensors** are unaffected — accelerometer and GPS speed ride along in the
capture packet (§11.3) at no cost and populate the tick's `device` block.

---

## 10. Episodes and scoring

Scoring is computed from **episodes**, not ticks. A fourth component sits
between the tick stream and the score:

- **Episode builder.** Runs alongside the trigger gate, over the same tick
  window. Collapses runs of ticks with a stable tag into one row: `meal`,
  `conversation`, `outdoor_block`, `screen_block`, `gym_session`, `sauna_session`.
  Each carries start, end, duration, and the dominant tag values. Uses the same
  debounce parameters as the gate so an episode and its escalation agree on
  boundaries.
- **Scorer.** Reads episodes (live) and seeded integration rows, evaluates both
  against §8 thresholds with the same code path, and emits per-metric scores
  plus a daily and weekly rollup. Metrics with proxy derivation are scored on
  the proxy target (e.g. minutes outdoors before 10:00, not lux).
- **Dashboard** reads scores and episodes. It labels each metric `live` or
  `seeded` but otherwise treats them identically.

T1's `log_insight` output feeds the same reports but is not a scoring input;
scores are deterministic from episodes and seeded rows.

**Demo metric set.** Five live metrics that a camera can visibly trigger inside
a four-minute demo: nature minutes, social episodes, screen hours, meal tagging
with the caffeine cutoff, and alcohol sightings. Everything else scores from
seeded rows.

---

## 11. Tech stack

### 11.1 Topology

**The phone is a glasses adapter. The laptop is the system.** Swift does capture
and playback; Python does all intelligence.

```
Ray-Ban Meta glasses
      │  camera stream (DAT)
      ▼
iPhone app  (Swift, MWDATCore + MWDATCamera)
      │  capture packet over Wi-Fi / LAN
      ▼
Laptop  (Python — T0, ring buffer, VLM, gate, episodes, T1, SQLite, dashboard)
      │  utterance audio
      ▼
iPhone app
      │  AVAudioSession → Bluetooth A2DP
      ▼
Ray-Ban speakers
```

**Audio returns through the phone.** The glasses are Bluetooth-bonded to the
phone — that bond is what the Meta AI app and DAT require — so they are not
independently available as a laptop audio device. TTS bytes generated in Python
travel back over the same socket and play through `AVAudioSession`.

This is why the DAT SDK cannot be a Python capture adapter: it is a native mobile
SDK. Glasses mode requires the iOS bridge. Webcam and replay modes read directly
on the laptop.

At 1 Hz and ~40 KB per JPEG the uplink carries ~40 KB/s — negligible on LAN.

### 11.2 Capture packet

One message per tick, phone → laptop:

```json
{
  "t": 1789200000.25,
  "image": "<jpeg bytes>",
  "gps_speed": 0.4,
  "accel": { "x": 0.01, "y": -0.12, "z": 0.98 }
}
```

Sensor fields are cheap on the phone and expensive to reconstruct anywhere else,
so they ride along. The phone computes nothing from them.

### 11.3 Capture adapters

T0 reads from a `CaptureSource` interface. Three implementations, `--source`:

| Adapter | Use |
|---|---|
| `glasses` | WebSocket server receiving capture packets from the iOS bridge. **The demo path.** |
| `webcam` | Local camera index. No packet, no sensor fields. Development without glasses. |
| `replay` | Directory of timestamped JPEGs. Deterministic debugging. |

`replay` is not optional — record one scripted walk early (seated → food appears →
outdoors → screen) and develop against it. It is the only way two people iterate
in parallel without passing the glasses back and forth.

### 11.4 iPhone bridge (thin)

| Concern | Choice |
|---|---|
| Platform | iOS (Swift), deployment target 17.0 |
| Glasses camera | Meta Wearables DAT SDK via SPM — `MWDATCore`, `MWDATCamera` |
| Requirements | Meta AI app v254+, glasses firmware v20+, Developer Mode on |
| Frame rate | `StreamConfiguration` at 2 fps (valid: 2/7/15/24/30) |
| Encode | Downscale to 512px, JPEG q70 |
| Sensors | Core Motion accelerometer, Core Location speed |
| Transport | `URLSessionWebSocketTask`, bidirectional |
| Audio out | `AVAudioSession` → Bluetooth A2DP → glasses speakers |

Total responsibility: sample, encode, send, play. No ticks, no VLM, no logic.

**Camera and audio use different paths.** Camera comes through DAT; microphone and
speaker on standard Ray-Ban Meta glasses use the normal iOS Bluetooth audio
profiles. Don't look for audio APIs in the DAT SDK.

Use `MWDATMockDevice` to develop without the glasses on. The DAT repos ship agent
skills (`install-skills.sh`) and an MCP docs server at
`https://mcp.developer.meta.com/wearables` — wire those in before writing SDK code.

**iOS gotchas, in order of time they will cost you:**

1. **You need a Mac with Xcode.** Confirm before committing to this path.
2. **`NSLocalNetworkUsageDescription`** in Info.plist — without it the WebSocket to
   the laptop fails silently in a way that looks like a backend bug.
3. **`NSAllowsLocalNetworking`** under `NSAppTransportSecurity` — plain `ws://` to
   the laptop is blocked by default.
4. **`NSCameraUsageDescription`** — app crashes on launch without it.
5. **`AVAudioSession` category** must be `.playback` with `.allowBluetoothA2DP`, or
   speech routes to the phone speaker instead of the glasses.
6. **Free provisioning expires after 7 days.** Fine for the weekend, but a
   reinstall late Saturday can hit a signing prompt.
7. **Background suspension** — keep the app foregrounded during the demo.

### 11.5 Laptop

| Concern | Choice |
|---|---|
| Runtime | Python 3.11 |
| Server | FastAPI + uvicorn (WebSocket both directions, REST for dashboard) |
| Pixel math | numpy; OpenCV only if optical flow is actually needed |
| Storage | SQLite |
| HTTP client | httpx (async) |
| TTS | ElevenLabs call in Python; audio bytes returned over the socket |
| Dashboard | Next.js, reads the FastAPI service |

### 11.6 Models

| Role | Model | Notes |
|---|---|---|
| T0 AI fields | Gemini Flash-Lite | Structured output; 1 s budget, drop on overrun |
| T1 reasoner | OpenAI GPT (Responses API) | Structured JSON output, interleaved multi-image input |
| TTS | ElevenLabs Flash v2.5 | Lowest-latency tier; stream, don't wait for the full file |

### 11.7 Settled decisions

| Decision | Resolution |
|---|---|
| Episode identity (§4.7) | **(a)** gate-side suppression. The "already annotated" hint to T1 can be layered on later without changing the gate. |
| Dropped-VLM ticks | Tick is written with the `ai` block **absent**, not carried forward stale. |
| Audio routing | Laptop → phone → Bluetooth → glasses. Never laptop → glasses. |

---

## 12. Tick schema

The contract between the two halves of the system. Person A produces these;
Person B consumes them and touches nothing upstream. A may add fields freely;
B must tolerate any field being absent.

```json
{
  "v": 1,
  "tick_id": "t_00001742",
  "t": 1757700842.000,
  "seq": 1742,

  "sensor": {
    "lux_proxy": 340,
    "cct": 4100,
    "hist_spread": 0.62,
    "frame_delta": 0.12,
    "flow_mag": 0.04,
    "sharpness": 88,
    "phash": "e3a91c04b7d2f855"
  },

  "device": {
    "accel_rms": 0.04,
    "gps_speed": 0.2
  },

  "ai": {
    "as_of": 1757700840.100,
    "age_ms": 1900,
    "scene": "office",
    "activity": "seated",
    "food_present": false,
    "food_type": "none",
    "caffeine_visible": true,
    "alcohol_visible": false,
    "screen_present": true,
    "vegetation_visible": false,
    "people_present": true,
    "conf": 0.83
  },

  "frame_ref": "f_00001742"
}
```

### 12.1 Guarantees

| Group | Source | Guarantee |
|---|---|---|
| `sensor` | numpy over the frame buffer, laptop-side | **Always present** |
| `device` | Phone sensors via capture packet (§11.2) | **Absent** under `webcam` / `replay` |
| `ai` | Gemini Flash-Lite (§9) | **May be absent** — see 12.2 |

`lux_proxy` is a relative luminance figure from an auto-exposed JPEG, **not
absolute lux**. It is usable for indoor/outdoor and bright/dim discrimination
and for nothing else. Scoring uses the proxy targets in §7, never a lux threshold.

### 12.2 The `ai` block contract

Two states B must handle:

1. **Present** — `age_ms` tells you how stale. Treat confidence as decaying with
   age; a 4 s-old `food_present` is weaker evidence than a 200 ms-old one.
2. **Absent** — the VLM call for this tick overran its 1 s budget and was dropped.
   The tick is still valid and `sensor` is unaffected.

Expect roughly 50–80% of ticks to carry an `ai` block. **Any trigger or episode
boundary written against `ai` fields must therefore tolerate gaps** — evaluate
over windows of ticks, never a single tick. This is the most likely source of
silent bugs in the gate and the episode builder.

### 12.3 `frame_ref`

An opaque handle into the laptop's ring buffer, valid for **90 seconds** from the
tick's timestamp. Used at escalation to fetch frames for the T1 call:

```
GET /frames?refs=f_00001738,f_00001740,f_00001741,f_00001742
```

An expired ref returns 410. Since escalation happens within seconds of the
triggering tick this should never occur — log it rather than crash, because it
means the pipeline has fallen behind.

Frame selection by perceptual-hash distance (§4.3) is computed by B from
`sensor.phash` in the tick history, keeping selection logic with the rest of the
consumer code.

---

## 13. Work split

Two people. **The seam is the tick stream.** A produces ticks and owns everything
upstream of them plus the audio return path; B consumes ticks and owns everything
downstream. Neither touches the other's side.

### 13.1 Person A — capture and audio (the glasses path)

Owns, exclusively:

- iOS bridge: DAT session, camera stream, JPEG encode, sensor collection,
  WebSocket both directions, `AVAudioSession` playback to the glasses
- `CaptureSource` interface and all three adapters (`glasses`, `webcam`, `replay`)
- WebSocket ingest on the laptop; capture packet decode
- Frame ring buffer (laptop RAM) and its 90 s TTL
- `GET /frames` endpoint
- All `sensor` and `device` field computation
- T0 VLM call, its structured-output schema, the 1 s budget and drop rule
- Tick assembly and emission
- ElevenLabs call and delivery of audio bytes to the phone
- **HealthKit forwarding** from the iOS bridge (§15.1) — Apple Watch samples
  posted to B's wearable ingest endpoint
- Recording the `replay` corpus (do this first — B needs it)

Does not touch: triggers, episodes, T1, scoring, dashboard, or what gets said.

### 13.2 Person B — reasoning and product

Owns, exclusively:

- Trigger gate: definitions, debounce, rate limits, contention drops
- Episode builder
- T1 reasoner: context envelope assembly, frame subsampling by phash, prompt,
  structured response handling
- Action handling: `annotate`, `log_insight`, `watch` (pending-checks table),
  `nothing`
- Speech rate limiter — decides *whether* an utterance is emitted
- Scorer and §8 threshold logic
- Seeded data: synthetic rows plus the 7-day pattern
- SQLite schema for episodes, insights, scores
- Next.js dashboard, including the silent-decision feed

Does not touch: the iOS app, capture, the ring buffer, `sensor`/`device` fields,
the T0 VLM, TTS, or audio transport.

### 13.3 The two interfaces between them

Everything crossing the seam is one of these. Fix both in the first thirty
minutes, then work independently.

1. **Tick object (§12)** — A emits, B consumes. Plus `GET /frames?refs=…` for
   escalation.
2. **Utterance handoff** — B calls an in-process function A owns,
   `speak(text, urgency)`. A synthesizes, ships the bytes to the phone, and plays
   them. B decides *whether* and *what*; A owns *how*. The rate limiter stays on
   B's side because it is logic, not plumbing.

### 13.4 Unblocking

**A's first deliverable is the `replay` corpus, not the iOS app.** Record a
scripted sequence with any camera — seated, food appears, go outdoors, sit at a
screen — and hand B a directory of timestamped JPEGs plus a script that replays
them as ticks at 1 Hz. B then builds the entire downstream system against
deterministic input and never touches Swift.

Build the `webcam` adapter before the `glasses` adapter for the same reason: it
proves the whole Python pipeline end to end while the iOS app is still being
provisioned.

For audio, A should get **laptop → phone → glasses playback working with a
hardcoded string** before the reasoner exists. It is the second-riskiest path in
the system and it is trivially testable in isolation.

### 13.5 Unowned work — assign explicitly

Neither role covers these, and they are how two-person teams lose:

- **Demo video and Devpost writeup.** Assign to B — the dashboard is what gets
  filmed and B will have working footage first. Record Saturday night regardless
  of how finished it feels.
- **Demo script and rehearsal.** Both. `DEMO_MODE` cooldowns (§6) need tuning
  against the actual script or the triggers won't fire inside four minutes.
- **Charging the glasses.** Continuous streaming drains them in well under an
  hour. Whoever holds them owns keeping them charged and off until judging.

---

## 14. Wearable biometrics (seeded)

The metrics in this section can only be collected by a wearable. The camera
cannot see them and the phone cannot infer them. Per the §7 rule they are
**hardcoded** for the demo, labelled `seeded`, with `source` set to the device
that would actually provide them — **except where §15 connects a real device.**
The two devices we actually own and connect are an Apple Watch and a Fitbit;
§15 defines those live paths. Seeded rows remain the fallback for any metric
no connected device supplies. Three devices are named because each has a
public API and each is the strongest source for something the others are not:

| Device | Best at | Integration path |
|---|---|---|
| **WHOOP** | Nightly HRV and recovery, strain, journal tags (alcohol, caffeine, nicotine, cannabis) | WHOOP API v2 (REST, OAuth) |
| **Oura Ring** | Sleep staging, readiness, skin temperature deviation, daytime HR every 5 min | Oura API v2 (REST, OAuth) |
| **Apple Watch** | Intraday HR at 1 Hz during workouts, workouts with pace and HR zones, VO2 max, walking steadiness, time in daylight | HealthKit on the phone, forwarded by Person A's iOS bridge |

### 14.1 Metric set

| Metric | Device | Native resolution | Seeded shape | Feeds |
|---|---|---|---|---|
| Overnight HRV (ln RMSSD) | WHOOP, Oura | one per night | one value per day, plus a `hrv_rmssd_ratio` against a fixed 60-day baseline | §8 recovery adequacy; the alcohol-night HRV dip |
| Resting heart rate | WHOOP, Oura, Apple Watch | one per night | one value per day | baseline for §14.3 |
| Respiratory rate | WHOOP, Oura, Apple Watch | one per night | one value per day | recovery context |
| Skin temperature deviation | Oura, WHOOP | one per night | one value per day, °C from baseline | recovery context |
| Blood oxygen (SpO2) | all three | nightly average | one value per day | recovery context |
| Sleep duration, stages, bed and wake time | all three | one per night | `sleep_hours`, `bed_time`, `wake_time`, `deep_min`, `rem_min` per day | §8 sleep duration; SRI derives from bed and wake |
| Sleep regularity index (SRI) | derived from any | one per night | one value per day | §8 regularity target ≥ 80 |
| Recovery / readiness score | WHOOP recovery, Oura readiness | one per morning | one value per day, 0–100 | 7-day summary line for T1 |
| Strain | WHOOP | one per day | one value per day | marathon load context |
| Intraday heart rate | Apple Watch (1 Hz in workouts, every few min otherwise), WHOOP (per minute), Oura (every 5 min daytime) | per minute or better | **per-minute series for the demo day**, stamped on the tick clock | §14.3 cross-reference with frames |
| Workouts | Apple Watch, WHOOP | per workout | `run_km`, `run_pace`, `run_avg_hr` per day, zero on rest days | marathon block on track |
| VO2 max | Apple Watch | periodic estimate | one value, constant across the week | marathon context |
| Walking steadiness | Apple Watch | daily | one value per day | §8 balance proxy |
| Time in daylight | Apple Watch (ambient light sensor) | daily minutes | one value per day | §8 daytime light dose — the seeded source §7 defers to |
| Journal tags | WHOOP Journal, Oura tags | daily yes/no | `journal_alcohol`, `journal_caffeine_late`, `journal_nicotine`, `journal_cannabis` per day | persona cut-down goals; corroborate camera sightings |

Steps, gait speed, and night noise stay on the phone side of §7 and are not
repeated here.

### 14.2 Seeded shape

- **Daily values** use the existing `seeded` table (`day, metric, value, unit,
  source`) with `source ∈ {whoop, oura, apple_watch}`. The scorer and the
  `GET /api/seeded` route need no schema change.
- **The intraday HR series** is a new table, `biometric_series (t, metric,
  value, source)`, with `t` on the same clock as `tick.t` so it runs under
  `--source sim --speed N` without desyncing. Seed the demo day only. Expose it
  as `GET /api/biometrics?metric=heart_rate&from=&to=`.
- **Deterministic fixtures, no randomness**, same as the existing 7-day seed.
  The planted pattern stays the one already in `seed/fixtures.py` (late coffee →
  late bedtime → short sleep → low HRV) and is extended, not replaced: the
  low-HRV days also carry a `journal_alcohol` or `journal_caffeine_late` tag, a
  low recovery score, and a skipped or slow run. The rest days show the
  opposite. The 7-day summary handed to T1 (§4.2 part 2) is rendered from these
  rows, so T1 can cite them.

### 14.3 Cross-referencing biometrics against frames

This is the feature only glasses plus a wearable can deliver: a biometric
anomaly explained by what the wearer was looking at.

- **Trigger.** `biometric_anomaly`: intraday HR above `resting_hr × 1.4` for
  a sustained window (default 3 min, `DEMO_MODE` 20 s) while `activity` is not
  `exercising` or `walking`. Evaluated by the trigger gate over the seeded
  series on the tick clock, with its own cooldown, like every other §3 trigger.
- **Escalation.** Same envelope as any camera trigger (§4.3): the tick table and
  four frames from the window, plus one extra text line carrying the HR series
  for the window. T1 is asked what was happening, not whether HR was high.
- **Output.** T1 annotates, and logs an insight of the form
  `14:32 HR 118 (resting 58), seated, frames show a three-person stand-up` —
  the wearable gives the number, the frames give the cause.
- **Demo.** One HR spike is planted at a scripted scenario moment so the
  trigger fires once inside the four-minute demo.

---

## 15. Live wearable integrations

§14 seeds every wearable metric. This section replaces the seed for the two
devices we own. Both feed the same laptop-side path, so the gate, the
reasoner, the scorer, and the dashboard never know which device a sample came
from — only its `source` label and whether its `origin` is `live` or `seed`.

### 15.1 Apple Watch via HealthKit (Person A, iOS bridge)

HealthKit is the Health database **on the iPhone paired to the watch**. It is
not a cloud account; the only way out is an app on that phone. The bridge
(§11.4) already runs there, so it reads HealthKit and forwards samples.

**Entitlement and permissions.** Add the HealthKit capability in Xcode and
`NSHealthShareUsageDescription` to Info.plist. Request read authorization for:

| HK type | Canonical metric | Unit | Cadence from the watch |
|---|---|---|---|
| `heartRate` | `heart_rate` | bpm | every few minutes at rest; ~1 Hz only inside an `HKWorkoutSession` (watchOS, out of scope) |
| `heartRateVariabilitySDNN` | `hrv_rmssd` | ms (SDNN — label it) | a few times a day, mostly during sleep |
| `oxygenSaturation` | `spo2` | % (×100) | periodic, mostly sleep |
| `respiratoryRate` | `respiratory_rate` | brpm | sleep |
| `appleSleepingWristTemperature` | `wrist_temp_dev` | °C relative to baseline | nightly |
| `stepCount` | `steps_delta` | steps per sample | continuous |
| `activeEnergyBurned` | `active_energy` | kcal per sample | continuous |
| `walkingHeartRateAverage` | `walking_hr_avg` | bpm | daily |
| `environmentalAudioExposure` | `env_sound_db` | dBA | periodic |

**Delivery.** One `HKAnchoredObjectQuery` per type with an `updateHandler`,
plus `enableBackgroundDelivery(for:frequency:.immediate)` and an
`HKObserverQuery` so the app is woken when new samples land. Persist the
anchors so a relaunch does not replay history. Batch samples and POST every
30 s; do not send one request per sample.

**Payload** — the canonical wearable payload, `t` = `sample.startDate` as
epoch seconds:

```json
POST http://<laptop>:8010/api/wearables/ingest
X-Ingest-Token: <WEARABLE_INGEST_TOKEN, if set>

{"device": "apple_watch",
 "samples": [
   {"t": 1789200842.0, "metric": "heart_rate", "value": 96, "unit": "bpm"},
   {"t": 1789200780.0, "metric": "spo2", "value": 97, "unit": "%"}
 ]}
```

Response `{accepted, rejected, reasons}`. Unknown metrics and timestamps more
than 48 h from now are rejected, not stored. The endpoint is idempotent on
`(t, metric)`, so re-sending a batch after a dropped connection is safe.

**Gotchas.** The simulator has no HealthKit data from a real watch — test on
the phone. Foreground the app during the demo; background delivery is
best-effort. HealthKit reports HRV as SDNN, not RMSSD; the dashboard labels
it, and no score compares it to the seeded RMSSD baseline.

**Fallback if the bridge slips: Health Auto Export.** A third-party iPhone app
that reads the same Health store and POSTs on a schedule. Point a REST
automation at `POST /api/wearables/ingest/health-auto-export` every 1–5 min
with the metrics above selected; the backend maps its JSON to the canonical
payload. Same data, same endpoint family, no Swift.

### 15.2 Fitbit via the Fitbit Web API (Person B, laptop)

The Fitbit Web API exposes **intraday** data for the account owner, which is
why it replaces WHOOP in the plan: WHOOP's API returns only daily and
per-event records.

**Registration** (once, by the account owner): dev.fitbit.com → new app,
application type **Personal** (this is what unlocks intraday access without
an approval process), redirect URL exactly
`http://localhost:8010/api/wearables/fitbit/callback`. Put the OAuth 2.0
client ID and secret in `backend/.env` as `FITBIT_CLIENT_ID` /
`FITBIT_CLIENT_SECRET`. Never commit them.

**Authorization.** OAuth 2.0 with PKCE. Open
`GET /api/wearables/fitbit/authorize` on the laptop, approve on Fitbit's page,
and the callback stores the token at `FITBIT_TOKEN_PATH` (gitignored). Access
tokens last 8 h; refresh tokens rotate and are persisted on every refresh.
Scopes: `heartrate sleep oxygen_saturation respiratory_rate temperature
cardio_fitness activity profile settings`.

**Poller.** `FitbitSync` runs every `FITBIT_POLL_S` (default 300 s), budgets
≤ 8 requests per cycle against the 150 requests/hour limit, honours
`Retry-After` on 429, and backs off ×2 up to 30 min on repeated failure.

| Endpoint | Canonical output |
|---|---|
| `/1/user/-/activities/heart/date/{d}/1d/1sec/time/{from}/{to}.json` | `heart_rate` samples (1 s), daily `resting_hr` |
| `/1/user/-/hrv/date/{d}/all.json`, `/hrv/date/{d}.json` | `hrv_rmssd` samples (5 min, sleep), daily `hrv_rmssd_ms` |
| `/1/user/-/spo2/date/{d}/all.json`, `/spo2/date/{d}.json` | `spo2` samples (1 min), daily `spo2` |
| `/1/user/-/br/date/{d}/all.json` | daily `respiratory_rate` |
| `/1/user/-/temp/skin/date/{d}.json` | daily `skin_temp_dev` |
| `/1/user/-/activities/steps/date/{d}/1d/1min/time/{from}/{to}.json` | `steps_delta` samples |
| `/1.2/user/-/sleep/date/{d}.json` | daily `sleep_hours`, `deep_min`, `rem_min`, `light_min`, `awake_min`, `sleep_efficiency`, `bed_time`, `wake_time` |
| `/1/user/-/activities/list.json?afterDate={d}` | daily `workout_km`, `workout_avg_hr`, `workout_minutes` |
| `/1/user/-/cardioscore/date/{d}.json` | daily `vo2_max` |

Timestamps in Fitbit responses are local to the profile timezone
(`/1/user/-/profile.json`); the poller converts them to epoch seconds.
Nightly metrics (sleep, HRV, SpO2, temperature) are fetched for yesterday as
well as today.

**Latency.** Data reaches Fitbit's servers only when the tracker syncs to the
Fitbit phone app — roughly every 15 min, or immediately when the app is
opened. Opening the app once before the demo forces a sync. "Live" from
Fitbit means minutes of lag; the Apple Watch path is the one that gets
within a minute.

### 15.3 Laptop side, shared by both

- **Storage.** Intraday samples go to `biometric_series (t, metric, value,
  source, origin)` with `origin = live`. Daily rows go to the existing
  `seeded` table with `source` set to the real device. When both live and
  seeded rows exist for a metric in a window, **live wins** and the seeded
  rows are ignored for that window.
- **Metric catalogue.** `heart_rate`, `hrv_rmssd`, `spo2`,
  `respiratory_rate`, `wrist_temp_dev`, `steps_delta`, `active_energy`,
  `strain`, `walking_hr_avg`, `env_sound_db`. Devices: `apple_watch`,
  `fitbit`, `whoop`, `oura`, `sim`.
- **Routes.** `POST /api/wearables/ingest` (canonical),
  `POST /api/wearables/ingest/health-auto-export`, `GET /api/wearables/status`
  (`live_connected` = any live sample in the last 15 min),
  `GET /api/biometrics?metrics=heart_rate,spo2,...` (multi-metric),
  `GET /api/wearables/fitbit/{authorize,callback,status,sync}`.
- **Every escalation** carries a "wearable now" context line (latest value
  of each metric seen in the last 30 min) in addition to the tick table, so
  T1 can cite HRV, SpO2, or breathing rate alongside the frames. The
  `biometric_anomaly` trigger (§14.3) reads the same feed and therefore fires
  on real heart rate as soon as a device is connected.
- **Dashboard.** The heart-rate strip gains tiles for the other metrics, each
  with a `live` / `seeded` pill and the device name, and the header says
  which device is connected.
- **Optional shared secret.** If `WEARABLE_INGEST_TOKEN` is set, ingest
  routes require the `X-Ingest-Token` header. Use it when the laptop is on a
  shared network.

