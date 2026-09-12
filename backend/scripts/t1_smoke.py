"""One real T1 call against the OpenAI Responses API.

Builds a plausible escalation out of :class:`~pipeline.sim.SimSource` ticks --
real placeholder JPEGs, real phashes, the ``ai`` block missing on a third of
them -- assembles the full context envelope, and makes one live call.

    cd backend && uv run python scripts/t1_smoke.py
    cd backend && uv run python scripts/t1_smoke.py --segment lunch_restaurant

Not a test: it costs money and needs a key. It exists to prove the envelope is
accepted and the strict schema round-trips before the demo depends on it.
If the configured model is rejected the script walks a fallback list and prints
which name worked, so the supervisor can set ``T1_MODEL`` accordingly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import Settings  # noqa: E402
from pipeline.models import Escalation  # noqa: E402
from pipeline.reasoner.client import OpenAIReasonerClient  # noqa: E402
from pipeline.reasoner.envelope import build_envelope, select_frames  # noqa: E402
from pipeline.reasoner.prompts import DEFAULT_PERSONA  # noqa: E402
from pipeline.reasoner.schema import normalize  # noqa: E402
from pipeline.sim import DEFAULT_SCENARIO, SimSource  # noqa: E402
from pipeline.frames import InMemoryFrameStore  # noqa: E402

#: Tried in order after the configured name. Do not edit .env from here.
FALLBACK_MODELS = ["gpt-5.4", "gpt-5", "gpt-4.1-mini"]

SEVEN_DAY = (
    "Screen time averaged 9.4 h/day (target under 8). Outdoor light before "
    "10:00 happened on 2 of 7 days. Caffeine after 15:00 on 5 of 7 days, and "
    "sleep onset was late on exactly those nights. Two social episodes a day. "
    "Nature dose 65 min against a 120 min weekly target."
)

TRIGGER_BY_SEGMENT = {
    "lunch_restaurant": ("food_in_frame", "food_present on 4 of the last 10 ticks"),
    "desk_coffee": ("caffeine_seen", "caffeine_visible first seen this window"),
    "home_wine": ("alcohol_seen", "alcohol_visible first seen this window"),
    "office_screen": ("screen_sustained", "screen_present on 18 of the last 20 ticks"),
    "park": ("outdoor_sustained", "scene=park with vegetation for 20 s"),
}


def build_escalation(segment_name: str, window_s: int) -> tuple[Escalation, InMemoryFrameStore]:
    """Run the sim until ``segment_name`` has filled the window, then escalate."""

    store = InMemoryFrameStore(ttl_s=90.0)
    source = SimSource(DEFAULT_SCENARIO, frame_store=store, seed=7, ai_coverage=0.65)

    # Find where the segment starts in the script.
    offset = 0.0
    for seg in DEFAULT_SCENARIO.segments:
        if seg.name == segment_name:
            break
        offset += seg.duration_s
    else:  # pragma: no cover - argparse restricts the choices
        raise SystemExit(f"unknown segment {segment_name!r}")

    end_seq = int(offset) + min(
        window_s,
        int(next(s.duration_s for s in DEFAULT_SCENARIO.segments if s.name == segment_name)),
    )
    ticks = [source.next_tick() for _ in range(end_seq)]
    window = ticks[-window_s:]

    trigger, reason = TRIGGER_BY_SEGMENT.get(
        segment_name, ("stillness", "low motion sustained")
    )
    esc = Escalation(
        trigger=trigger,
        t=window[-1].t,
        tick=window[-1],
        window=window,
        reason=reason,
    )
    return esc, store


def describe(esc: Escalation, messages: list[dict]) -> None:
    content = messages[1]["content"]
    images = [i for i in content if i["type"] == "input_image"]
    text_chars = sum(len(i.get("text", "")) for i in content if i["type"] == "input_text")
    image_bytes = sum(len(i["image_url"]) for i in images)
    print(f"trigger      : {esc.trigger} — {esc.reason}")
    print(f"window       : {len(esc.window)} ticks, "
          f"{sum(1 for t in esc.window if t.ai is not None)} with an ai block")
    print(f"envelope     : {len(content)} content items, {len(images)} images")
    print(f"              {text_chars} text chars, {image_bytes // 1024} KB of base64")
    print()
    print("--- tick table ---")
    print(content[2]["text"])
    print()
    print("--- frame labels ---")
    for i, item in enumerate(content):
        if item["type"] == "input_image":
            print(" ", content[i - 1]["text"])
    print()


async def call(model: str, messages: list[dict], api_key: str):
    client = OpenAIReasonerClient(api_key, model)
    started = time.perf_counter()
    resp, meta = await client.complete(messages)
    meta.setdefault("latency_ms", int((time.perf_counter() - started) * 1000))
    return resp, meta


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--segment",
        default="lunch_restaurant",
        choices=sorted(s.name for s in DEFAULT_SCENARIO.segments),
    )
    parser.add_argument("--window", type=int, default=30, help="window length in ticks")
    parser.add_argument("--model", default=None, help="override T1_MODEL")
    args = parser.parse_args()

    settings = Settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not set; nothing to smoke.", file=sys.stderr)
        return 2

    esc, store = build_escalation(args.segment, args.window)
    selected = select_frames(esc.window, esc.tick, k=4)
    frames = store.get([t.frame_ref for t in selected])
    messages = build_envelope(esc, frames, [], SEVEN_DAY, DEFAULT_PERSONA)

    describe(esc, messages)

    candidates = [args.model or settings.t1_model] + [
        m for m in FALLBACK_MODELS if m != (args.model or settings.t1_model)
    ]

    for model in candidates:
        print(f"--- calling {model} ---")
        try:
            resp, meta = await call(model, messages, settings.openai_api_key)
        except Exception as exc:
            print(f"    FAILED: {type(exc).__name__}: {exc}\n")
            continue

        norm = normalize(resp, t=esc.t)
        print(f"    OK in {meta['latency_ms']} ms (model reported: {meta['model']})")
        if meta.get("usage"):
            usage = meta["usage"]
            print(
                "    tokens: in="
                f"{usage.get('input_tokens')} out={usage.get('output_tokens')}"
            )
        print()
        print(f"    interpretation : {norm.interpretation}")
        print(f"    confidence     : {norm.confidence}")
        print("    actions        :")
        for action in norm.actions:
            print("      " + json.dumps(action.model_dump(), ensure_ascii=False))
        print()
        print(f"WORKING MODEL: {model}")
        return 0

    print("No candidate model accepted the request.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
