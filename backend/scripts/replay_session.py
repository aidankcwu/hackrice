"""Replay a recorded session through the decision stack and print what it says.

The glasses are not needed. A recorded run's ticks (real Gemini tags, real
``in_hand`` values, real captions) are read from its SQLite database and fed
back through a fresh pipeline -- gate, episodes, clerk, voice agent -- under
whatever settings you pass, and every spoken line is written to a report next
to what the original run said. The 40-odd evidence JPEGs the original run
saved at its escalations are put back into the frame ring so the models see
real pixels at exactly the moments that matter.

    cd backend
    uv run python scripts/replay_session.py ../deploy/data/aidan/pipeline.db \\
        --from 16:30 --to 16:44 --speed 2 --reasoner openai \\
        --no-cue-trigger --persona persona_investor.txt \\
        --expect scripts/expect_quiet_desk.json --out /tmp/replay.md

    --reasoner fake      plumbing only, no keys, run it at --speed 20
    --speed N            wall time compression; the model calls are still
                         real-time, so above ~3 the clerk slot is busier than
                         it would be live. Use 1 for the final check.
    --no-cue-trigger     CUE_TRIGGER=0: no one-tick persona cues
    --no-fast-path       FAST_PATH=0: cues wake the clerk instead of the mouth
    --persona FILE       PERSONA_FILE: replaces the built-in persona
    --env-file FILE      where OPENAI_API_KEY lives (e.g. ../deploy/.env)
    --expect FILE        JSON: {"max_spoken": 2, "forbid": ["phone", ...],
                         "require_once": ["creatine"]}; exit 1 when violated

Ticks are shifted so the first one lands on the pipeline's own clock; the
report shows original times. The source database is opened read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.actions.speech import clear_spoken, spoken  # noqa: E402
from pipeline.api.wiring import build_pipeline  # noqa: E402
from pipeline.config import Settings  # noqa: E402
from pipeline.db import Database  # noqa: E402
from pipeline.models import Tick  # noqa: E402

log = logging.getLogger("replay")


# -- reading the recorded run ---------------------------------------------------


def _open_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _hhmm_on(day_t: float, hhmm: str) -> float:
    base = datetime.fromtimestamp(day_t)
    h, m = (int(x) for x in hhmm.split(":"))
    return base.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()


def load_recorded(path: Path, t_from: str | None, t_to: str | None,
                  session: str | None) -> dict[str, Any]:
    con = _open_ro(path)
    first = con.execute("select min(t) as t from ticks").fetchone()["t"]
    if first is None:
        raise SystemExit(f"{path}: no ticks")
    t0, t1 = float("-inf"), float("inf")
    if session:
        row = con.execute("select started_t, ended_t from sessions where id = ?",
                          (session,)).fetchone()
        if row is None:
            raise SystemExit(f"no session {session}")
        t0, t1 = row["started_t"], row["ended_t"] or t1
    if t_from:
        t0 = _hhmm_on(first, t_from)
    if t_to:
        t1 = _hhmm_on(first, t_to)
    rows = con.execute("select * from ticks where t >= ? and t <= ? order by t",
                       (t0, t1)).fetchall()
    ticks = [Database._tick_from_row(r) for r in rows]
    if not ticks:
        raise SystemExit("no ticks in that range")
    refs = {t.frame_ref for t in ticks}
    frames: dict[str, bytes] = {}
    for r in con.execute("select frame_ref, jpeg from escalated_frames"):
        if r["frame_ref"] in refs:
            frames[r["frame_ref"]] = bytes(r["jpeg"])
    lo, hi = ticks[0].t - 5, ticks[-1].t + 60
    said: list[tuple[float, str, str]] = []
    for r in con.execute("select opened_t, turns from conversations"
                         " where opened_t between ? and ? order by opened_t", (lo, hi)):
        for turn in json.loads(r["turns"] or "[]"):
            if turn.get("role") == "agent" and turn.get("text"):
                said.append((float(turn.get("t") or r["opened_t"]),
                             turn.get("kind") or "statement", turn["text"]))
    con.close()
    return {"ticks": ticks, "frames": frames, "said": said}


# -- feeding it back ------------------------------------------------------------


class ReplaySource:
    """An async tick source the pipeline can pump, paced by the recorded gaps."""

    def __init__(self, ticks: list[Tick], speed: float, frame_store, frames: dict[str, bytes]):
        self.ticks = ticks
        self.speed = speed
        self.frame_store = frame_store
        self.frames = frames
        self.start_t = ticks[0].t
        self.finished = asyncio.Event()
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self._run()

    async def _run(self) -> AsyncIterator[Tick]:
        prev = None
        try:
            for tick in self.ticks:
                if prev is not None:
                    await asyncio.sleep(max(0.0, (tick.t - prev) / self.speed))
                prev = tick.t
                jpeg = self.frames.get(tick.frame_ref)
                if jpeg is not None:
                    self.frame_store.put(tick.frame_ref, jpeg, tick.t)
                self._seq += 1
                yield tick
        finally:
            self.finished.set()


def shift(tick: Tick, delta: float) -> Tick:
    update: dict[str, Any] = {"t": tick.t + delta}
    if tick.ai is not None and getattr(tick.ai, "as_of", None) is not None:
        update["ai"] = tick.ai.model_copy(update={"as_of": tick.ai.as_of + delta})
    return tick.model_copy(update=update)


async def run(args: argparse.Namespace, rec: dict[str, Any], db_path: Path) -> dict[str, Any]:
    env_file = str(args.env_file) if args.env_file else ".env"
    settings = Settings(
        _env_file=env_file,
        demo_mode=True, db_path=db_path, speech_mode="text",
        cue_trigger=args.cue_trigger, fast_path=args.fast_path,
        persona_file=args.persona,
    )
    if args.reasoner == "openai" and not settings.openai_api_key:
        raise SystemExit(f"OPENAI_API_KEY not found in {env_file}; pass --env-file or use --reasoner fake")
    pipeline = build_pipeline(settings, source="sim", reasoner_mode=args.reasoner,
                              speed=args.speed, seed_db=False)
    delta = pipeline.clock.sim_start_t - rec["ticks"][0].t
    ticks = [shift(t, delta) for t in rec["ticks"]]
    source = ReplaySource(ticks, args.speed, pipeline.frame_store, rec["frames"])
    pipeline.source = source
    clear_spoken()

    span = ticks[-1].t - ticks[0].t
    log.info("replaying %d ticks (%.0f s of session) at x%g, shift %+.0f s, %d frames",
             len(ticks), span, args.speed, delta, len(rec["frames"]))
    await pipeline.start()
    await source.finished.wait()
    log.info("ticks done; %.0f s grace for in-flight calls and the session close", args.grace)
    await asyncio.sleep(args.grace)
    persona_override = bool(pipeline.db.get_persona())
    gate_stats = pipeline.gate.stats()
    conv_stats = pipeline.conversation.stats() if pipeline.conversation else {}
    reasoner_stats = pipeline.reasoner.stats()
    await pipeline.stop()

    con = _open_ro(db_path)
    said: list[tuple[float, str, str]] = []
    conv_ids: set[str] = set()
    for r in con.execute("select id, opened_t, turns from conversations order by opened_t"):
        conv_ids.add(r["id"])
        for turn in json.loads(r["turns"] or "[]"):
            if turn.get("role") == "agent" and turn.get("text"):
                said.append((float(turn.get("t") or r["opened_t"]) - delta,
                             turn.get("kind") or "statement", turn["text"]))
    # Lines the clerk spoke directly (no conversation) show up only in `spoken`.
    conv_texts = {s[2] for s in said}
    for t, text, _urg in spoken:
        if text not in conv_texts:
            said.append((t - delta, "statement", text))
    said.sort()

    decisions = [dict(r) for r in con.execute(
        "select t, trigger, actions, spoke, drop_reason, interpretation"
        " from decisions order by t")]
    lines = [dict(r) for r in con.execute("select t, line from today_summary order by t")]
    con.close()
    return {
        "delta": delta, "said": said, "decisions": decisions, "lines": lines,
        "n_ticks": len(ticks), "span_s": span,
        "ai_cov": sum(1 for t in ticks if t.ai is not None) / max(1, len(ticks)),
        "gate": gate_stats, "conversation": conv_stats, "reasoner": reasoner_stats,
        "persona_override": persona_override,
        "n_convs": len(conv_ids),
    }


# -- the report -----------------------------------------------------------------


def hms(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def check(said: list[tuple[float, str, str]], expect: dict[str, Any]) -> list[tuple[bool, str]]:
    texts = [s[2].lower() for s in said]
    out: list[tuple[bool, str]] = []
    if "max_spoken" in expect:
        n = len(said)
        out.append((n <= expect["max_spoken"], f"spoken lines {n} <= {expect['max_spoken']}"))
    if "max_questions" in expect:
        q = sum(1 for s in said if s[1] == "question")
        out.append((q <= expect["max_questions"], f"questions {q} <= {expect['max_questions']}"))
    for word in expect.get("forbid", []):
        hits = [s for s in said if word.lower() in s[2].lower()]
        out.append((not hits, f"never says '{word}'" + (f"  ({len(hits)}x)" if hits else "")))
    for word in expect.get("require_once", []):
        n = sum(1 for t in texts if word.lower() in t)
        out.append((n == 1, f"says '{word}' exactly once (got {n})"))
    for word in expect.get("require", []):
        n = sum(1 for t in texts if word.lower() in t)
        out.append((n >= 1, f"says '{word}' at least once (got {n})"))
    return out


def report(args: argparse.Namespace, rec: dict[str, Any], res: dict[str, Any],
           checks: list[tuple[bool, str]]) -> str:
    p: list[str] = []
    p.append(f"# Replay of {args.db}  ({hms(rec['ticks'][0].t)}–{hms(rec['ticks'][-1].t)})\n")
    p.append(f"- ticks {res['n_ticks']}, {res['span_s']:.0f} s, ai coverage {res['ai_cov']:.0%}, "
             f"evidence frames {len(rec['frames'])}")
    p.append(f"- speed x{args.speed}, reasoner {args.reasoner}, cue_trigger {args.cue_trigger}, "
             f"fast_path {args.fast_path}, persona override {res['persona_override']}"
             + (f" ({args.persona})" if args.persona else ""))
    p.append(f"- clock shift {res['delta']:+.0f} s (times below are the original run's)\n")

    p.append(f"## Before: what the original run said ({len(rec['said'])})\n")
    for t, kind, text in rec["said"]:
        p.append(f"- {hms(t)} [{kind}] {text}")
    p.append("")
    p.append(f"## After: what this replay said ({len(res['said'])})\n")
    for t, kind, text in res["said"]:
        p.append(f"- {hms(t)} [{kind}] {text}")
    if not res["said"]:
        p.append("- (nothing)")
    p.append("")
    if checks:
        ok = all(c[0] for c in checks)
        p.append(f"## Expectations: {'PASS' if ok else 'FAIL'}\n")
        for good, label in checks:
            p.append(f"- {'ok  ' if good else 'FAIL'} {label}")
        p.append("")

    p.append(f"## Decisions ({len(res['decisions'])})\n")
    p.append("| time | trigger | actions | spoke | dropped | interpretation |")
    p.append("|---|---|---|---|---|---|")
    for d in res["decisions"]:
        try:
            kinds = ",".join(a.get("type", "?") for a in json.loads(d["actions"] or "[]"))
        except Exception:
            kinds = "?"
        interp = (d.get("interpretation") or "").replace("|", "/")[:70]
        p.append(f"| {hms(d['t'] - res['delta'])} | {d['trigger']} | {kinds} | "
                 f"{'yes' if d['spoke'] else ''} | {d.get('drop_reason') or ''} | {interp} |")
    p.append("")
    p.append(f"## Log lines the clerk wrote ({len(res['lines'])})\n")
    for line in res["lines"]:
        p.append(f"- {hms(line['t'] - res['delta'])} {line['line']}")
    p.append("")
    p.append("## Stats\n")
    p.append("```")
    p.append(json.dumps({"gate": res["gate"], "conversation": res["conversation"],
                         "reasoner": res["reasoner"]}, indent=1, default=str))
    p.append("```")
    return "\n".join(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path, help="recorded run's pipeline.db (opened read-only)")
    ap.add_argument("--from", dest="t_from", help="HH:MM local, on the recording's day")
    ap.add_argument("--to", dest="t_to", help="HH:MM local")
    ap.add_argument("--session", help="replay one recorded session id instead")
    ap.add_argument("--speed", type=float, default=2.0)
    ap.add_argument("--reasoner", choices=["openai", "fake"], default="openai")
    ap.add_argument("--no-cue-trigger", dest="cue_trigger", action="store_false", default=True)
    ap.add_argument("--no-fast-path", dest="fast_path", action="store_false", default=True)
    ap.add_argument("--persona", type=Path, help="persona text file (PERSONA_FILE)")
    ap.add_argument("--env-file", type=Path,
                    help="env file with the model keys (default backend/.env); never printed")
    ap.add_argument("--expect", type=Path, help="expectations JSON")
    ap.add_argument("--grace", type=float, default=45.0, help="wall seconds after the last tick")
    ap.add_argument("--out", type=Path, help="write the markdown report here")
    ap.add_argument("--keep-db", action="store_true", help="print the temp DB path and keep it")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The chatter that hides the hand-offs.
    for noisy in ("httpx", "httpcore", "openai", "pipeline.scoring", "pipeline.api.wiring"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    rec = load_recorded(args.db, args.t_from, args.t_to, args.session)
    expect = json.loads(args.expect.read_text()) if args.expect else {}
    tmp = Path(tempfile.mkdtemp(prefix="replay_")) / "pipeline.db"
    res = asyncio.run(run(args, rec, tmp))
    checks = check(res["said"], expect) if expect else []
    text = report(args, rec, res, checks)
    if args.out:
        args.out.write_text(text)
        print(f"report: {args.out}")
    else:
        print(text)
    print(f"\nbefore {len(rec['said'])} lines, after {len(res['said'])} lines"
          + (f", expectations {'PASS' if all(c[0] for c in checks) else 'FAIL'}" if checks else ""))
    for t, kind, said in res["said"]:
        print(f"  {hms(t)} [{kind}] {said}")
    if args.keep_db:
        print(f"db: {tmp}")
    return 0 if all(c[0] for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
