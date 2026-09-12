"""Smoke harness: wire a tick source to the bus, the store and SQLite.

This is scaffolding, not the demo path. Later subtasks replace it with FastAPI
startup wiring; for now it proves the foundation end to end:

    uv run python -m pipeline.main --source sim --speed 20

Runs until Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

from .bus import Subscription, TickBus
from .config import Settings
from .db import Database
from .frames import InMemoryFrameStore
from .models import Tick
from .sim import DEFAULT_SCENARIO, SimSource

log = logging.getLogger("pipeline.main")


def format_tick(tick: Tick) -> str:
    """One compact line per tick, for watching the stream go by."""

    if tick.ai is None:
        tags = "ai:-"
        flags = ""
    else:
        ai = tick.ai
        tags = f"{ai.scene}/{ai.activity}"
        on = [
            name
            for name, value in (
                ("food", ai.food_present),
                ("screen", ai.screen_present),
                ("people", ai.people_present),
                ("caffeine", ai.caffeine_visible),
                ("alcohol", ai.alcohol_visible),
                ("green", ai.vegetation_visible),
            )
            if value
        ]
        flags = " " + ",".join(on) if on else ""
    return (
        f"#{tick.seq:05d} {tick.tick_id} lux={tick.sensor.lux_proxy:7.1f} "
        f"d={tick.sensor.frame_delta:.3f} {tags}{flags}"
    )


async def tick_store_consumer(sub: Subscription, db: Database) -> None:
    """Write every tick it manages to consume to SQLite (SPEC §2.5: no pixels)."""

    async for tick in sub:
        await asyncio.to_thread(db.insert_tick, tick)


async def printer_consumer(sub: Subscription) -> None:
    async for tick in sub:
        sys.stdout.write(format_tick(tick) + "\n")
        sys.stdout.flush()


async def run(args: argparse.Namespace) -> int:
    settings = Settings(demo_mode=args.demo_mode, db_path=Path(args.db))
    log.info(
        "starting: source=%s speed=%s demo_mode=%s db=%s",
        args.source,
        args.speed,
        settings.demo_mode,
        settings.db_path,
    )

    db = Database(settings.db_path).connect().init_schema()
    frames = InMemoryFrameStore(ttl_s=settings.frame_ttl_s)
    bus = TickBus()

    if args.source != "sim":  # argparse already constrains this
        raise SystemExit(f"unknown source: {args.source}")
    source = SimSource(DEFAULT_SCENARIO, frames, speed=args.speed)

    store_sub = bus.subscribe("tick_store")
    print_sub = bus.subscribe("printer")
    consumers = [
        asyncio.create_task(tick_store_consumer(store_sub, db)),
        asyncio.create_task(printer_consumer(print_sub)),
    ]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def pump() -> None:
        async for tick in source:
            bus.publish(tick)
            if stop.is_set():
                break

    pump_task = asyncio.create_task(pump())
    try:
        await asyncio.wait(
            [pump_task, asyncio.create_task(stop.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:  # pragma: no cover - signal path
        pass
    finally:
        pump_task.cancel()
        bus.close()
        for task in consumers:
            task.cancel()
        await asyncio.gather(pump_task, *consumers, return_exceptions=True)
        stats = db.stats()
        log.info(
            "stopped: published=%d stored=%d ai=%d dropped=%d frames_held=%d",
            bus.published,
            stats["tick_count"],
            stats["ai_tick_count"],
            bus.total_dropped,
            len(frames),
        )
        db.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    p.add_argument("--source", choices=["sim"], default="sim")
    p.add_argument("--speed", type=float, default=1.0, help="tick rate multiplier")
    p.add_argument("--db", default="./data/pipeline.db")
    p.add_argument(
        "--demo-mode",
        dest="demo_mode",
        action="store_true",
        default=True,
        help="short cooldowns and rate limits (SPEC §6)",
    )
    p.add_argument("--no-demo-mode", dest="demo_mode", action="store_false")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
