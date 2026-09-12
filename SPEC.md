# Architecture spec

Lifestyle tracking on Ray-Ban Meta glasses. §1–§6 cover the processing
architecture — capture, tiering, storage, and the escalation model. §7–§11 cover
metric sources, reference thresholds, the T0 field set, scoring, and demo build
defaults. The dashboard is specified elsewhere.

---

## 1. Overview

Three layers, decoupled clocks.

| Layer | Cadence | Cost | Blocking |
|---|---|---|---|
| **T0 — Tick producer** | 1 Hz, always | Free + one small VLM call | Never |
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
- T0 samples the most recent frame at 1 Hz. The second frame each second is
  discarded.
- Frame is immediately downscaled to ~512px on the longest edge and JPEG-encoded
  at quality ~70 (~40 KB). **Store JPEG, not raw I420** — escalation then costs a
  buffer copy instead of a transcode.

### 2.3 Tick fields

Two groups, populated by different mechanisms:

**Non-AI fields** — computed locally from the frame buffer and phone sensors.
Always present, ~5 ms, zero cost. Includes pixel statistics (luminance, colour
temperature, histogram spread, frame delta, optical flow, sharpness, perceptual
hash), on-device ML outputs (face count, OCR text density), and phone sensors
(accelerometer, GPS speed, indoor/outdoor).

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
| **Frames** | Ring buffer, phone RAM | **90 seconds**, then evicted | ~40 KB each, ~3.5 MB total |
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
| Daytime light dose | Live (proxy) | `indoor_outdoor` + time of day. Outdoor daylight is reliably >1,000 lux, so "≥30 min outdoors before 10:00" needs no lux estimate. Absolute lux is **not** recoverable from an auto-exposed JPEG; use exposure metadata (ISO/shutter) only if the SDK exposes it |
| Evening light | Live (proxy) | Indoor + low luminance + warm colour temperature after sunset → "dim warm evening" flag. Covers worn time only |
| Nature dose | Live | Outdoor + `vegetation_visible` or `scene ∈ {park, trail}`, summed to weekly minutes |
| Screen / work hours | Live | `screen_present` sustained across ticks + OCR density, integrated to hours |
| Social integration | Live | `face_count` sustained over a window → conversation episodes per day |
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
| `people_present` | bool | Cross-checked against on-device `face_count` |

Non-AI fields remain as in §2.3. Where the platform exposes them, add pedometer
step delta and ambient light sensor (Android yes, iOS no) — both feed the seeded
side in the demo regardless.

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
a four-minute demo: morning outdoor light, nature minutes, social episodes,
screen hours, and meal tagging with the caffeine cutoff. Everything else scores
from seeded rows.

---

## 11. Demo build defaults (proposed, not yet confirmed)

| Decision | Default |
|---|---|
| Capture source | `--source` flag taking a webcam index or video file. T0 runs on a laptop; the DAT SDK is a swap-in capture adapter later |
| T0 VLM | Gemini Flash-Lite |
| T1 reasoner | Claude, structured tool-use response |
| Pipeline | Python, FastAPI, SQLite |
| Dashboard | Next.js, reads from the FastAPI service |
| Episode identity (§4.7) | **(a)** gate-side suppression for the demo; the "already annotated" hint to T1 can be layered on later without changing the gate |
