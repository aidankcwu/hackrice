# Demo script — four minutes on stage

Two people. Person A wears the glasses and performs the scenes. Person B
drives the laptop, narrates the dashboard, and watches the clock. Times below
are elapsed demo time (`t` from backend start), not wall clock.

> "The glasses see. A small model tags what they see once a second. Plain
> code decides when something is worth thinking about. A big model thinks
> about it maybe twenty times a day, and almost always decides to stay
> quiet — but it always writes down why." — opening line, say this cold
> before touching the laptop.

## 30-second setup checklist

- [ ] **Backend**, from `backend/`: `uv run python -m pipeline.main --source glasses --reasoner openai --speed 1 --demo-mode --port 8010`. Confirms `OPENAI_API_KEY` is set (`.env`) — the process exits immediately if it isn't and `--reasoner openai` was requested.
- [ ] **Dashboard**, from `dashboard/`: `npm run dev`, `NEXT_PUBLIC_API_BASE=http://localhost:8010` in `.env.local`. Load `http://localhost:3000`, confirm `/api/status` shows `demo_mode: true` and `tick_count` climbing.
- [ ] **Glasses**: charged (streaming drains them in under an hour — plug in until the last possible minute), Bluetooth-bonded to the phone, Meta AI app foregrounded, Developer Mode on.
- [ ] **Wi-Fi**: phone and laptop on the same LAN. The capture packet uplink is ~40 KB/s — if the venue Wi-Fi is congested, tether the phone to the laptop directly.
- [ ] **Fallback**: if the glasses drop mid-demo, kill the process and restart with `--source sim --speed 3` — the scripted scenario (seated → coffee → lunch → outdoors → screen) plays on its own and the dashboard keeps working. Person B narrates off whatever the dashboard shows rather than chasing the table below.

## Timeline

| t | Person A does | What fires | What the dashboard shows | Person B says |
|---|---|---|---|---|
| 0:00 | Sits at a laptop, screen in frame | — (warming up: `screen_sustained` needs 8 screen-positive ticks inside a 20 s window) | Tick counter climbing, `ai_coverage` ~0.6–0.8 | "Every second, whether or not anything's happening, we're writing one of these." *(point at the tick feed)* |
| ~0:15 | Still at the screen | `screen_sustained` fires → episode `screen_block` | New decision row lands, dim (silent) | "That's the gate. Screen time for twenty seconds, plain code, no model, decided this was worth a look — the reasoner looked and said 'nothing to do.'" |
| 0:15–0:50 | Recovery gap: glances away, sips water, no held prop | Nothing (per-trigger cooldown 45 s on `screen_sustained`; global 10 s gap keeps other triggers eligible) | Feed stays quiet | "Watch the feed do nothing for a bit — that's the design, not a bug." |
| ~0:55–1:05 | Lifts a coffee cup into frame, holds it there | `caffeine_seen` fires (2 hits in 10 s) → episode `caffeine_sighting` | New silent row: `caffeine_seen · annotate, log_insight` | "It just checked that against bedtime minus nine hours. If this were 9pm it would have said something." |
| 1:05–1:55 | Recovery gap: walks to a table, colleague sits across | Nothing yet | Quiet | *(walking, no narration needed)* |
| ~1:55–2:00 | Food on the table, plate in frame, colleague visible | `food_in_frame` fires (2 hits in 10 s) → episode `meal` | Silent row: `food_in_frame · mixed lunch w/ people · annotate, log_insight` | *(see silence beat below)* |
| ~2:00–2:40 | Keeps eating, talking | People-sustained accrues quietly in the background; nothing new highlighted | Feed adds a couple more dim rows | *(narrating the silence beat — see below)* |
| ~2:45–2:50 | Stays seated at the table, tenses up / checks phone (planted wearable HR spike lands here, on the tick clock) | `biometric_anomaly` fires — HR sustained ≥1.4× resting for 20 s while `activity` is `seated`, not `exercising`/`walking` | Row lights up (spoken, not dimmed): `biometric_anomaly · HR 118 vs resting 58, seated, frames show lunch with a colleague · speak, log_insight` | "This is the one thing neither the wearable nor the glasses could do alone. The watch has the number. The glasses have the reason." |
| 2:50–3:00 | Stays in scene, lets the utterance land | Speech rate limiter passes it through (30 s since last spoken — first speech of the demo) | `spoke: true` on that row | *(let the TTS play through the glasses if audio is wired; otherwise read the `interpretation` field aloud)* |

## The "silence is the feature" beat (~2:00–2:40)

Point at the dimmed `food_in_frame` row and read it aloud verbatim:
`12:31 · food_in_frame · mixed lunch w/ people · annotate, log_insight · silent`.

> "Ninety percent of the time the system decides to say nothing. On stage
> that looks broken, so the dashboard shows every silent decision as a
> line: what it saw, what it concluded, and that it chose silence."

Say the line, then read the `interpretation` text next to it out loud —
that's the proof the model actually looked, reasoned, and chose quiet on
purpose, rather than the pipeline just not running.

## What's real vs. seeded (say this once, plainly)

Everything the camera can plausibly see is live: screen hours, outdoor and
nature minutes, conversation episodes, meal and food-type tagging, and
caffeine/alcohol sightings all come from the T0 vision tags aggregated into
episodes and scored against the SPEC §8 thresholds in real time, right now,
off what Person A is doing. Everything the camera cannot see — sleep, HRV,
recovery, resting heart rate, steps, workouts — is seeded: hardcoded
WHOOP / Oura / Apple Watch fixture data, including the seven-day pattern
worth finding, clearly labelled `seeded` next to every `live` row on the
dashboard so nobody has to take our word for which is which. The one moment
that uses both — the heart-rate spike you just watched fire — pairs a real
camera-derived cause with a seeded biometric number, which is honestly the
whole pitch.

## Likely judge questions

**"What about privacy — you're recording everyone around you?"**
Raw frames live only in the laptop's RAM for 90 seconds and are discarded;
the only way one survives is being copied out as evidence at the moment of
an actual escalation, about 20 times a day. Nothing is written to disk or
uploaded from the always-on tagging layer — the retention window *is* the
privacy control, not a policy bolted on top of it.

**"What does this cost to run all day?"**
The always-on tagging is a small, cheap model at 1 Hz; the expensive
reasoning call only fires on escalation, roughly 20 times a day per SPEC §6.
At that volume a full day of monitoring, even with a frontier model doing
the reasoning, costs cents.

**"Why not just point one big multimodal model at the stream?"**
A frontier model watching every frame is either too slow to keep up with a
1 Hz stream or too expensive to run continuously — splitting into a free
tagger, a free rule-based gate, and an expensive reasoner that wakes rarely
gets both real-time and affordable. It also keeps the one expensive call
simple: one structured response with full context, not a chain of prompts.

**"What happens when the vision model is slow?"**
The call is fired but never awaited, with a 1-second budget; if it doesn't
return in time the tick is written without AI fields and the next tick
fires a fresh call immediately rather than waiting on the slow one. Nothing
queues anywhere in this system — a queued frame is a stale frame.

**"What's next?"**
Real WHOOP/Oura/HealthKit integration in place of the seeded biometric
fixtures, the DAT glasses capture path replacing today's `sim`/`replay`
sources with a live 2 fps camera stream, and tick downsampling so storage
survives longer than a 15-minute demo.
