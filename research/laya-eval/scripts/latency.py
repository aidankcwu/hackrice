"""Latency of convaiinnovations/laya typed-decisions on real states, our 10 decider questions.

Usage: python latency.py <mps|cpu> [n_samples]
Merges its results into ../results/latency.json under "laya" -> <device>.
Laya encodes (state + question) once per question and runs the 10 rows as one batch per call.
"""
import json, sys, time, statistics, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import torch, laya
from questions import laya_questions, trim
from laya.common import serialize_state

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "results", "latency.json")
states = [json.loads(l)["state"] for l in open(os.path.join(ROOT, "data", "real_states.jsonl"))]
Q = laya_questions()
device = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 15
CPU_SKIP_MS = 20000

t0 = time.perf_counter()
agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device=device)
load_s = time.perf_counter() - t0
print(f"[{device}] load {load_s:.1f}s | cfg max_len={agent.cfg.get('max_len')} head_max_len={agent.cfg.get('head_max_len')}")
tok = agent.tok
full_lens = [len(tok(serialize_state(s), add_special_tokens=False)["input_ids"]) for s in states]
trim_lens = [len(tok(serialize_state(trim(s)), add_special_tokens=False)["input_ids"]) for s in states]
tokens = {"n_states": len(states),
          "full": {"median": statistics.median(full_lens), "max": max(full_lens), "min": min(full_lens)},
          "trimmed": {"median": statistics.median(trim_lens), "max": max(trim_lens), "min": min(trim_lens)}}
print(f"state tokens full {tokens['full']} | trimmed {tokens['trimmed']}")

def sync():
    if device == "mps": torch.mps.synchronize()

def bench(variant_name, variant, max_len, n):
    sample = states[-n:]
    for s in sample[:3]:  # warm-up
        a = time.perf_counter(); agent.system_one(variant(s), Q, max_len=max_len); sync()
        first = (time.perf_counter() - a) * 1000
        if device == "cpu" and first > CPU_SKIP_MS:
            print(f"[{device}] {variant_name} max_len={max_len}: skipped, one call took {first:.0f} ms")
            return {"skipped": f"single call {first:.0f} ms > {CPU_SKIP_MS} ms"}
    ts = []
    for s in sample:
        sync(); a = time.perf_counter(); agent.system_one(variant(s), Q, max_len=max_len); sync()
        ts.append((time.perf_counter() - a) * 1000)
    ts.sort()
    r = {"n": len(ts), "p50_ms": round(statistics.median(ts), 1),
         "p90_ms": round(ts[max(0, int(round(0.9 * len(ts))) - 1)], 1),
         "min_ms": round(ts[0], 1), "max_ms": round(ts[-1], 1)}
    print(f"[{device}] {variant_name:8s} max_len={max_len:5d}  p50 {r['p50_ms']:7.0f} ms  p90 {r['p90_ms']:7.0f} ms  (n={len(ts)}, 10 questions)")
    return r

ident = lambda s: s
runs = {}
for vname, v in (("trimmed", trim), ("full", ident)):
    for ml in (512, 1024, 2048):
        runs[f"{vname}@{ml}"] = bench(vname, v, ml, N)

res = json.load(open(OUT)) if os.path.exists(OUT) else {}
res.setdefault("laya", {})
res["laya"]["checkpoint"] = "convaiinnovations/laya subfolder typed-decisions"
res["laya"]["questions_per_call"] = len(Q)
res["laya"]["note"] = ("Laya tokenizes the state once, builds one (state + question) sequence per question, "
                       "and runs the 10 rows as one batch per system_one call; timings are per call (all 10 questions). "
                       "p50/p90 over the last n real states after 3 warm-up calls. Docker backend running.")
res["laya"]["state_tokens"] = tokens
res["laya"][device] = {"load_s": round(load_s, 2), "torch": torch.__version__, "runs": runs}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(res, open(OUT, "w"), indent=1)
print("wrote", OUT)
