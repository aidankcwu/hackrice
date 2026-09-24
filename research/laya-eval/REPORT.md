# Report: is Laya worth it as the decider?

_Finished 2026-09-24 13:15 CDT. Rules: EVAL_PROTOCOL.md (fixed before training, amendment 1 at 03:37)._

## Verdict

**Friday: stay on the clerk.** Laya takes about 1.5 s per decision on the Mac mini's GPU for our
10 questions, against 250 ms needed and 2.0 s for the clerk. Trained quality is also below the bar.

**Long term: training works, and it would make a usable decider if it ran on a GPU server.**
Two epochs on 1,961 generated states took it from unusable to disciplined:

| What changed with training | Untrained | Trained |
|---|---|---|
| Real moments: says "speak" when it should be quiet | 96% | **0%** |
| Real moments: says "ask" when it should not | 86% | **0%** |
| Real moments: macro-F1 over the 4 questions with enough positives | 0.30 | 0.46 |
| Generated test: macro-F1 over all 8 yes/no questions | 0.34 | **0.68** |
| Generated test: speak F1 / false-alarm rate | 0.36 / 67% | **0.69 / 5%** |
| Generated test: ask F1 | 0.15 | 0.43 |
| Topic accuracy (real / generated) | 0.52 / 0.60 | 0.80 / 0.92 |
| Calibration error, real (lower is better) | 0.21 | 0.035 |

**Your "new field" worry mostly does not hold, but field ORDER matters.** Adding an unseen field
(-0.007) or renaming a field (-0.003) cost nothing. Shuffling the order of the fields cost 0.17
macro-F1. Our state builder always emits the same order, so in practice: add new fields at the end
and never reorder, and retrain when a field starts to matter for the decision.

## Decision rules (EVAL_PROTOCOL.md)

| Rule | Result | Status |
|---|---|---|
| Friday 1: trimmed p50 <= 250 ms on mps | 1,500 ms | missed |
| Friday 2a: real speak / ask false-alarm <= 5% | 0% / 0% | met |
| Friday 2b: generated speak F1 >= 0.80, ask F1 >= 0.70, macro-F1 >= 0.75 | 0.69 / 0.43 / 0.68 | missed |
| Friday 3: serve trained weights on this Mac | not tried (Friday already decided; 3.5 GB disk, heavy swap) | not evaluated |
| Friday 4: saves >= 1 s wearer-facing | at most ~0.5 s | missed |
| Long-term 1: trained beats untrained by >= 0.15 macro-F1 on real | +0.16 (and +0.34 on generated) | met, barely on real |
| Long-term 2: each altered form costs <= 0.05 | extra field -0.007, renamed -0.003, shuffled order 0.17 | missed on order only |
| Long-term 3: trimmed p50 <= 500 ms on mps, or a path to it | 1,500 ms; paths: 2 questions ~325 ms here, Kaggle T4 ~350-420 ms for all 10 | missed, path exists |

## What to trust and what not to

- **The real test set is narrow.** 141 moments from one 3-minute phone-at-laptop session. The right
  answer was "stay quiet" for all of them, so it measures restraint, not whether Bryan speaks up at
  the right moment. "Speak when it matters" is only measured on generated data.
- **Generated test numbers are optimistic,** because the same kind of agent wrote train and test.
- **Trained 2 epochs only, and the best epoch was the last,** so more epochs would probably help.
  `ask` (F1 0.43) is the weakest question.
- **The labels are the rulebook's judgment** (RULEBOOK.md, 49 rules). Generators reported that the
  `watch` rules fire on most intake moments (40-80% yes). Review the rulebook before any real training.
- **Jev was not tested** (signups closed, no key). The harness here can score it in ~10 minutes once a key exists.

## Run details

- Data: 2,426 generated states in 13 themed batches, grouped split 1,961 / 239 / 226, no duplicates,
  no scene in both train and test. Remember/ask/act/look had 7.6-9.9% positives in train (149-194 each).
- Training: Laya's official method (policy gradient on a proper scoring reward plus soft cross-entropy,
  per-type temperatures fitted on validation), 2x T4 on Kaggle, 1.03 states/s, 66 min of training,
  74 min total. Trained weights stay on Kaggle (private kernel `wuaidan/laya-eval-train`).
- Full metrics: results/metrics_full.md, results/trained.json, results/baseline_notebook.json,
  results/robustness.json, results/latency.json.
- What went wrong overnight: the Kaggle pipeline stage took about 8 hours instead of 1, and nothing
  enforced its deadline, so the full run started at 11:54 instead of about 05:30.

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
