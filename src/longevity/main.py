"""`t0` — the T0 entry point (PERSON_A.md, the goal line).

    uv run t0 --source glasses            # the demo path
    uv run t0 --source webcam             # proves the pipeline with no phone
    uv run t0 --source replay --dir corpus/ --speed 10

"Goal: `--source glasses` emits a valid §12 tick object, once per second, continuously."

The HTTP server runs under every adapter, not just `glasses`: Person B needs
`GET /frames` for escalation regardless of where the frames came from.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# Keys live in a gitignored .env at the repo root. Loaded here, at the entry point,
# rather than inside vlm.py — importing a module should not reach out and mutate the
# process environment. `override=False` so a real exported env var still wins.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from .emit import JSONLWriter, SQLiteMirror, TickBus
from .loop import T0Loop
from .ring import FrameRing
from .server import ingest
from .server.app import create_app
from .sources.base import CaptureSource
from .vlm import build_client, T0Tagger

log = logging.getLogger("t0")


def build_source(args: argparse.Namespace, link: ingest.GlassesLink) -> CaptureSource:
    if args.source == "glasses":
        from .sources.glasses import GlassesSource

        return GlassesSource(link)
    if args.source == "webcam":
        from .sources.webcam import WebcamSource

        return WebcamSource(index=args.camera)
    from .sources.replay import ReplaySource

    if not args.dir:
        sys.exit("--source replay needs --dir pointing at a corpus of frame_<millis>.jpg")
    return ReplaySource(args.dir, speed=args.speed, loop=args.loop)


async def run(args: argparse.Namespace) -> int:
    # ONE ring, shared by the capture loop and the HTTP app. If these are two
    # instances the loop fills one while /frames reads the other, and every escalation
    # returns 410 with no other symptom — a silent failure worth being explicit about.
    ring = FrameRing()
    link = ingest.GlassesLink()
    app = create_app(link=link, ring=ring)

    bus = TickBus()
    mirror = SQLiteMirror(args.db) if args.db else None
    if mirror:
        mirror.start()
    jsonl = JSONLWriter(args.jsonl) if args.jsonl else None

    tagger = T0Tagger(build_client(args.vlm))
    source = build_source(args, link)
    loop = T0Loop(
        source, ring=ring, tagger=tagger, bus=bus, mirror=mirror, jsonl=jsonl,
        flow=args.flow,
    )

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(), name="http")

    stop = asyncio.Event()
    with contextlib.suppress(NotImplementedError):
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    print(
        f"T0 up  source={args.source}  vlm={args.vlm}  http=http://{args.host}:{args.port}\n"
        f"       GET /health · GET /frames?refs=… · WS /ws/glasses"
        + (f"\n       ticks -> {args.jsonl}" if args.jsonl else "")
        + (f"\n       mirror -> {args.db}" if args.db else ""),
        flush=True,
    )

    loop_task = asyncio.create_task(loop.run(), name="t0-loop")
    stop_task = asyncio.create_task(stop.wait(), name="stop")
    done, _ = await asyncio.wait(
        {loop_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
    )

    for task in (loop_task, stop_task):
        if not task.done():
            task.cancel()
    server.should_exit = True
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await loop_task
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await server_task

    await source.aclose()
    if mirror:
        mirror.close()
    if jsonl:
        jsonl.close()

    print(f"\nT0 down · {loop.stats.line()}", flush=True)
    print(f"          {tagger.stats_line()}", flush=True)
    if loop_task in done and loop_task.exception():
        log.error("capture loop failed", exc_info=loop_task.exception())
        return 1
    return 0


def cli() -> None:
    p = argparse.ArgumentParser(prog="t0", description="T0 tick producer")
    p.add_argument("--source", choices=["glasses", "webcam", "replay"], default="glasses")
    p.add_argument("--dir", help="corpus directory for --source replay")
    p.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    p.add_argument("--loop", action="store_true", help="replay the corpus on repeat")
    p.add_argument("--camera", type=int, default=0, help="webcam index")
    p.add_argument("--vlm", choices=["gemini", "fake", "off"], default="gemini")
    p.add_argument("--flow", choices=["numpy", "opencv", "off"], default=None,
                   help="flow_mag backend (default: numpy phase correlation)")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--jsonl", help="also append every tick to this .jsonl file")
    p.add_argument("--db", help="mirror ticks to this SQLite file")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    cli()
