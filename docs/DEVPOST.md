# Devpost writeup

**Title suggestion:** Quietly
**Tagline:** Ray-Ban glasses that watch your health all day and almost never interrupt you.

## Inspiration

Longevity science has a decent list of things that predict how long and how
well you live — daylight before 10am, Mediterranean-pattern meals, movement
snacks, social contact, sleep regularity — but almost none of it is
something a phone or wristband can see. A wearable knows your heart rate
spiked at lunch; it has no idea whether that's an argument or a staircase.
We wanted glasses that watch what a wristband is blind to, and only speak
when it's worth interrupting you — a device that narrates everything gets
muted by day one.

## What it does

- Streams what Ray-Ban Meta glasses see at 1 Hz through a small vision-language model tagging scene, activity, food, caffeine, alcohol, screen, people, vegetation.
- Runs a plain, rule-based trigger gate over that tag stream — no model, no cost — escalating to a reasoning model only on sustained conditions, ~20×/day.
- The reasoner (GPT) gets one structured multimodal call per escalation — interleaved tick data and four subsampled frames — and decides whether to speak, log an insight, jot a note, or recheck later.
- Cross-references seeded WHOOP / Oura / Apple Watch biometrics against camera frames to explain anomalies a wearable alone can't: *why* your heart rate spiked, not just that it did.
- Scores five camera-visible metrics (nature minutes, social time, screen hours, meal/caffeine timing, alcohol) plus a dozen wearable-seeded ones against `SPEC.md` §8 thresholds, daily and weekly.
- Logs every decision, including silent ones, to a dashboard feed — the 90% of the time it says nothing is visible as reasoning, not a broken system.

## How we built it

**Three layers, decoupled clocks.** A tick producer (T0) emits one tagged
object per second, forever, and never blocks — its vision-model call is
fired but not awaited, dropped on a 1-second budget, next tick fires
regardless. A trigger gate reads that stream over sliding windows (never a
single tick — AI tags land on only 50–80% of ticks), escalating on
sustained conditions with per-trigger cooldowns and a global minimum gap. A
reasoner (T1) runs only on escalation, capped to one concurrent call,
dropped and logged if busy — nothing ever queues.

**The tick schema is the contract**: `sensor` always present, `device` only
off real phone hardware, `ai` ~65% of the time and tolerated as absent
downstream. Fixed in the first thirty minutes, it let one side build a
synthetic tick source (`--source sim`) and develop the entire downstream
half — gate, episodes, reasoner, scoring, dashboard — before a real camera
frame existed. "We built the brain before the eyes were ready."

**The two-person seam.** One person owns everything upstream of the tick
stream — iOS/DAT bridge, capture adapters, ring buffer, vision tagging,
audio out. The other owns everything downstream — gate, episodes,
reasoning, scoring, dashboard. The only things crossing it are the tick
object and a `speak(text, urgency)` call.

**A fleet of agents, two supervisors.** The downstream half shipped as six
bounded subtasks across Claude Opus and Sol coders, every plan and diff
reviewed twice — Claude (Fable) supervising, a second independently-vendored
reviewer (Astra) reading against the spec. "Two supervisors, one from each
vendor, reviewed every design decision and every diff. Neither could ship
without the other." That second read caught most of the bugs below.

## Challenges we ran into

- **Suppression-return bug.** A trigger suppressed by its own open episode returned early for the *whole tick*, not just itself — caffeine couldn't fire while a screen block was open. Two-line fix, caught in review.
- **Threshold mismatch.** Episode-entry thresholds were hardcoded separately from trigger thresholds, so an escalation could reference an episode that hadn't opened yet. Fixed by deriving both from one timing config.
- **Decision-id collision.** IDs allocated from a row count could collide under concurrent writes, silently dropping a decision.
- **Mock-hid contract mismatches.** The dashboard looked perfect against mock data; real data found three lies in it — summary lines were objects not strings, seeded rows were long-format not wide, scores arrived as `{overall, scores}`. "That's why we wired it tonight and not tomorrow."

## Accomplishments

A full glasses-to-dashboard pipeline, wired end to end, the same night it
was designed. A system quiet by design and still fully legible — every
silent decision is a readable line, not a black box. A biometric-anomaly
feature that genuinely needs both wearable and camera. 885 backend tests,
and a two-reviewer process that caught real bugs before the demo touched
real data.

## What we learned

Real-time multimodal pipelines break at their seams — dropped calls, absent
fields, mock/live contract drift — more than in the "smart" model call
itself. A synthetic tick source matching the real schema is the
highest-leverage way to unblock a hardware-split team, and a system's
silence needs its own UI or a demo reads it as failure.

## What's next

Real WHOOP / Oura / HealthKit integration in place of today's seeded
fixtures; the DAT glasses capture path replacing `sim`/`replay` with a live
2 fps stream; and tick downsampling so storage survives past a 15-minute
demo.

## Built with

Python 3.11, FastAPI, uvicorn, SQLite, httpx, Gemini Flash-Lite, OpenAI GPT
(Responses API), ElevenLabs Flash v2.5, Next.js, Swift, iOS 17, Meta
Wearables DAT SDK (MWDATCore, MWDATCamera), Ray-Ban Meta glasses, uv, pytest.
