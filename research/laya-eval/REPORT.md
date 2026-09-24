# Report: is Laya worth it as the decider?

_Status: in progress. Sections are added as each stage finishes._

## Latency

Measured 2026-09-24 ~03:30 CDT on this Mac mini (Apple M4, 16 GB) with the backend containers running.
Checkpoint `convaiinnovations/laya` subfolder `typed-decisions` (ModernBERT-large encoder, 843 MB, loads in 2.6 to 2.9 s).
Every call asks our 10 decider questions. Laya tokenizes the state once and builds one (state + question) sequence per question,
so a call is a batch of 10 rows, each carrying the whole state. Times cover the whole call. mps: n=15 real states; cpu: n=8. Both after 3 warm-up calls.
Script: `scripts/latency.py`; raw numbers: `results/latency.json`.

State size (141 real states, Laya tokenizer): full median **1174** tokens (max 1272); trimmed median **434** (max 553).
So at `max_len` 512 the full state gets cut off (its time matches the trimmed state's), and every trimmed state fits in 1024.

| device | variant | max_len 512 p50 / p90 | 1024 p50 / p90 | 2048 p50 / p90 |
|---|---|---|---|---|
| mps | trimmed | 1498 / 1515 ms | 1500 / 1526 ms | 1503 / 1534 ms |
| mps | full | 1538 / 1552 ms (cut off) | 3248 / 3281 ms | 4216 / 4248 ms |
| cpu | trimmed | 3536 / 3590 ms | 3432 / 3459 ms | 3338 / 3370 ms |
| cpu | full | 3511 / 3552 ms (cut off) | 8194 / 8272 ms | 11044 / 11688 ms |

Fewer questions (mps, trimmed, 512): speak + ask only (2 rows) p50 **325** ms / p90 395 ms; speak only (1 row) p50 **163** ms / p90 169 ms.
Cost grows about linearly, roughly 150 ms per question.

**The current pipeline, for comparison** (session DB snapshot and `docker logs`, read-only, about 02:07 to 02:10 CDT):

| stage | n | median | range |
|---|---|---|---|
| Clerk decision (gpt-5.4-mini, `decisions.latency_ms`) | 10 | 1972 ms | 1286 to 3283 ms (p90 2600) |
| Voice agent turn (gpt-5.4-mini) | 5 | 1781 ms | 973 to 2777 ms |
| ElevenLabs TTS | 5 | 232 ms | 169 to 815 ms |
| Gemini Flash-Lite T0 tagging | 119 calls | p50 1257 ms | p90 1436 ms |
| Trigger to audio ready, per spoken nudge (estimated from log times) | 4 | 3788 ms | 1297 to 5006 ms |

In 2 of the 4 nudges the voice call started only after the clerk decision finished (4.6 s and 5.0 s total).
In the other 2 it started alongside the decision (3.0 s and 1.3 s). Phone playback and Bluetooth are not included.

**What this means.** Laya is not fast enough on this Mac for the full question set. With the trimmed state it takes about 1.5 s on the GPU (mps) for our 10 questions.
That is only about 0.5 s faster than the clerk's median decision of 2.0 s, and Laya only decides. The voice call that writes the sentence (about 1.8 s) and TTS (about 0.2 s) still have to run afterwards.
So at best it trims about half a second off the 3 to 5 s the wearer waits, and only when the voice call is waiting on the decision.
The CPU is 2 to 3 times slower and is out of the question. The full state is worse still (3.2 s at 1024 tokens), so trimming is required.
The one clear path is asking fewer questions: speak + ask alone takes 325 ms, speak alone 163 ms.

- **Friday rule 1 (trimmed p50 <= 250 ms on mps): MISSED.** Measured 1498 ms at max_len 512 and 1500 ms at 1024. That is 6 times over the limit.
- **Long-term rule 3 (trimmed p50 <= 500 ms on mps, or a clear path to it): MISSED as measured (1500 ms), but a clear path exists.**
  Cutting the call to the 2 wearer-facing questions (speak, ask) measured 325 ms. The other 8 would have to move off the hot path or into a smaller checkpoint.
