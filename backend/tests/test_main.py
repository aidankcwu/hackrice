from __future__ import annotations

import asyncio

from conftest import make_tick

from pipeline.bus import TickBus
from pipeline.db import Database
from pipeline.main import build_parser, format_tick, tick_store_consumer


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.source == "sim"
    assert args.speed == 1.0
    assert args.demo_mode is True


def test_parser_flags() -> None:
    args = build_parser().parse_args(
        ["--source", "sim", "--speed", "20", "--no-demo-mode", "--db", "/tmp/y.db"]
    )
    assert args.speed == 20.0
    assert args.demo_mode is False
    assert args.db == "/tmp/y.db"


def test_format_tick_with_and_without_ai() -> None:
    with_ai = format_tick(make_tick(1))
    assert "office/seated" in with_ai
    assert "t_00000001" in with_ai

    without = format_tick(make_tick(2, with_ai=False))
    assert "ai:-" in without


def test_format_tick_lists_flags() -> None:
    tick = make_tick(3)
    assert tick.ai is not None
    tick.ai.food_present = True
    tick.ai.screen_present = True
    line = format_tick(tick)
    assert "food" in line and "screen" in line


async def test_tick_store_consumer_persists(tmp_path) -> None:
    db = Database(tmp_path / "m.db").connect().init_schema()
    bus = TickBus()
    sub = bus.subscribe("tick_store")
    task = asyncio.create_task(tick_store_consumer(sub, db))

    for i in range(3):
        bus.publish(make_tick(i))
        await asyncio.sleep(0.01)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert db.stats()["tick_count"] == 3
    db.close()
