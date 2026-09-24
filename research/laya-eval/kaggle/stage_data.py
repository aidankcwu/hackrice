"""Stage the private Kaggle dataset wuaidan/laya-eval-data (data + questions + run config).

Usage:
  python kaggle/stage_data.py OUT_DIR --mode smoke  [--epochs 1] [--budget-min 25]
  python kaggle/stage_data.py OUT_DIR --mode full --max-train N --epochs E --budget-min M

smoke: 60 real states with PLACEHOLDER labels from a fixed rule (mechanics only, not quality):
       50 synth_train / 5 synth_val / 5 synth_test, and 20 of the same states as a fake real_test.
full:  copies data/synth_{train,val,test}.jsonl and data/real_test.jsonl (whatever exists).
Both copy scripts/questions.py (the one source of the 10 questions and the trim transform)
and write config.json + dataset-metadata.json. Then:
  kaggle datasets create -p OUT_DIR --dir-mode zip      (first time)
  kaggle datasets version -p OUT_DIR -m MSG --dir-mode zip   (after)
"""
import argparse, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from questions import ACTION_QUESTIONS, TOPICS, URGENCY  # noqa: E402

SLUG = "wuaidan/laya-eval-data"
WORDS = ("food", "drink", "coffee", "cup", "bottle", "phone", "iphone", "eat", "snack", "water")


def placeholder_labels(i, state):
    caps = " ".join((t.get("caption") or "") + " " + " ".join(t.get("objects") or [])
                    for t in state["recent"]).lower()
    lab = {q: bool((i + j) % 2) for j, q in enumerate(ACTION_QUESTIONS)}
    lab["speak"] = any(w in caps for w in WORDS)
    lab["topic"] = TOPICS[i % len(TOPICS)]
    lab["urgency"] = URGENCY[i % len(URGENCY)]
    return lab


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--mode", choices=["smoke", "full"], required=True)
    ap.add_argument("--max-train", type=int, default=0, help="0 = all of synth_train")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--budget-min", type=float, default=25)
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=2.5e-5, help="encoder LR; head LR is 4x")
    ap.add_argument("--state-mode", choices=["trim", "full"], default="trim")
    ap.add_argument("--keep-ckpt", action="store_true", help="keep the best checkpoint in /kaggle/working")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    for f in os.listdir(a.out):
        if f.endswith((".jsonl", ".json", ".py")):
            os.remove(os.path.join(a.out, f))
    shutil.copy(os.path.join(ROOT, "scripts", "questions.py"), a.out)

    if a.mode == "smoke":
        rows = [json.loads(l) for l in open(os.path.join(ROOT, "data", "real_states.jsonl"))][:60]
        lab = [{"id": r["id"], "source": "synth", "state": r["state"],
                "labels": placeholder_labels(i, r["state"]), "rationale": "PLACEHOLDER smoke label"}
               for i, r in enumerate(rows)]
        splits = {"synth_train": lab[:50], "synth_val": lab[50:55], "synth_test": lab[55:60],
                  "real_test": [dict(x, source="real") for x in lab[40:60]]}
        for name, xs in splits.items():
            with open(os.path.join(a.out, name + ".jsonl"), "w") as f:
                f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in xs)
    else:
        found = 0
        for name in ("synth_train", "synth_val", "synth_test", "real_test"):
            src = os.path.join(ROOT, "data", name + ".jsonl")
            if os.path.exists(src):
                shutil.copy(src, a.out); found += 1
            else:
                print("missing (skipped):", src)
        if not os.path.exists(os.path.join(a.out, "synth_train.jsonl")):
            sys.exit("synth_train.jsonl is required for a full run")

    cfg = {"mode": a.mode, "max_train": a.max_train, "epochs": a.epochs, "max_len": a.max_len,
           "lr": a.lr, "time_budget_min": a.budget_min, "state_mode": a.state_mode,
           "keep_ckpt": a.keep_ckpt}
    json.dump(cfg, open(os.path.join(a.out, "config.json"), "w"), indent=1)
    json.dump({"title": "laya-eval-data", "id": SLUG, "licenses": [{"name": "CC0-1.0"}]},
              open(os.path.join(a.out, "dataset-metadata.json"), "w"), indent=1)
    print("staged", a.out, cfg)


if __name__ == "__main__":
    main()
