"""Merge data/synth/batch_*.jsonl, dedup, and split 80/10/10 into
data/synth_train.jsonl, synth_val.jsonl, synth_test.jsonl.

Grouped split: states that share the same `recent` captions (contrast pairs and
programmatic variants of one scene) land in the same split, so a near-duplicate
of a test state is never trained on. Deterministic (seeded by group key hash).
Prints one summary line per split and exits 1 if an acceptance check fails.
"""
import glob, hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
YESNO = ["annotate", "log_insight", "remember", "watch", "speak", "ask", "act", "look"]


def canon(state):
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def group_key(state):
    caps = [t.get("caption", "") for t in state.get("recent", [])]
    return hashlib.sha1("|".join(caps).encode()).hexdigest()


def bucket(key):
    h = int(key[:8], 16) % 100
    return "test" if h < 10 else "val" if h < 20 else "train"


def main():
    rows, seen = [], set()
    real_canon = set()
    rt = os.path.join(DATA, "real_test.jsonl")
    for path in (rt, os.path.join(DATA, "real_states.jsonl")):
        if os.path.exists(path):
            for line in open(path):
                real_canon.add(canon(json.loads(line)["state"]))
    dup = leak_real = 0
    for path in sorted(glob.glob(os.path.join(DATA, "synth", "batch_*.jsonl"))):
        for line in open(path):
            ex = json.loads(line)
            c = canon(ex["state"])
            if c in real_canon:
                leak_real += 1
                continue
            if c in seen:
                dup += 1
                continue
            seen.add(c)
            rows.append(ex)
    splits = {"train": [], "val": [], "test": []}
    for ex in rows:
        splits[bucket(group_key(ex["state"]))].append(ex)
    ok = True
    for name, items in splits.items():
        with open(os.path.join(DATA, f"synth_{name}.jsonl"), "w") as f:
            for ex in items:
                f.write(json.dumps(ex) + "\n")
        n = len(items) or 1
        rates = {q: round(sum(ex["labels"][q] for ex in items) / n, 3) for q in YESNO}
        print(f"{name}: n={len(items)} " + " ".join(f"{q}={r}" for q, r in rates.items()))
        if name == "train":
            low = [q for q, r in rates.items() if r < 0.10]
            if low:
                print("FAIL: train yes-rate < 10% for", low)
                ok = False
    tr = {group_key(e["state"]) for e in splits["train"]}
    te = {group_key(e["state"]) for e in splits["test"] + splits["val"]}
    overlap = len(tr & te)
    print(f"merged={len(rows)} dropped_dups={dup} dropped_real_copies={leak_real} group_overlap={overlap}")
    if overlap:
        ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
