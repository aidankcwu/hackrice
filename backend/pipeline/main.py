"""Command-line entry point for the pipeline and dashboard API."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path

import uvicorn

from .api.app import create_app
from .api.wiring import build_pipeline
from .bus import Subscription
from .config import Settings
from .db import Database
from .models import Tick


def format_tick(tick: Tick) -> str:
    """Compact diagnostic representation retained for library callers."""
    if tick.ai is None:
        tags, flags = "ai:-", ""
    else:
        tags = f"{tick.ai.scene}/{tick.ai.activity}"
        names = [name for name, value in (
            ("food", tick.ai.food_present), ("screen", tick.ai.screen_present),
            ("people", tick.ai.people_present),
            ("caffeine", tick.ai.caffeine_visible),
            ("alcohol", tick.ai.alcohol_visible),
            ("green", tick.ai.vegetation_visible),
        ) if value]
        flags = " " + ",".join(names) if names else ""
    return (f"#{tick.seq:05d} {tick.tick_id} lux={tick.sensor.lux_proxy:7.1f} "
            f"d={tick.sensor.frame_delta:.3f} {tags}{flags}")


async def tick_store_consumer(sub: Subscription, db: Database) -> None:
    """Compatibility helper; production wiring uses its combined consumer."""
    async for tick in sub:
        db.insert_tick(tick)


async def run_headless(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = build_pipeline(
        settings, source=args.source, reasoner_mode=args.reasoner,
        speed=args.speed, seed_db=not args.no_seed, dir=args.dir, loop=args.loop,
        camera=args.camera, vlm=args.vlm, flow=args.flow,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await pipeline.start()
    try:
        await stop.wait()
    finally:
        await pipeline.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("--source", choices=["sim", "glasses", "webcam", "replay"],
                        default="sim")
    parser.add_argument("--dir", help="frame corpus directory for replay")
    parser.add_argument("--loop", action="store_true", help="loop a replay corpus")
    parser.add_argument("--camera", type=int, default=0, help="webcam device index")
    parser.add_argument("--vlm", choices=["gemini", "fake", "off"], default="gemini",
                        help="T0 tagger; fake/off need no Gemini key")
    parser.add_argument("--flow", choices=["numpy", "opencv", "off"], default=None)
    parser.add_argument("--speed", type=float, default=1.0)
    # The capture cadence, not the playback rate: `--speed` compresses wall
    # time, this changes how many ticks a scenario second produces.
    parser.add_argument("--tick-interval", dest="tick_interval_s", type=float,
                        default=defaults.tick_interval_s,
                        help="seconds between ticks (glasses emit every 1.5 s)")
    parser.add_argument("--reasoner", choices=["openai", "fake"], default="fake")
    parser.add_argument("--db", default=str(defaults.db_path))
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--demo-mode", dest="demo_mode", action="store_true",
                        default=defaults.demo_mode)
    parser.add_argument("--no-demo-mode", dest="demo_mode", action="store_false")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings(demo_mode=args.demo_mode, db_path=Path(args.db),
                        tick_interval_s=args.tick_interval_s)
    if args.reasoner == "openai" and not settings.openai_api_key:
        raise SystemExit(
            "OPENAI_API_KEY is required for --reasoner openai; "
            "use --reasoner fake otherwise"
        )
    if args.headless:
        try:
            return asyncio.run(run_headless(args, settings))
        except KeyboardInterrupt:
            return 0
    app = create_app(
        settings=settings, source=args.source, reasoner_mode=args.reasoner,
        speed=args.speed, seed_db=not args.no_seed, dir=args.dir, loop=args.loop,
        camera=args.camera, vlm=args.vlm, flow=args.flow,
    )
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
