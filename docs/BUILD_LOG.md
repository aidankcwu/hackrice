# Build log — Person B (reasoning and product)

Running narrative of what was built, why, and who did it. This is the source
for the stage script: every entry is written so it can be read aloud.

Conventions: times are local (Houston). "Fleet" = Claude Fable 5.1 supervising,
with Opus / Sol coders and Sonnet / Terra debuggers, Astra 6 as second reviewer.

---

## Sat 12 Sep, ~02:00 — Scope locked

- Two-person split settled in SPEC §13. **We are Person B**: everything
  downstream of the tick stream. Person A (Aidan) owns glasses capture, the
  iOS bridge, the T0 VLM, and audio out.
- Models: Gemini Flash-Lite tags every frame at 1 Hz (A's side). **GPT is the
  T1 reasoner** — one structured call per escalation, images interleaved with
  tick tables. ElevenLabs speaks (A's side).
- Dropped from the live side: daytime/evening light (not recoverable from an
  auto-exposed JPEG), and all on-device ML (face count, OCR). `people_present`
  and `screen_present` are VLM tags only.
- **Stage line:** "The glasses see. A small model tags what they see once a
  second. Plain code decides when something is worth thinking about. A big
  model thinks about it maybe twenty times a day, and almost always decides to
  stay quiet — but it always writes down why."

## Sat 12 Sep, ~02:10 — Unblocking without the glasses

- Person A's replay corpus doesn't exist yet, so B ships its own **synthetic
  tick source** (`--source sim`): a scripted scenario (seated at a screen →
  coffee appears → lunch → outdoors → back to screen → drink) that emits
  spec-exact ticks at 1 Hz with realistic VLM gaps (~65% coverage) and
  placeholder JPEG frames. The entire downstream system is built and demoed
  against this before a single real frame arrives.
- **Stage line:** "We built the brain before the eyes were ready. The tick
  schema is the contract; anything that produces ticks — glasses, webcam, a
  script — plugs in."

## Sat 12 Sep, ~02:15 — Plan (Person B)

Six bounded subtasks, alternated between coders:

| # | Subtask | Coder |
|---|---|---|
| S1 | Contracts, SQLite, tick bus, frame store, synthetic tick source | Opus |
| S2 | Trigger gate + episode builder | Sol |
| S3 | T1 reasoner (GPT), context envelope, actions, speech rate limiter | Opus |
| S4 | Scorer against §8 thresholds + 7-day seeded data with a findable pattern | Sol |
| S5a | FastAPI routes, SSE feed, end-to-end wiring | Sol |
| S5b | Next.js dashboard with the silent-decision feed | Opus |

Every diff is reviewed by Fable and Astra before it lands.

## Sat 12 Sep, ~02:30 — Astra's plan review (second supervisor)

Astra 6 reviewed the plan read-only against the spec. Objections we adopted:

- **Tolerate everything.** Ingress models accept unknown fields and treat a
  missing boolean as *unknown*, never as *false*. AI tags older than ~3 s are
  unknown too, with confidence decaying before that.
- **Claim, don't wait.** The T1 concurrency cap is an atomic try-claim. If the
  reasoner is busy the escalation is dropped and logged — the gate never awaits
  inference, frame fetches, or speech.
- **Copy frames at admission.** The four evidence frames are copied out of the
  90 s ring *before* the model call and stored durably. Nothing else survives.
- **One clock.** Cooldowns, watches, and frame TTL all run on `tick.t`, so a
  10× simulation doesn't desync them.
- **Entry and exit debounce**, plus a grace window for AI gaps so a dropped
  VLM call doesn't close and reopen an episode.
- **Cuts:** SSE (poll instead), live gym/sauna episodes, elaborate seed
  generation. Caffeine and alcohol become point *sightings*, not episodes.
- **Fake mode is explicit.** `--reasoner fake` is a flag, never a silent
  fallback when a key is missing.
- **Stage line:** "Two supervisors, one from each vendor, reviewed every
  design decision and every diff. Neither could ship without the other."

## Sat 12 Sep, ~02:50 — S1 foundation landed (Opus)

- Tick contract, SQLite, latest-wins tick bus, 90 s frame store, and a
  synthetic tick source that plays a scripted day at any speed. 73 tests.
- Two supervisor amendments before anything built on it: AI booleans are
  tri-state (missing means *unknown*, never false), and all downstream timing
  runs on `tick.t` so a 20× simulation can't desync a cooldown.
- Astra's review caught one bug in the smoke harness (a drop counter read
  after the bus closed). Fixed inline.

## Sat 12 Sep, ~03:05 — S2 gate + episodes landed (Sol), one real bug caught

- Seven triggers evaluated over tick windows with per-trigger cooldowns, a
  global gap, episode-identity suppression (option a), and `watch` polling.
- **Bug caught in review:** a trigger suppressed by its own open episode
  ended evaluation for the whole tick, so caffeine could never fire while a
  screen block was already escalated. Two-line fix; the scenario went from
  four distinct triggers to six.
- **Astra's review** then caught that episode entry thresholds were hardcoded
  separately from trigger thresholds, so an escalation could arrive before
  its episode existed. Terra derived both from the same `Timings` fields.
- **Stage line:** "Plain code decides *when* to think. Seven conditions, each
  evaluated over a window of seconds, each with its own cooldown, and a rule
  that a sustained condition escalates once — not every second."

## Sat 12 Sep, ~03:35 — S3 reasoner (Opus), S4 scoring (Opus), S5b dashboard (Sol)

- **S3.** One GPT call per escalation, structured JSON out. The envelope
  interleaves a compact tick table with four JPEGs chosen by perceptual-hash
  change, each labelled with its timestamp and sensor fields, trigger frame
  last. Evidence frames are copied to a durable table *before* the call. The
  single T1 slot is a non-blocking try-lock; a busy reasoner drops and logs.
  Fake mode is an explicit flag. **Live smoke:** `gpt-5.4-mini`, ~2.3 s,
  returned "annotate + log_insight + speak(low)" on a late-coffee frame.
- **Persona.** Rishi wrote the T1 stable prefix in their own voice: short,
  direct, no cheerleading, say it once when actionable, otherwise write it
  down and stay quiet.
- **S4.** Every §8 row is a `MetricSpec` with a 0–1 score function. Live
  episodes and seeded WHOOP/phone rows go through the same scorer and come
  out tagged `live` or `seeded`. The seeded week has three late-caffeine days
  each followed by a short, low-HRV night, and the 7-day summary that feeds
  T1 is derived from the rows, not hardcoded.
- **S5b.** Next.js dashboard against the API contract, polling at 1 s.
  Centrepiece is the decision feed: every escalation, including the silent
  ones, one line each. Mock mode renders the whole thing with no backend.
  Sol's sandbox had no network, so the supervisor ran the install and build.
- **Astra caught two more bugs in review:** decision ids allocated from a row
  count could collide under contention and silently lose a dropped decision;
  and seeding on consecutive days never advanced the window. Both to Sonnet.
- **Stage line:** "Ninety percent of the time the system decides to say
  nothing. On stage that looks broken, so the dashboard shows every silent
  decision as a line: what it saw, what it concluded, and that it chose
  silence."

## Sat 12 Sep, ~04:10 — End to end, for real

- **S5a (Sol)** wired it into one FastAPI process: a single downstream
  subscriber feeds the tick store, episode builder, and gate in that order,
  the scorer runs on tick time, and every dashboard route exists. Sol's
  sandbox couldn't bind a port, so the supervisor ran the HTTP smoke.
- **First live run** (fake reasoner, 10× simulated time): ticks flowing at
  ~62% AI coverage, four decisions from four different triggers, four
  episodes, eighteen daily scores, today's memory populated. Port 8000 was
  already taken by another service on the laptop; the stack now runs on 8010.
- **Dashboard against live data** surfaced three contract mismatches that the
  mock had hidden: summary lines are objects not strings, seeded rows are
  long-format per metric, scores come as `{overall, scores}`. All fixed in the
  client normaliser, plus a doubled time prefix on annotate lines.
- **Sonnet (D2)** fixed Astra's two findings and found a third underneath:
  seeded episode ids were keyed by window position, so overlapping windows
  overwrote each other's days.
- **Stage line:** "The mock dashboard looked perfect. The first minute of real
  data found three lies in it. That's why we wired it tonight and not
  tomorrow."

## Sat 12 Sep, ~04:45 — S6/S7: wearables and the cross-reference

- **S6a (Sol).** WHOOP, Oura, and Apple Watch daily rows with device
  provenance, a deterministic per-minute heart-rate series on the tick clock
  with one spike planted in the lunch scene, `GET /api/biometrics`, and a
  7-day summary that now cites recovery, sleep stages, runs, and journal tags.
- **S6b (Opus).** The `biometric_anomaly` trigger: HR above resting × 1.4,
  sustained, while the camera says the wearer is not exercising or walking.
  The escalation carries one extra text line with the HR series, and T1 is
  asked what was happening, not whether HR was high. Dashboard gets an HR
  strip with the threshold line and new 7-day columns.
- **S7 (Sol).** Caffeine and alcohol sightings this week vs last, scored
  against the persona's own cut-down goal.
- **Astra caught** that the gate kept 90 s of ticks while the production HR
  window is 180 s, so exertion in the first half could age out and a workout
  would look like an anomaly. Fixed by keeping the widest trigger window.
- **Stage line:** "The wearable gives the number. The glasses give the
  cause. Neither alone can tell you that your heart rate spiked because of a
  three-person stand-up and not a run."

## Sat 12 Sep, ~05:30 — S8: real wearables

- Rishi owns a Fitbit and an Apple Watch, not a WHOOP, and that turned out to
  be good news: the Fitbit Web API exposes **intraday** data for the account
  owner (1-second heart rate, 5-minute HRV, per-minute SpO2, breathing rate,
  skin temperature, sleep stages, workouts), where WHOOP's API is daily only.
- **S8a (Opus).** One canonical ingest endpoint any device posts to, adapters
  for Health Auto Export and WHOOP objects, live-over-seed per window, a
  "wearable now" line on every escalation so T1 can cite HRV or SpO2 next to
  the frames, seven more seeded metrics for the demo, and dashboard tiles with
  live/seeded pills. An end-to-end run caught that "latest sample" under an
  accelerated sim was always in the future; fixed to read a window.
- **S8b (Sol).** Fitbit OAuth with PKCE, rotating token store, a poller that
  budgets ≤ 8 requests per 5 minutes against the 150/hour limit. It writes
  nothing itself; a sink the supervisor wired routes samples to ingest and
  daily rows to the seeded table under `source = fitbit`.
- **SPEC §15** written for Aidan: HealthKit types → canonical metrics, anchored
  queries with background delivery, batch POST every 30 s, and the Health Auto
  Export fallback. Apple Watch data only leaves the paired iPhone, so it goes
  through his bridge.
- **Astra caught** that live samples are stamped in wall time while an
  accelerated sim runs on tick time, so a real device would be invisible at
  `--speed 10`, and that first-time OAuth saved a token but started no poller.
  Both to Terra.
- **Stage line:** "Same endpoint whether the sample came from a Fitbit, a
  watch, or the simulator. The pipeline only cares that it's a number with a
  timestamp and a source."

## Sat 12 Sep, ~05:50 — Merge: Person A's branch lands

- Aidan's `person-a/capture-pipeline` branch merged in: `src/longevity`, a
  separate package at the repo root (not inside `backend/`), plus its own
  tests, tools, and the `ios/` Swift files. 46 tests passing on his side, one
  skipped.
- **Only `SPEC.md` conflicted**, and only because both sides had edited it —
  main kept its §7–§15, and took Person A's §2.3 rewrite (the sensor/device/AI
  field-group split) on top. Every other file was additive on one side or the
  other; nothing under `backend/` or `dashboard/` touched anything under
  `src/longevity` or `ios/`.
- **The seam held.** Two people worked the whole weekend on the same repo
  without a shared file, and it showed at merge time: not one file overlapped
  outside the one doc both sides needed to keep current.
- **Stage line:** "We drew the seam on a whiteboard at 2am and didn't touch
  it again all weekend. At merge time that showed up as one conflicted file —
  the spec — and zero conflicted code."

## Sat 12 Sep, ~06:05 — S9: tick cadence, for real ticks this time

- **Opus (S9).** The merged capture pipeline emits at 1.5 s, not the 1 Hz
  every trigger and episode threshold was tuned against. `Timings.scaled_hits()`
  derives every hit count from its 1 Hz reference so thresholds stay aligned
  by construction instead of being retuned by hand; the sim source and the
  dashboard's rate readout both switched to the same configured interval.
- **Measured:** worst-case `screen_sustained` first-fire at the old,
  unscaled thresholds would have been 78 s at 1.5 s cadence — nearly a third
  of the four-minute demo gone before the first decision lands. Scaled, it's
  22 s, matching what the 1 Hz design always intended.
- **Astra's review** added cadence-aware AI freshness (an `ai` block's age is
  judged against the real tick interval, not a hardcoded 1 s) and fixed
  episode dominant-tag selection to match.
- **Stage line:** "The cadence changed out from under every threshold we'd
  tuned. We didn't retune them one by one — we derived them all from the same
  number, so changing the number was the whole fix."

## Sat 12 Sep, ~06:25 — S10: one process, for real

- **Sol (S10).** `--source glasses|webcam|replay` now runs Person A's
  `T0Loop` inside Person B's FastAPI process instead of two services talking
  over a wire: his `TickBus` feeds ours, his `FrameRing` backs the reasoner's
  `FrameStore`, his `/ws/glasses` and `/frames` routes mount alongside ours on
  port 8010, and `speak()` sends over his phone link. Tick cadence passes
  through from `Settings.tick_interval_s`. **No edits to his code** — the
  bridge lives entirely in `backend/pipeline/capture/`. Backend moved to
  Python 3.11 to satisfy both projects' `pyproject.toml`.
- **Honest note.** The phone-side packet sender — A12 (sample and encode),
  A13 (phone sensors), A14 (assemble and send) — was still unwritten at merge
  time; `ios/MacLink.swift` only proves the socket and speech path (A11 +
  A16) both ways, with a comment marking where A14 replaces the placeholder
  payload with a real capture packet. A drafted Swift sender for A12–A14 was
  handed to Aidan rather than left as a gap.
- **Stage line:** "Two services became one process tonight, but one wire is
  still missing on the other side of it — the phone doesn't send real frames
  yet. We handed over a draft rather than a TODO."

## Sat 12 Sep, 04:39 — Glasses to dashboard, live

- First real packets from the Ray-Bans reached the Mac at 04:38: one phone
  connected, zero malformed, ~32 KB per frame. Gemini tagged the first ten
  ticks at 90% coverage, zero overruns, median 930 ms: `home / seated /
  screen_present`. The gate escalated `screen_sustained` and GPT's first
  decision on real frames was "user is seated at home with sustained screen
  presence" — annotate and log_insight, silent. Correct, and correctly quiet.
- The Swift sender that made it possible was drafted by the fleet, typechecked
  with `swiftc` for iOS 17, and reviewed adversarially before Person A pasted
  it in. The review caught that the sender would have reported packets as sent
  before the socket delivered them, and that MacLink overwrote its own
  "connected" status milliseconds after connecting.
- **Stage line:** "Camera on the glasses, small model on every frame, plain
  code deciding when to think, big model thinking twenty times a day, and a
  dashboard that shows you every time it chose to stay quiet."

## Sat 12 Sep, 05:09 — The loop closes: glasses in, ElevenLabs out

- Rishi heard the first ElevenLabs line through the Ray-Bans: rendered on
  the Mac in River's voice (9 KB of mp3, 1.35 s end to end), sent over the
  phone's WebSocket, decoded by Person A's new audio handler, played over
  Bluetooth. Every stage of SPEC §11.1 is now real hardware and real models.
- The path there was three small, dumb failures in a row, each found by
  testing the real thing: a label pasted along with the API key; a default
  voice the free plan can't use; a voice-id line glued onto a key line with
  no trailing newline. None of them would have shown up in a mock.
- Also live tonight: Gemini captions and object lists on every tick, the
  trigger frame sent to GPT at high detail, keyword triggers on the captions
  (first one: Rice Krispy treats, GPT chooses its own words), a decision feed
  that shows the trigger and GPT's full output per card, and a rule that
  memory is context, not evidence — after the reasoner spent a minute echoing
  a coffee it had seen once.
- **Stage line:** "The glasses see, a small model tags, plain code decides
  when to think, a big model thinks, and when it finally has something worth
  saying, you hear it in your ear. Everything in between chose silence, and
  the dashboard shows every one of those choices."

## Sat 12 Sep, ~06:10 — Fitbit, take two: the API we built against was dead

- The wearer went to register a Fitbit developer app and found dev.fitbit.com
  shut down: the Fitbit Web API was turned off this month in favour of the
  **Google Health API** (GA May 2026, Google OAuth, all Fitbit devices).
  Rishi pushed back on "not possible tonight" and was right to: a
  Testing-status OAuth client with the wearer as a test user reads their own
  data with no verification review.
- Verified the endpoints from Google's reference before writing a line:
  `health.googleapis.com/v4/users/me/dataTypes/{heart-rate,…}/dataPoints`,
  1-second heart rate, HRV, SpO2, sleep stages, steps, workouts, and the three
  restricted read scopes. Sol rebuilt the poller against that shape in one
  pass; the sink and dashboard did not change. FITBIT.md now walks the wearer
  through Google Cloud Console instead.
- Also merged tonight: Person A restructured the T0 menus (scene, activity,
  food, drink) and added five tri-state booleans; the B-side mirror and tests
  followed in the same hour. Open risk: 18 required VLM fields against a
  budget Gemini already sits on. Being measured live.
- **Stage line:** "Our wearable API died mid-hackathon. Two hours later the
  replacement was live, because nothing downstream of the sink knew or cared
  which API the numbers came from."
