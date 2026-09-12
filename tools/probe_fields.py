"""Run real corpus frames through the §9 field set and report tags + latency.

This is a validation probe, not the production path — `vlm.py` (A10) owns that. The
question this answers is the one CLAUDE.md flags as open: §9 is AI-generated and "not
fully reasoned," so does the field set actually survive contact with glasses-POV
frames, and does Flash-Lite return inside the 1 s budget (§2.4)?

    uv run python tools/probe_fields.py [--model M] [--n 12]
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from longevity.ai_fields import PROMPT, coerce, response_schema  # noqa: E402

TRUE_FIELDS = [
    "food_present", "caffeine_visible", "alcohol_visible",
    "screen_present", "vegetation_visible", "people_present",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-2.5-flash-lite")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--dir", default="corpus")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("frame_*.jpg"))
    if not files:
        print(f"no frames in {args.dir}/")
        return 2
    step = max(1, len(files) // args.n)
    sample = files[::step][: args.n]

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=response_schema(),
        system_instruction=PROMPT,
    )

    print(f"model={args.model}  frames={len(sample)}\n")
    header = f"{'frame':>6} {'ms':>6}  {'scene':<11} {'activity':<10} {'food':<11} flags"
    print(header)
    print("-" * len(header))

    latencies: list[float] = []
    fails = 0
    for f in sample:
        jpeg = f.read_bytes()
        t0 = time.perf_counter()
        try:
            resp = client.models.generate_content(
                model=args.model,
                contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")],
                config=cfg,
            )
            ms = (time.perf_counter() - t0) * 1000
            import json

            tags = coerce(json.loads(resp.text))
        except Exception as e:  # noqa: BLE001 — a probe, report and keep going
            ms = (time.perf_counter() - t0) * 1000
            print(f"{f.name[6:16]:>6} {ms:6.0f}  ERROR {type(e).__name__}: {e}")
            fails += 1
            continue

        latencies.append(ms)
        flags = " ".join(k.split("_")[0] for k in TRUE_FIELDS if tags[k])
        over = "!" if ms > 1000 else " "
        print(
            f"{f.name[12:16]:>6} {ms:6.0f}{over} {tags['scene']:<11} "
            f"{tags['activity']:<10} {tags['food_type']:<11} {flags}  c={tags['conf']:.2f}"
        )

    if not latencies:
        print("\nall calls failed")
        return 1

    latencies.sort()
    p = lambda q: latencies[min(len(latencies) - 1, int(len(latencies) * q))]  # noqa: E731
    under = sum(1 for m in latencies if m <= 1000)
    print(
        f"\nlatency  p50 {statistics.median(latencies):.0f}ms  "
        f"p90 {p(0.9):.0f}ms  max {max(latencies):.0f}ms"
    )
    print(
        f"budget   {under}/{len(latencies)} under the 1s budget "
        f"({100 * under / len(latencies):.0f}% coverage; §2.4 expects 50-80%)"
    )
    if fails:
        print(f"errors   {fails}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
