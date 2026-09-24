import json, sys, time, statistics, os
sys.path.insert(0, os.path.dirname(__file__))
import torch, laya
from questions import laya_questions, trim
from laya.common import serialize_state

states = [json.loads(l)["state"] for l in open(os.path.join(os.path.dirname(__file__), "real_states.jsonl"))]
Q = laya_questions()
device = sys.argv[1]
t0 = time.perf_counter()
agent = laya.load("convaiinnovations/laya", subfolder="typed-decisions", device=device)
print(f"[{device}] load {time.perf_counter()-t0:.1f}s | cfg max_len={agent.cfg.get('max_len')} head_max_len={agent.cfg.get('head_max_len')}")
tok = agent.tok
full_lens = [len(tok(serialize_state(s), add_special_tokens=False)["input_ids"]) for s in states]
trim_lens = [len(tok(serialize_state(trim(s)), add_special_tokens=False)["input_ids"]) for s in states]
print(f"state tokens  full: median {statistics.median(full_lens)}, max {max(full_lens)} | trimmed: median {statistics.median(trim_lens)}, max {max(trim_lens)}")

def bench(label, variant, max_len, n=15):
    sample = states[-n:]
    for s in sample[:3]:  # warm-up
        agent.system_one(variant(s), Q, max_len=max_len)
    ts = []
    for s in sample:
        if device == "mps": torch.mps.synchronize()
        a = time.perf_counter(); agent.system_one(variant(s), Q, max_len=max_len)
        if device == "mps": torch.mps.synchronize()
        ts.append((time.perf_counter() - a) * 1000)
    ts.sort()
    print(f"[{device}] {label:28s} max_len={max_len:5d}  p50 {statistics.median(ts):7.0f} ms  p90 {ts[int(0.9*len(ts))-1]:7.0f} ms  (10 questions)")

ident = lambda s: s
bench("trimmed state", trim, 512)
bench("trimmed state", trim, 1024)
bench("full state (truncated)", ident, 512)
bench("full state (truncated)", ident, 1024)
bench("full state", ident, 2048)
