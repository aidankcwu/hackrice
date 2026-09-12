"""SPEC §5.2: drop, never queue."""

from __future__ import annotations

import asyncio

from conftest import make_tick

from pipeline.bus import TickBus


async def test_single_consumer_sees_every_tick_when_keeping_up() -> None:
    bus = TickBus()
    sub = bus.subscribe("fast")
    seen: list[int] = []

    async def consume() -> None:
        async for tick in sub:
            seen.append(tick.seq)
            if len(seen) == 5:
                return

    task = asyncio.create_task(consume())
    for i in range(5):
        bus.publish(make_tick(i))
        await asyncio.sleep(0)  # let the consumer drain the slot
    await asyncio.wait_for(task, 1.0)

    assert seen == [0, 1, 2, 3, 4]
    assert sub.dropped == 0


async def test_slow_consumer_drops_and_never_backlogs() -> None:
    bus = TickBus()
    sub = bus.subscribe("slow")
    seen: list[int] = []

    async def slow() -> None:
        async for tick in sub:
            seen.append(tick.seq)
            await asyncio.sleep(0.05)

    task = asyncio.create_task(slow())
    await asyncio.sleep(0)

    for i in range(50):
        bus.publish(make_tick(i))
        # The slot never grows past one element, no matter the publish rate.
        assert sub.pending <= 1
        await asyncio.sleep(0.004)  # publisher runs ~12x faster than the consumer

    task.cancel()

    assert sub.dropped > 0, "a slow subscriber must be dropping ticks"
    assert sub.pending <= 1
    assert 2 <= len(seen) < 50
    assert sub.delivered + sub.dropped + sub.pending == 50
    # The consumer always gets the freshest tick, never the oldest queued one.
    assert seen == sorted(seen)
    assert seen[1] - seen[0] > 1, "intermediate ticks were skipped, not queued"


async def test_publish_never_blocks_without_any_consumer_running() -> None:
    bus = TickBus()
    sub = bus.subscribe("idle")
    for i in range(1000):
        bus.publish(make_tick(i))
    assert sub.pending == 1
    assert sub.dropped == 999
    # And the one tick held is the newest.
    assert (await sub.get()).seq == 999


async def test_fanout_to_multiple_subscribers() -> None:
    bus = TickBus()
    a = bus.subscribe("a")
    b = bus.subscribe("b")
    bus.publish(make_tick(7))
    assert (await a.get()).seq == 7
    assert (await b.get()).seq == 7
    assert bus.published == 1


async def test_unsubscribe_on_close() -> None:
    bus = TickBus()
    sub = bus.subscribe("gone")
    assert bus.subscribers == [sub]
    sub.close()
    assert bus.subscribers == []
    bus.publish(make_tick(1))
    assert sub.pending == 0


async def test_closed_subscription_stops_iteration() -> None:
    bus = TickBus()
    sub = bus.subscribe("s")
    bus.publish(make_tick(1))
    sub.close()
    seen = [tick.seq async for tick in sub]
    assert seen == [1]
