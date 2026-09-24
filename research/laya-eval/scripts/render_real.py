"""Render decider states from tonight's real session, exactly as the pipeline would.

Each tick becomes a hypothetical escalation moment (plus the 12 real ones), using the
pipeline's own build_state, the real today_summary lines written before that moment,
the episodes open at that moment, the real persona and the seven-day text.
Read-only against a snapshot copy of the DB. Output: real_states.jsonl
"""
import json, sqlite3, sys
from pipeline.db import Database, day_key
from pipeline.models import Tick, Escalation, Episode
from pipeline.reasoner.decider import build_state
from pipeline.reasoner.prompts import DEFAULT_PERSONA
from pipeline.seed.generate import seven_day_summary

SNAP = sys.argv[1]; OUT = sys.argv[2]
db = Database(SNAP); db.connect()
con = sqlite3.connect(SNAP); con.row_factory = sqlite3.Row
ticks = []
for r in con.execute("select * from ticks order by t"):
    d = {k: r[k] for k in ("tick_id", "t", "seq", "frame_ref", "v")}
    for k in ("sensor", "device", "ai"):
        d[k] = json.loads(r[k]) if r[k] else None
    ticks.append(Tick.model_validate(d))
summary = [(r["t"], r["line"]) for r in con.execute("select t, line from today_summary order by t")]
eps = [Episode.model_validate({**dict(r), "dominant": json.loads(r["dominant"] or "{}"), "open": bool(r["open"])})
       for r in con.execute("select id, kind, start_t, end_t, duration_s, dominant, tick_count, open from episodes")]
real = {r["trigger_tick_id"]: dict(r) for r in con.execute("select * from decisions")}
persona = db.get_persona() or DEFAULT_PERSONA
day = day_key(ticks[-1].t)
try:
    seven = seven_day_summary(db, day)
except Exception as e:
    seven = "(no seven-day summary)"
n = 0
with open(OUT, "w") as f:
    for i, tk in enumerate(ticks):
        if i < 3:
            continue
        window = [x for x in ticks[: i + 1] if x.t >= tk.t - 90]
        dec = real.get(tk.tick_id)
        trig = dec["trigger"] if dec else "cue"
        reason = (dec or {}).get("interpretation") or ""
        esc = Escalation(trigger=trig, t=tk.t, tick=tk, window=window, reason=reason)
        lines = [line for (t, line) in summary if t < tk.t]
        open_eps = [e for e in eps if e.start_t <= tk.t and (e.end_t is None or e.end_t > tk.t)]
        state = build_state(esc, window, lines, open_eps, persona, seven, tk.t)
        f.write(json.dumps({"id": f"real_{tk.tick_id}", "real_decision": dec, "state": state}) + "\n")
        n += 1
print("rendered", n, "states; real decisions:", len(real), "; persona chars", len(persona), "; seven-day chars", len(seven))
