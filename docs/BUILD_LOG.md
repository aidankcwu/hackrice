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
