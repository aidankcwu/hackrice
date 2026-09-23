"""Command-line entry point for the pipeline and dashboard API."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

import uvicorn

from .api.app import create_app
from .api.auth import install_log_redaction
from .api.wiring import build_pipeline
from .bus import Subscription
from .config import Settings
from .db import Database
from .models import Tick


def fresh_database(path: Path) -> list[Path]:
    """Remove SQLite's database and sidecars, returning the files that existed."""
    targets = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    removed = [target for target in targets if target.exists()]
    for target in targets:
        target.unlink(missing_ok=True)
    return removed


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


def default_port() -> int:
    """``$PORT`` when it is a usable port number, else 8010 (the Mac's port).

    A malformed PORT falls back rather than raising: the backend starting on
    the rehearsed port beats a container stuck in a restart loop over a typo.
    """
    raw = os.environ.get("PORT", "").strip()
    try:
        port = int(raw)
    except ValueError:
        return 8010
    return port if 0 < port < 65536 else 8010


def build_parser() -> argparse.ArgumentParser:
    # Defaults come from Settings, i.e. the environment (SOURCE, REASONER, VLM,
    # PORT, DB_PATH, ...), so a container needs no flags (docs/DEPLOY.md).
    defaults = Settings()
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("--source", choices=["sim", "glasses", "webcam", "replay"],
                        default=defaults.source)
    parser.add_argument("--dir", help="frame corpus directory for replay")
    parser.add_argument("--loop", action="store_true", help="loop a replay corpus")
    parser.add_argument("--camera", type=int, default=0, help="webcam device index")
    parser.add_argument("--vlm", choices=["gemini", "fake", "off"], default=defaults.vlm,
                        help="T0 tagger; fake/off need no Gemini key")
    parser.add_argument("--flow", choices=["numpy", "opencv", "off"], default=None)
    parser.add_argument("--speed", type=float, default=1.0)
    # The capture cadence, not the playback rate: `--speed` compresses wall
    # time, this changes how many ticks a scenario second produces.
    parser.add_argument("--tick-interval", dest="tick_interval_s", type=float,
                        default=defaults.tick_interval_s,
                        help="seconds between ticks (glasses emit every 1.5 s)")
    parser.add_argument("--reasoner", choices=["openai", "fake"], default=defaults.reasoner)
    parser.add_argument("--db", default=str(defaults.db_path))
    parser.add_argument("--fresh", action="store_true", help="delete the DB and WAL files before startup")
    # PORT from the environment, as container platforms set it; the flag wins.
    parser.add_argument("--port", type=int, default=default_port())
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
    # After validation on purpose: a start that is about to fail must not have
    # already destroyed the previous demo's database.
    if args.fresh:
        fresh_database(settings.db_path)
        logging.getLogger(__name__).info("fresh database: %s", settings.db_path)
    # One line that says whether this backend is open or locked, never the
    # token itself: container logs get pasted into chats.
    logging.getLogger(__name__).info(
        "access token: %s%s", "required" if settings.access_token.strip() else "off (open)",
        f"; public prefix {settings.root_path}" if settings.root_path else "")
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
    # root_path: the public prefix the proxy strips (/t/NAME). uvicorn puts it
    # back into each request's path so a redirect the app builds leads back
    # through the proxy; Starlette strips it again before routing.
    config = uvicorn.Config(app, host="0.0.0.0", port=args.port, log_level="info",
                            root_path=settings.root_path.rstrip("/"))
    # After Config, which (re)configures uvicorn's loggers: the phone's socket
    # URL carries ?token=, and uvicorn logs every path it serves.
    install_log_redaction()
    uvicorn.Server(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
