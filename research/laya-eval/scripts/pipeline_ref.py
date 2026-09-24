"""Current-pipeline latency reference, read-only, from the session DB snapshot and saved backend logs.

Usage: python pipeline_ref.py <tonight.db> <backend.log>   (log = `docker logs brian-backend-aidan-1 2>&1`)
Merges into ../results/latency.json under "pipeline_reference".
"""
import json, os, re, sqlite3, statistics, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "latency.json")
db, logf = sys.argv[1], sys.argv[2]

def summ(xs):
    xs = sorted(xs)
    if not xs: return {"n": 0}
    return {"n": len(xs), "median_ms": statistics.median(xs), "min_ms": xs[0], "max_ms": xs[-1],
            "p90_ms": xs[max(0, int(round(0.9 * len(xs))) - 1)]}

c = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
clerk = [r[0] for r in c.execute("select latency_ms from decisions where path='clerk' and latency_ms is not null")]
paths = dict(c.execute("select coalesce(path,'(null)'), count(*) from decisions group by path").fetchall())

ts = lambda line: datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").timestamp()
t1, voice, tts, vlm = [], [], [], []
events = []  # (kind, end_time, ms)
for line in open(logf, errors="replace"):
    if m := re.search(r"pipeline\.reasoner\.client: T1 \S+ (\d+) ms", line):
        t1.append(int(m[1])); events.append(("t1", ts(line), int(m[1])))
    elif m := re.search(r"conversation: \S+ turn (\d+) .* (\d+) ms\s*$", line):
        voice.append(int(m[2])); events.append(("voice", ts(line), int(m[2]), int(m[1])))
    elif m := re.search(r"speech mode=elevenlabs bytes=\d+ ms=(\d+)", line):
        tts.append(int(m[1])); events.append(("tts", ts(line), int(m[1])))
    elif m := re.search(r"longevity\.vlm: T0 VLM .*?ticks=(\d+)/.* p50=(\d+)ms p90=(\d+)ms", line):
        vlm.append({"ticks": int(m[1]), "p50_ms": int(m[2]), "p90_ms": int(m[3])})

# Trigger-to-audio for each proactive utterance (voice turn 1 followed by TTS); end is when the TTS mp3 is ready.
wearer = []
for i, e in enumerate(events):
    if e[0] != "voice" or e[3] != 1: continue
    vstart = e[1] - e[2] / 1000
    nxt = next((x for x in events[i + 1:] if x[0] == "tts"), None)
    if not nxt: continue
    # A T1 decision that ended at most 0.5 s before the voice call started is taken as the one it waited on.
    prior = [x for x in events if x[0] == "t1" and 0 <= vstart - x[1] <= 0.5]
    start = min([vstart] + [x[1] - x[2] / 1000 for x in prior])
    wearer.append({"at": datetime.fromtimestamp(e[1]).strftime("%H:%M:%S"), "total_ms": round((nxt[1] - start) * 1000),
                   "voice_ms": e[2], "tts_ms": nxt[2],
                   "waited_for_decision": vstart - start > 0.5})

res = json.load(open(OUT)) if os.path.exists(OUT) else {}
res["pipeline_reference"] = {
    "source": "session DB snapshot tonight.db (decisions table, read-only) and `docker logs brian-backend-aidan-1` "
              "(log times are UTC; session ~02:07-02:10 CDT). Nothing in the backend was modified.",
    "decision_paths": paths,
    "clerk_decision_ms": {**summ(clerk), "model": "gpt-5.4-mini", "what": "decisions.latency_ms where path='clerk'"},
    "t1_calls_from_log_ms": {**summ(t1), "what": "pipeline.reasoner.client T1 lines (same calls as the clerk decisions)"},
    "voice_turn_ms": {**summ(voice), "what": "conversation agent turn (gpt-5.4-mini voice call)"},
    "tts_ms": {**summ(tts), "what": "ElevenLabs synthesis, speech mode=elevenlabs ms="},
    "t0_vlm_gemini": {"reports": vlm, "what": "Gemini Flash-Lite T0 tagging, cumulative p50/p90 per report line"},
    "wearer_trigger_to_audio_ms": {**summ([w["total_ms"] for w in wearer]), "events": wearer,
        "what": "per proactive utterance: start of the T1 decision the voice call waited on (one that ended <= 0.5 s before it began), else the voice call start -> ElevenLabs mp3 ready. Estimate from log timestamps. "
                "Excludes phone playback and Bluetooth. waited_for_decision = the voice call started only after the decision."},
}
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps({k: v for k, v in res["pipeline_reference"].items() if k not in ("source",)}, default=str)[:1500])
