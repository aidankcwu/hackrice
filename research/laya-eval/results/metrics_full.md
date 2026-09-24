# Laya eval metrics (full run, status done)

| model | set | n | macro-F1 | #q in macro-F1 | speak F1 | speak false-alarm | ask F1 | ask false-alarm | topic acc | urgency acc | ECE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| zero_shot | real_test | 141 | 0.301 | 4 | - | 0.957 | 0.000 | 0.857 | 0.525 | 0.000 | 0.206 |
| zero_shot | synth_test | 226 | 0.343 | 8 | 0.357 | 0.671 | 0.152 | 0.264 | 0.597 | 0.173 | 0.043 |
| trained | real_test | 141 | 0.463 | 4 | - | 0.000 | 0.000 | 0.000 | 0.801 | 1.000 | 0.035 |
| trained | synth_test | 226 | 0.682 | 8 | 0.687 | 0.053 | 0.435 | 0.000 | 0.916 | 0.757 | 0.017 |
| trained | real_test__extra_field | 141 | 0.470 | 4 | - | 0.000 | 0.000 | 0.000 | 0.766 | 1.000 | 0.039 |
| trained | real_test__shuffled_keys | 141 | 0.291 | 4 | - | 0.000 | 0.000 | 0.000 | 0.447 | 0.972 | 0.062 |
| trained | real_test__renamed_key | 141 | 0.466 | 4 | - | 0.000 | 0.000 | 0.000 | 0.809 | 1.000 | 0.036 |

Robustness (trained, real_test): macro-F1 drop vs unaltered = extra_field -0.007, shuffled_keys 0.172, renamed_key -0.003

Train: 2.000 epochs done, best epoch 2, 1.028 states/s (10.277 question-rows/s), stop: epochs completed

Wall (s): install_s 11.1, load_s 16.2, total_s 4439.8, zero_shot_eval_s 120.5, train_s 3980.8, trained_eval_s 286.8

F1/AUROC are null when a question's labels are one class. Macro-F1 covers only questions with >= 5 positives on real_test (>= 1 elsewhere); metrics.json lists them per set (Amendment 1).
