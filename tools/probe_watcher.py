"""Calibrate the watcher's per-concept thresholds against Gemini booleans (PERCEPTION.md, "Calibration").

Scores every corpus frame with the watcher model, compares each §9 boolean's score
against what Gemini said about the same frame, and sweeps the enter threshold for
the target recall at the lowest false-wake rate. Exit is two thirds of enter.

    uv run python tools/probe_watcher.py --corpus corpus/ [--labels labels.jsonl]
        [--model mobileclip2-s0|mobileclip2-s2|fake] [--out thresholds.json]
        [--recall 0.90] [--limit N]

Without `--labels`, each frame is labelled once with Gemini (needs GEMINI_API_KEY)
and appended to `labels.jsonl` inside the corpus directory; frames already in that
file are never re-labelled. `--out` writes the WATCH_THRESHOLDS_JSON shape plus a
"_meta" key (the backend logs and ignores "_meta").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from longevity.ai_fields import BOOL_FIELDS, coerce  # noqa: E402
from longevity.sensors import quick_quality  # noqa: E402
from longevity.sources.replay import ReplayCorpusError, scan_corpus  # noqa: E402
from longevity.watcher import WatcherConfig  # noqa: E402
from longevity.watcher_model import bank_from_prompts, build_watcher_model, scores  # noqa: E402

USAGE = "usage: probe_watcher.py --corpus DIR (or set CORPUS_DIR) [--labels F] [--model M] [--out F]"
#: Mirrors backend/pipeline/capture/settings.py DEFAULT_ENTER / DEFAULT_EXIT.
DEFAULT_ENTER, DEFAULT_EXIT = 0.60, 0.40
MIN_POSITIVES = 3
LABEL_CONCURRENCY = 4
SWEEP = [round(0.05 + 0.01 * i, 2) for i in range(91)]  # 0.05 .. 0.95


def read_labels(path: Path) -> dict[str, dict[str, Any]]:
    """{file name: coerced ai block}; the last line for a name wins, bad lines are skipped."""
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            out[row["file"]] = coerce(row["ai"])
        except (ValueError, KeyError, TypeError):
            continue
    return out


async def label_missing(files: list[Path], path: Path) -> dict[str, dict[str, Any]]:
    """Gemini-label every frame not already in `path`, appending as each call lands."""
    from dotenv import load_dotenv

    from longevity.vlm import GeminiClient

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    labels = read_labels(path)
    todo = [f for f in files if f.name not in labels]
    if not todo:
        return labels
    client = GeminiClient()
    sem = asyncio.Semaphore(LABEL_CONCURRENCY)
    print(f"labelling {len(todo)} frame(s) with Gemini -> {path}")

    async def one(f: Path) -> None:
        async with sem:
            try:
                ai = coerce(await client.tag(f.read_bytes()))
            except Exception as e:  # noqa: BLE001 - a probe, report and keep going
                print(f"  {f.name}  ERROR {type(e).__name__}: {e}")
                return
        labels[f.name] = ai
        with path.open("a") as fh:
            fh.write(json.dumps({"file": f.name, "ai": ai}) + "\n")

    await asyncio.gather(*(one(f) for f in todo))
    await client.aclose()
    return labels


def sweep(pos: list[float], neg: list[float], target: float) -> tuple[float, float, float, str]:
    """(enter, recall, false_wake, note) for one concept's positive and negative scores."""
    def rates(t: float) -> tuple[float, float]:
        recall = sum(s >= t for s in pos) / len(pos)
        fw = sum(s >= t for s in neg) / len(neg) if neg else 0.0
        return recall, fw

    # Recall only falls as enter rises, so the highest qualifying enter has the lowest
    # false-wake rate among those that reach the target.
    ok = [t for t in SWEEP if rates(t)[0] >= target]
    if ok:
        enter, note = max(ok), ""
    else:
        enter, note = max(SWEEP, key=lambda t: rates(t)[0] - rates(t)[1]), "low-recall"
    return (enter, *rates(enter), note)


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))] if s else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default=os.environ.get("CORPUS_DIR"))
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--model", default="mobileclip2-s0",
                    choices=["mobileclip2-s0", "mobileclip2-s2", "fake"])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--recall", type=float, default=0.90)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    if not args.corpus:
        print(USAGE)
        return 2
    try:
        entries, _ = scan_corpus(args.corpus)
    except ReplayCorpusError as e:
        print(e)
        return 2
    files = [e.path for e in entries]
    if args.limit:
        files = files[:: max(1, len(files) // args.limit)][: args.limit]

    if args.labels:
        labels = read_labels(args.labels)
    else:
        labels = asyncio.run(label_missing(files, Path(args.corpus) / "labels.jsonl"))

    model = build_watcher_model(args.model)
    bank, groups, null = bank_from_prompts(model)
    cfg = WatcherConfig()
    per_frame: dict[str, dict[str, float]] = {}
    ms: list[float] = []
    gated = 0
    for f in files:
        jpeg = f.read_bytes()
        t0 = time.perf_counter()
        per_frame[f.name] = scores(model.embed(jpeg), bank, groups, null)
        ms.append((time.perf_counter() - t0) * 1000)
        q = quick_quality(jpeg)
        if q["sharpness"] < cfg.min_sharpness or q["lux_proxy"] < cfg.min_lux:
            gated += 1

    labelled = [n for n in per_frame if n in labels]
    print(f"model={model.name}  frames={len(files)}  labelled={len(labelled)}  "
          f"target recall={args.recall:.2f}\n")
    header = (f"{'concept':<26} {'pos':>4} {'neg':>4} {'enter':>6} {'exit':>6} "
              f"{'recall':>7} {'false_wake':>10}  note")
    print(header)
    print("-" * len(header))

    calibrated: dict[str, Any] = {}
    for c in BOOL_FIELDS:
        pos = [per_frame[n][c] for n in labelled if labels[n].get(c) is True]
        neg = [per_frame[n][c] for n in labelled if labels[n].get(c) is not True]
        if len(pos) < MIN_POSITIVES:
            enter, exit_, note = DEFAULT_ENTER, DEFAULT_EXIT, "insufficient positives"
            recall = f"{sum(s >= enter for s in pos) / len(pos):.2f}" if pos else "-"
            fw = f"{sum(s >= enter for s in neg) / len(neg):.2f}" if neg else "-"
        else:
            enter, r, w, note = sweep(pos, neg, args.recall)
            exit_ = round(0.67 * enter, 2)
            recall, fw = f"{r:.2f}", f"{w:.2f}"
            calibrated[c] = {"enter": enter, "exit": exit_}
        print(f"{c:<26} {len(pos):>4} {len(neg):>4} {enter:>6.2f} {exit_:>6.2f} "
              f"{recall:>7} {fw:>10}  {note}")

    p50, p99 = pct(ms, 0.5), pct(ms, 0.99)
    print(f"\ninference  p50 {p50:.1f}ms  p99 {p99:.1f}ms  (target 10 / 30 ms)")
    print(f"quality    {gated}/{len(files)} frame(s) excluded by the gate "
          f"(sharpness < {cfg.min_sharpness:g} or lux < {cfg.min_lux:g})")

    if args.out:
        calibrated["_meta"] = {
            "model": model.name, "frames": len(files), "labelled": len(labelled),
            "ms_p50": round(p50, 2), "ms_p99": round(p99, 2),
            "recall_target": args.recall, "date": date.today().isoformat(),
        }
        args.out.write_text(json.dumps(calibrated, indent=2) + "\n")
        print(f"wrote      {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
