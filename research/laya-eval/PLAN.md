# Laya evaluation: overnight plan (2026-09-24)

**Question to answer by morning:** is Laya worth pursuing as the decider,
(a) for the Friday zfellows demo and (b) long term? Answer with measured
numbers, not opinions.

**To resume after a context reset:** read `prd.json` (which stories pass)
and the tail of `progress.txt`. Nothing else is needed. Every story's outputs
are files in this folder.

## Principles that stop a botched night

1. **The report grows as I go.** `REPORT.md` gets a section the moment each
   stage finishes. If a late stage dies, the morning report still carries every
   conclusion reached before it. There is never a single "write the report at
   the end" step that can fail.
2. **Cheapest decisive test first.** Latency on this Mac mini is measured
   before anything else. It alone can settle the Friday question.
3. **Smoke before scale.** The Kaggle training pipeline runs end to end on a
   tiny dataset (about 10 minutes) before the real run is launched. The real
   run is only started once the smoke run has produced a metrics file.
4. **Evaluation rules are fixed before training** (`EVAL_PROTOCOL.md`), so the
   result cannot be tuned after the fact.
5. **Every stage has a deadline and a fallback.** When a stage overruns, it is
   cut down and the night moves on. See the table below.
6. **Nothing outside this folder changes.** No edits to the backend, the
   running containers, Docker, other sessions' files, or git branches. Commits
   go to local `main`, this folder only, never pushed.

## Context management

| Rule | How |
|---|---|
| State on disk, not in my head | `prd.json` + `progress.txt`, Ralph-style. One story = one fresh sub-agent. |
| Sub-agents return short summaries | Every sub-agent writes its outputs to files and returns at most ~150 words: pass/fail, key numbers, file paths. |
| I never read bulk output | No catting datasets, logs or notebooks into my context. Checks are scripts that print one line. |
| Long waits run in the background | Kaggle runs are polled by a background shell loop that exits on completion, which wakes me. |
| Verification is mechanical | Each story's acceptance criteria are checked by a script (`scripts/check_story.py`), not by rereading the work. |

**Why not `ralph.sh` itself:** it launches `claude --dangerously-skip-permissions`
in a loop, which disables every permission check and runs outside this
session. Too risky unattended. The same pattern (prd.json, progress log,
fresh context per story) is run with in-session sub-agents instead.

## Stages, deadlines, fallbacks (times CDT)

| # | Stage | Done by | If it fails or overruns |
|---|---|---|---|
| 1 | Latency on MPS and CPU, real states, 512/1024/2048 tokens, full vs trimmed state | 04:15 | CPU only; if both fail, report the failure and use published numbers, flagged as unverified |
| 2 | Rulebook (the labeling policy), state schema, validator | 04:15 | Runs in parallel with 1 |
| 3 | Label the 141 real states from tonight's session (real test set) | 05:00 | Label a subset (at least the 12 real escalations + 60 others) |
| 4 | Generate synthetic states in parallel batches, validate, dedup, split | 05:30 | Train on whatever has passed validation by 05:30 (minimum 600 states) |
| 5 | Kaggle training script + smoke run on 50 states | 05:30 | Fall back to supervised head-only training on Kaggle; if Kaggle itself fails, local head-only on MPS |
| 6 | Full Kaggle run, sized from smoke-run throughput to finish by 08:30; baseline + trained eval + robustness tests run inside the notebook | 08:30 | Evaluate the last saved epoch checkpoint |
| 7 | Zero-shot baseline on this Mac (sanity check against the notebook's baseline) | 06:30 | Use the notebook's baseline only |
| 8 | Final report: Friday answer + long-term answer | 09:00 | Stage-by-stage sections already exist from principle 1 |

## Known risks and the guard for each

| Risk | Guard |
|---|---|
| Disk (5.5 GB free) | Only the `typed-decisions` checkpoint is downloaded locally. Trained weights stay on Kaggle; metrics come back as JSON. Before any large write, check `df` >= 2.5 GB; if lower, `uv cache prune` (cache only) is the one allowed cleanup. |
| Memory (Docker holds 8 of 16 GB) | No full fine-tuning locally. Local work is inference and at most head-only training at small batch size. Docker is never stopped. |
| Kaggle session limit (12 h) and quota | Size the run from measured smoke throughput to end by 08:30. Save a checkpoint every epoch to `/kaggle/working`. |
| Synthetic data looks unlike real Gemini output | Generators are seeded with real captions and objects from tonight. The real test set is scored separately and is the number that counts. |
| Labels encode one person's judgment | The rulebook is written down first and committed, so it can be reviewed and changed. |
| A permission prompt blocks me while you sleep | Only low-risk commands: files in this folder, the scratchpad, Kaggle private kernels. No docker, no git push, no deletions outside the scratchpad. |
| My context degrades | Principle list above; resume from `prd.json` + `progress.txt`. |

## Folder layout

```
research/laya-eval/
  PLAN.md            this file
  prd.json           stories and pass/fail (Ralph format)
  progress.txt       append-only log, one line per event
  RULEBOOK.md        labeling policy (stage 2)
  SCHEMA.md          exact state and label format (stage 2)
  EVAL_PROTOCOL.md   metrics and decision rules, fixed before training
  REPORT.md          grows stage by stage; the morning deliverable
  scripts/           render, latency, validate, split, eval
  data/              real_states.jsonl, real_test.jsonl, synth/, splits
  kaggle/            training kernel and its metadata
  results/           latency.json, baseline.json, trained.json, robustness.json
```

Model weights never go in git.
