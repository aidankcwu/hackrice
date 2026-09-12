# Architecture spec

Lifestyle tracking on Ray-Ban Meta glasses. This document covers the processing
architecture only — capture, tiering, storage, and the escalation model. Feature
list, scoring model, and dashboard are specified elsewhere.

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