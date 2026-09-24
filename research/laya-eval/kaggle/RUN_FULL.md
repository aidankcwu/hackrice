# Full Kaggle run: exact commands

Kernel `wuaidan/laya-eval-train` (this folder: `train_eval.py`, `kernel-metadata.json`) reads the
private dataset `wuaidan/laya-eval-data`. The smoke run left that dataset holding PLACEHOLDER labels;
step 1 replaces them with the real data. All commands run from `research/laya-eval/`.

```bash
K="uvx --quiet --from kaggle kaggle"
STAGE=/private/tmp/claude-501/-Users-ljawhomebot-hackrice/a46816de-d932-4e2c-a518-9b23c731e483/scratchpad/kaggle-data
```

## 0. Size the run

Measured by the smoke run (kernel v2/v3, 2x T4 via DataParallel, trimmed state, median sequence 455
tokens, max_len 1024, 10 question rows per state):

| quantity | value |
|---|---|
| `STATES_PER_S` (training, fwd+bwd, incl. warm-up) | **1.19 states/s** (11.9 question-rows/s) |
| `EVAL_STATE_S` (Agent.predict_batch, 10 questions) | 0.31 s/state |
| `SETUP_S` (kernel boot + pip + model download/load) | ~60 s |
| checkpoint save + val (5 states) | ~3 s; budget 30 s |

Worked example: 60-min budget, n_real_test 141, n_synth_test 100, n_synth_val 100, 3 epochs:
FIXED_S = 60 + 0.31*(705+200) = 341; VAL_S = 0.31*150+30 = 77; avail_s = 3600-341-231-180 = 2848;
MAX_TRAIN = floor(1.19*2848/3) = 1129 states/epoch.

Pick `EPOCHS` (3 is a sane default; early stopping on synth_val
ends it sooner if val CE stops falling), then

```
avail_s   = BUDGET_MIN*60 - FIXED_S - EPOCHS*VAL_S - 180
MAX_TRAIN = floor(STATES_PER_S * avail_s / EPOCHS)      # 0 = use all of synth_train if that is smaller
FIXED_S   = SETUP_S + EVAL_STATE_S * (5*n_real_test + 2*n_synth_test)
VAL_S     = EVAL_STATE_S * 1.5 * n_synth_val + 30        # per-epoch val pass + temperature fit + checkpoint
```

`BUDGET_MIN` is script wall time only: it starts when the kernel script starts. Add ~2-4 min of
Kaggle queue/boot and ~1 min to fetch outputs when working back from a deadline. The kernel also
enforces the budget itself: it stops training mid-epoch when the remaining time is needed for the
final evaluation, validates and checkpoints what it has, and evaluates that. So an oversized
`MAX_TRAIN` costs epochs, not results.

## 1. Upload the real data + full-run config as a new dataset version

```bash
python3 kaggle/stage_data.py $STAGE --mode full --max-train MAX_TRAIN --epochs EPOCHS --budget-min BUDGET_MIN --keep-ckpt
$K datasets version -p $STAGE -m "full run data"
until $K datasets status wuaidan/laya-eval-data | grep -q ready; do sleep 15; done
```

`stage_data.py --mode full` copies `data/synth_{train,val,test}.jsonl` and `data/real_test.jsonl`
(whatever exists; synth_train is required) plus `scripts/questions.py`, and writes `config.json`.
`--keep-ckpt` keeps the best checkpoint in `/kaggle/working/ckpt_epN` (fp16, ~850 MB) so the trained
weights stay on Kaggle; never download it to this Mac unless disk allows.

## 2. Push the kernel

```bash
$K kernels push -p kaggle --accelerator NvidiaTeslaT4
```

## 3. Poll (background shell; exits when the kernel stops)

```bash
for i in $(seq 1 400); do s=$($K kernels status wuaidan/laya-eval-train | tail -1); echo "$(date +%H:%M) $s";
  case "$s" in *COMPLETE*|*ERROR*|*CANCEL*) break;; esac; sleep 60; done
```

## 4. Fetch outputs (metrics and predictions only, never the checkpoint)

```bash
$K kernels output wuaidan/laya-eval-train -p kaggle/out_full --file-pattern '(metrics\.(json|md)|\.log|preds/.*)$'
python3 - <<'EOF'
import json
M = json.load(open("kaggle/out_full/metrics.json"))
meta = {k: M[k] for k in ("status", "config", "method", "state_transform", "env", "sizes", "train", "wall")}
json.dump({**meta, "sets": M["models"]["trained"]}, open("results/trained.json", "w"), indent=1)
json.dump({**meta, "sets": M["models"]["zero_shot"]}, open("results/baseline_notebook.json", "w"), indent=1)
json.dump({"robustness": M.get("robustness"), "variants": {k: v for k, v in M["models"]["trained"].items()
           if k.startswith("real_test__")}}, open("results/robustness.json", "w"), indent=1)
print(M["status"], M["train"].get("epochs_done"), M["train"].get("stop_reason"))
EOF
```

If the kernel died (status ERROR or timeout), `metrics.json` still holds every phase that finished
(`status` stays `running`); the log `laya-eval-train.log` says where it stopped. The last saved
checkpoint is only evaluated inside the same run; there is no resume-from-checkpoint mode.

## metrics.json layout

- `models.zero_shot.{real_test,synth_test}` and `models.trained.{real_test,synth_test,real_test__extra_field,real_test__shuffled_keys,real_test__renamed_key}`:
  `n`, `macro_f1` over `macro_f1_questions` (EVAL_PROTOCOL Amendment 1: on real_test only questions with
  n_pos >= 5, elsewhere n_pos >= 1), `speak_f1`, `ask_f1`, `speak_false_alarm_rate`, `ask_false_alarm_rate`
  (FP / (FP + TN), the real_test Friday metric), `ece` (10 bins, all yes/no answers,
  conf = max(p, 1-p)), `topic_acc`, `urgency_acc` (argmax), `urgency_acc_rounded_expected`, and per
  question `acc, f1_yes, auroc, false_alarm_rate, pred_yes_rate, true_yes_rate, n_pos` (F1 and AUROC are null when the labels are one class).
- `robustness`: `base_macro_f1` and per variant `macro_f1`, `drop`.
- `train`: `states_per_s`, `rows_per_s`, `epochs_done`, `best_epoch`, `stop_reason`, per-epoch `history`
  (train CE, val CE, val macro-F1, fitted temperatures).
- `wall`: `install_s, load_s, zero_shot_eval_s, train_s, trained_eval_s, total_s`.
- `preds/<model>__<set>.jsonl`: per state id, gold labels and option probabilities per question.

Decode matches `laya.Agent` exactly because evaluation *is* `Agent.predict_batch` (zero-shot with the
shipped temperatures; trained with per-type temperatures fitted on synth_val, clamped to [0.5, 5]).
