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
