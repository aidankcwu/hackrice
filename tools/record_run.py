#!/usr/bin/env python3
"""Record a run: poll `/api/status`, save every sample, summarise where the time went.

    uv run python tools/record_run.py                       # localhost:8010
    uv run python tools/record_run.py --base http://10.0.0.4:8010
    uv run python tools/record_run.py --summarise runs/run_20260912_161500.jsonl

The pipeline already measures a great deal -- tick rate, AI coverage, VLM latency,
T1 latency, gate counts, a health block with `problems`. None of it is *kept*:
`/api/status` is a live snapshot that dies with the process, so after a bad demo
there is nothing to look at. This records the snapshot once a second to a JSONL
file and prints a summary at the end.

Deliberately read-only and out-of-process. It changes nothing in the pipeline, so
it cannot itself be the reason a demo behaves differently.

## What it is actually looking for

**Stalls.** `last_tick_t` is the clock that matters: if it stops advancing, capture
has frozen even though the HTTP server keeps answering happily. A stall is recorded
whenever that clock is older than `--stall` seconds, with the wall-clock time it
started and how long it lasted. That is the "where did it freeze" answer.

**Latency, split by stage.** A slow demo is slow *somewhere*, and the stages have
very different fixes: phone transit (network), VLM p50 (model/budget), T1 latency
(the reasoner), speech. They are reported separately rather than as one number.

**Silent degradation.** Coverage collapsing to 0%, escalations dropping on `t1_busy`,
frames missing from the ring -- each looks fine from the outside and ruins the demo.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# `key=value` inside the capture stat lines (`coverage= 66.7% ... p50=850ms`).
# The pipeline formats those for humans; this pulls the numbers back out without
# asking anyone to change the format.
_KV = re.compile(r"([a-z_0-9]+)=\s*([0-9.]+)")


def _get(base: str, path: str, timeout: float = 3.0) -> Any:
    with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode())


def _dig(obj: Any, *path: str, default: Any = None) -> Any:
    """`obj["a"]["b"]` that returns `default` instead of raising anywhere along the way."""
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj if obj is not None else default


def _kv(line: Any) -> dict[str, float]:
    return {k: float(v) for k, v in _KV.findall(line)} if isinstance(line, str) else {}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _fmt(value: float | None, unit: str = "", nd: int = 0) -> str:
    return "—" if value is None else f"{value:.{nd}f}{unit}"


# --- recording ----------------------------------------------------------------


def record(base: str, out: Path, interval: float, stall_after: float) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    stop = {"now": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("now", True))
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("now", True))

    print(f"recording {base} -> {out}\n  Ctrl-C to stop and summarise\n", flush=True)
    samples = 0
    errors = 0
    last_line = ""
    with out.open("w") as fh:
        while not stop["now"]:
            wall = time.time()
            try:
                status = _get(base, "/api/status")
                row = {"wall": wall, "ok": True, "status": status}
                errors = 0
            except Exception as exc:  # noqa: BLE001 - an unreachable backend is data
                errors += 1
                row = {"wall": wall, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            fh.flush()  # a crashed demo must still leave a readable file
            samples += 1

            if row["ok"]:
                status = row["status"]
                age = wall - _dig(status, "last_tick_t", default=wall)
                health_ok = _dig(status, "health", "ok", default=True)
                mark = "STALL" if age > stall_after else ("PROB" if not health_ok else "ok")
                last_line = (
                    f"\r[{samples:5d}] {mark:<5} ticks={_dig(status,'tick_count',default=0):<6}"
                    f" tick_age={age:5.1f}s"
                    f" cov={_dig(status,'ai_coverage',default=0.0)*100:5.1f}%"
                    f" t1_busy={str(_dig(status,'t1_busy',default=False)):<5}"
                )
            else:
                last_line = f"\r[{samples:5d}] DOWN  {row['error'][:60]}"
            print(last_line + " " * 8, end="", flush=True)
            time.sleep(interval)

    print("\n")
    # Decisions carry the T1 latency the status block only shows for the last one.
    decisions: list[dict] = []
    try:
        raw = _get(base, "/api/decisions?limit=500")
        decisions = raw if isinstance(raw, list) else raw.get("decisions", [])
    except Exception as exc:  # noqa: BLE001
        print(f"(could not fetch decisions for latency stats: {exc})")
    if decisions:
        with out.with_suffix(".decisions.json").open("w") as fh:
            json.dump(decisions, fh, indent=2)

    summarise(out, decisions=decisions, stall_after=stall_after)
    return 0


# --- summarising --------------------------------------------------------------


def summarise(path: Path, decisions: list[dict] | None = None,
              stall_after: float = 5.0) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        print("no samples recorded")
        return

    good = [r for r in rows if r.get("ok")]
    down = [r for r in rows if not r.get("ok")]
    span = rows[-1]["wall"] - rows[0]["wall"]

    print("=" * 72)
    print(f"RUN SUMMARY  {path}")
    print("=" * 72)
    print(f"  duration       {span:.0f} s over {len(rows)} samples"
          f"   ({len(down)} with the backend unreachable)")
    if not good:
        print("  backend never answered — nothing else to report")
        return

    first, last = good[0]["status"], good[-1]["status"]
    print(f"  source         {_dig(first,'source',default='?')}"
          f"   reasoner={_dig(first,'reasoner_mode',default='?')}"
          f"   tick_interval={_dig(first,'tick_interval_s',default='?')}s")

    # Stalls are found before throughput is printed: an average tick rate over a run
    # that was frozen a third of the time still reads as healthy, so the throughput
    # line has to carry the stalled total next to it or it actively misleads.
    stalls: list[tuple[float, float]] = []
    _run_start: float | None = None
    for r in good:
        age = r["wall"] - _dig(r["status"], "last_tick_t", default=r["wall"])
        if age > stall_after:
            _run_start = r["wall"] - age if _run_start is None else _run_start
        elif _run_start is not None:
            stalls.append((_run_start, r["wall"] - _run_start))
            _run_start = None
    if _run_start is not None:
        stalls.append((_run_start, good[-1]["wall"] - _run_start))
    stalled_s = sum(length for _, length in stalls)

    # -- throughput: did it actually run at the cadence it claims? --
    ticks = _dig(last, "tick_count", default=0) - _dig(first, "tick_count", default=0)
    want = _dig(first, "tick_interval_s", default=1.5) or 1.5
    # Measured across the samples that *answered*, not the whole wall clock. A
    # backend that died halfway would otherwise be reported as a slow tick rate,
    # which points at the VLM budget instead of at the crash that actually happened.
    observed = good[-1]["wall"] - good[0]["wall"]
    print("\n-- THROUGHPUT " + "-" * 58)
    print(f"  ticks          {ticks} in {observed:.0f} s of reachable backend"
          f"  ->  {ticks / observed if observed else 0:.2f}/s"
          f"   (configured {1 / want:.2f}/s)")
    if down:
        print(f"                 {len(down)} sample(s) excluded: backend unreachable")
    if stalled_s > 0:
        print(f"  STALLED        {stalled_s:.0f} s of {observed:.0f} s"
              f"  ({stalled_s / observed * 100 if observed else 0:.0f}% of the run)"
              f"  -- the rate above is an average across that")
    covs = [_dig(r["status"], "ai_coverage", default=None) for r in good]
    covs = [c * 100 for c in covs if isinstance(c, (int, float))]
    print(f"  AI coverage    last={_fmt(covs[-1] if covs else None,'%',1)}"
          f"  min={_fmt(min(covs) if covs else None,'%',1)}"
          f"  median={_fmt(statistics.median(covs) if covs else None,'%',1)}")

    # -- stalls: the freeze detector --
    print("\n-- STALLS " + "-" * 62)
    if not stalls and not down:
        print(f"  none — the tick clock never fell more than {stall_after:.0f}s behind")
    for start, length in stalls:
        rel = start - rows[0]["wall"]
        print(f"  capture frozen  at +{rel:6.0f}s  for {length:5.1f}s"
              f"   ({time.strftime('%H:%M:%S', time.localtime(start))})")
    if down:
        rel = down[0]["wall"] - rows[0]["wall"]
        print(f"  backend unreachable for {len(down)} sample(s), first at +{rel:.0f}s")

    # -- latency, split by stage --
    print("\n-- LATENCY BY STAGE " + "-" * 52)
    cap = [_kv(_dig(r["status"], "capture", "tagger")) for r in good]
    vlm_p50 = [c["p50"] for c in cap if "p50" in c]
    phone = [_kv(_dig(r["status"], "capture", "loop")) for r in good]
    if vlm_p50:
        print(f"  VLM (T0)       p50 {_fmt(vlm_p50[-1],'ms')}"
              f"   worst sample {_fmt(max(vlm_p50),'ms')}")
    else:
        print("  VLM (T0)       — (no capture block; --source sim, or VLM off)")
    lat = [d["latency_ms"] for d in (decisions or [])
           if isinstance(d.get("latency_ms"), (int, float)) and d["latency_ms"] > 0]
    if lat:
        print(f"  T1 reasoner    n={len(lat)}  p50 {_fmt(_pct(lat,.5),'ms')}"
              f"  p95 {_fmt(_pct(lat,.95),'ms')}  max {_fmt(max(lat),'ms')}")
    else:
        print("  T1 reasoner    — (no completed escalations with a latency)")
    t1_last = _dig(last, "health", "t1", default={})
    if t1_last:
        print(f"  T1 deadline    {t1_last.get('deadline_s','?')}s"
              f"   model={t1_last.get('model','?')}")

    # -- what was dropped, and why. Each of these is a silent demo-killer. --
    print("\n-- DROPS & SUPPRESSION " + "-" * 49)
    gate = _dig(last, "gate", default={})
    print(f"  gate fired     {gate.get('fired', {})}")
    print(f"  gate dropped   {gate.get('dropped', 0)}"
          f"   suppressed(cooldown) {gate.get('suppressed', {})}")
    for key, label in (("dropped_busy", "T1 busy"), ("dropped_timeout", "T1 timeout"),
                       ("dropped_error", "T1 error"), ("frames_missing", "frames missing")):
        value = t1_last.get(key, 0)
        if value:
            print(f"  {label:<14} {value}")
    print(f"  escalations    {t1_last.get('escalations', 0)}"
          f"  completed {t1_last.get('completed', 0)}"
          f"  spoke {t1_last.get('spoke', 0)}")
    print(f"  speech         allowed {t1_last.get('speech_allowed', 0)}"
          f"  suppressed {t1_last.get('speech_suppressed', 0)}"
          f"  mode={_dig(last,'health','speech','mode',default='?')}")

    # -- phone link, only meaningful with real glasses --
    ph = _dig(last, "health", "phone", default=None)
    if ph:
        print("\n-- PHONE LINK " + "-" * 58)
        for k, v in ph.items():
            print(f"  {k:<20} {v}")

    # -- every distinct health problem seen, with when it first appeared --
    print("\n-- HEALTH PROBLEMS " + "-" * 53)
    seen: dict[str, float] = {}
    for r in good:
        for p in _dig(r["status"], "health", "problems", default=[]) or []:
            seen.setdefault(str(p), r["wall"] - rows[0]["wall"])
    if not seen:
        print("  none reported")
    for p, when in sorted(seen.items(), key=lambda kv: kv[1]):
        print(f"  +{when:6.0f}s  {p}")
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="record and summarise a pipeline run")
    ap.add_argument("--base", default="http://localhost:8010", help="backend base URL")
    ap.add_argument("--out", help="output JSONL (default runs/run_<timestamp>.jsonl)")
    ap.add_argument("--interval", type=float, default=1.0, help="poll seconds")
    ap.add_argument("--stall", type=float, default=5.0,
                    help="tick clock older than this counts as frozen")
    ap.add_argument("--summarise", help="summarise an existing recording and exit")
    args = ap.parse_args()

    if args.summarise:
        path = Path(args.summarise)
        if not path.exists():
            sys.exit(f"no such recording: {path}")
        decisions_path = path.with_suffix(".decisions.json")
        decisions = json.loads(decisions_path.read_text()) if decisions_path.exists() else []
        summarise(path, decisions=decisions, stall_after=args.stall)
        return 0

    out = Path(args.out) if args.out else Path("runs") / (
        f"run_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    return record(args.base.rstrip("/"), out, args.interval, args.stall)


if __name__ == "__main__":
    sys.exit(main())
