# Evaluation protocol (fixed before any training)

Written 2026-09-24 03:30 CDT, before any data is generated or any model trained.
Results are judged against these rules and nothing else.

## Test sets

| Set | Source | Role |
|---|---|---|
| `real_test` | The 141 states rendered from tonight's real session (`data/real_states.jsonl`), labeled against `RULEBOOK.md` by an agent that did not generate training data | **The number that counts** |
| `synth_test` | 10% of generated states, held out before training | Sanity check only; expected to look better than reality |

No state from either test set is ever used for training or for picking hyperparameters.
`synth_val` (10%) is used for early stopping.

## Models compared

1. `zero-shot`: `convaiinnovations/laya` subfolder `typed-decisions`, untouched.
2. `trained`: the same checkpoint fine-tuned on `synth_train`.

## Metrics, per question

- The 8 yes/no questions: accuracy, F1 of the "yes" class, AUROC, and the predicted versus true yes-rate. A decision uses P(yes) >= 0.5.
- `topic`: accuracy. `urgency`: accuracy over the 3 levels.
- Calibration: expected calibration error (10 bins) over all yes/no answers.
- Headline number: **macro-F1 over the 8 yes/no questions on `real_test`**, with `speak` and `ask` F1 reported separately, since those decide what the wearer hears.

## Robustness (the "new field" question)

Run the trained model on `real_test` in three altered forms and report the drop in macro-F1:

1. An extra field it never saw in training: `"weather": {"temp_c": 31, "sky": "clear"}`.
2. The state's keys in a shuffled order.
3. One key renamed: `today` becomes `today_log`.

## Latency

- Measured on this Mac mini (M4, backend containers running), device `mps` and `cpu`.
- 10 questions per call, as the decider sends them. p50 and p90 over 15 real states after 3 warm-up calls.
- Full state versus trimmed state (no persona or trends, 4 ticks, 8 today lines), at `max_len` 512, 1024 and 2048.
- Compared with the clerk's decision time and the full delay the wearer hears, taken from the backend logs.

## Decision rules

**Friday: use Laya only if all four hold.** Otherwise Friday stays on the clerk.

1. Trimmed-state p50 <= 250 ms on `mps` with the backend running.
2. `trained` on `real_test`: `speak` F1 >= 0.80, `ask` F1 >= 0.70, macro-F1 >= 0.75.
3. The trained weights can be served on this Mac with `laya-serve` inside the disk and memory limits.
4. It saves at least 1 s of wearer-facing delay compared with the clerk.

**Long term: worth pursuing if all three hold.**

1. `trained` beats `zero-shot` by >= 0.15 macro-F1 on `real_test`.
2. Robustness: each altered form costs <= 0.05 macro-F1.
3. Latency: trimmed-state p50 <= 500 ms on `mps`, or a clear path to it (smaller checkpoint, fewer questions).

If a rule cannot be evaluated because a stage failed, the report says so and does not guess.
