"""US-W06: every packet reaches the watcher; ticks keep their cadence.

A real `Watcher(run_inline=True)` over `FakeWatcherModel` so the counts are exact, a
fake tagger so no network is touched, and either a real `GlassesLink`/`GlassesSource`
pair or a finite fake source standing in for replay/webcam.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import statistics
import time

import numpy as np
from PIL import Image

from longevity import wire
from longevity.emit import TickBus
from longevity.loop import T0Loop
from longevity.ring import FrameRing
from longevity.sensors import quick_quality
from longevity.server import ingest
from longevity.sources.base import CaptureSource, Frame
from longevity.sources.glasses import GlassesSource
from longevity.tick import build_tick
from longevity.watcher import Watcher, WatcherConfig
from longevity.watcher_model import FakeWatcherModel


def _jpeg(seed: int, size: tuple[int, int] = (512, 288), level: int | None = None) -> bytes:
    """Noisy (so sharp) mid-grey frame; `level` makes it flat at that grey instead."""
    w, h = size
    if level is None:
        arr = np.random.default_rng(seed).integers(40, 220, (h, w, 3)).astype(np.uint8)
    else:
        arr = np.full((h, w, 3), level, np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


class FakeTagger:
    """The committed `T0Tagger` surface the loop uses, with no network."""

    def __init__(self) -> None:
        self.on_result = None
        self.result: tuple[dict, float] | None = None

    async def start(self) -> None: ...
    async def aclose(self) -> None: ...

    def offer(self, frame_t: float, jpeg: bytes) -> None: ...

    def take(self, now: float | None = None):
        return None

    def claim(self, now: float | None = None):
        result, self.result = self.result, None
        return result

    def stats_line(self) -> str:
        return "tagger fake"


class ListSource(CaptureSource):
    """Replay-shaped: yields the given frames, then ends."""

    name = "replay"

    def __init__(self, frames: list[Frame]) -> None:
        self._frames = frames

    async def frames(self):
        for f in self._frames:
            yield f


class Bus(TickBus):
    def __init__(self) -> None:
        super().__init__()
        self.ticks: list[dict] = []

    def publish(self, tick) -> None:
        self.ticks.append(tick)
        super().publish(tick)


def make_watcher() -> Watcher:
    return Watcher(FakeWatcherModel(), WatcherConfig(), {}, frozenset(), run_inline=True)


def make_loop(source, watcher=None, **kw) -> tuple[T0Loop, Bus, FakeTagger]:
    bus, tagger = Bus(), FakeTagger()
    loop = T0Loop(source, ring=FrameRing(), tagger=tagger, bus=bus, flow="off", log_every=0,
                  watcher=watcher, **kw)
    return loop, bus, tagger


def replay_frames(n: int) -> list[Frame]:
    t0 = time.time()
    return [Frame(t=t0 + i * 1.5, jpeg=_jpeg(i)) for i in range(n)]


def put_capture(link: ingest.GlassesLink, seed: int) -> None:
    ingest._handle(link, wire.capture_packet(t=time.time(), jpeg=_jpeg(seed), gps_speed=None, accel=None))


# --- glasses: the packet hook ---------------------------------------------------


async def test_three_packets_in_one_interval_make_one_tick_with_three_frames() -> None:
    link = ingest.GlassesLink()
    source = GlassesSource(link, interval=0.05)
    watcher = make_watcher()
    loop, bus, _ = make_loop(source, watcher)
    assert link.on_packet is not None

    task = asyncio.create_task(loop.run())
    for seed in range(3):  # no await between them: all inside one interval
        put_capture(link, seed)
    deadline = time.monotonic() + 2.0
    while not bus.ticks and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.15)  # a few empty intervals: no extra tick, no re-emit
    await source.aclose()
    await asyncio.wait_for(task, 2.0)

    assert len(bus.ticks) == 1
    tick = bus.ticks[0]
    assert tick["watch"]["frames"] == 3
    assert tick["watch"]["usable"] == 3
    assert "device" in tick
    assert watcher.frames == 3  # the tick's own frame was not offered a second time
    assert loop.stats.watch_ticks == 1
    assert link.on_packet is None  # unhooked when the loop ends


async def test_no_watcher_registers_no_hook_and_no_watch_key() -> None:
    link = ingest.GlassesLink()
    source = GlassesSource(link, interval=0.05)
    loop, bus, _ = make_loop(source)
    assert link.on_packet is None

    task = asyncio.create_task(loop.run())
    put_capture(link, 0)
    deadline = time.monotonic() + 2.0
    while not bus.ticks and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await source.aclose()
    await asyncio.wait_for(task, 2.0)

    assert len(bus.ticks) == 1
    assert "watch" not in bus.ticks[0]
    assert loop.stats.watch_ticks == 0


def test_hook_that_raises_never_breaks_put(caplog) -> None:
    link = ingest.GlassesLink()
    calls = []

    def boom(packet: ingest.Packet) -> None:
        calls.append(packet.seq)
        raise RuntimeError("listener bug")

    link.on_packet = boom
    with caplog.at_level(logging.ERROR, logger="longevity.server.ingest"):
        put_capture(link, 0)
        put_capture(link, 1)

    assert calls == [1, 2]
    assert link.n_received == 2
    assert link.latest is not None and link.latest.seq == 2
    assert link.n_hook_errors == 2
    assert link.stats()["hook_errors"] == 2
    assert caplog.text.count("on_packet hook raised") == 1  # rate-limited


def test_malformed_packet_never_reaches_the_hook() -> None:
    link = ingest.GlassesLink()
    seen = []
    link.on_packet = seen.append
    ingest._handle(link, json.dumps({"v": 1, "type": "capture", "t": time.time()}))
    ingest._handle(link, b"\x00not json")
    assert seen == []
    assert link.n_malformed == 2
    put_capture(link, 0)
    assert [p.seq for p in seen] == [1]


# --- replay / webcam: once per frame -------------------------------------------


async def test_replay_source_feeds_the_watcher_once_per_frame() -> None:
    watcher = make_watcher()
    loop, bus, _ = make_loop(ListSource(replay_frames(4)), watcher)
    await loop.run()

    assert len(bus.ticks) == 4
    assert [t["watch"]["frames"] for t in bus.ticks] == [1, 1, 1, 1]
    assert watcher.frames == 4
    assert loop.stats.watch_ticks == 4


async def test_no_watcher_ticks_are_identical_to_before() -> None:
    frames = replay_frames(3)
    loop, bus, _ = make_loop(ListSource(frames))
    await loop.run()

    for tick in bus.ticks:
        assert set(tick) == {"v", "tick_id", "t", "seq", "sensor", "frame_ref"}
        rebuilt = build_tick(seq=tick["seq"], t=tick["t"], sensor=tick["sensor"])
        assert json.dumps(tick) == json.dumps(rebuilt)


async def test_resent_tick_on_landing_carries_the_same_watch_block() -> None:
    watcher = make_watcher()
    updates: list[dict] = []
    frames = replay_frames(2)
    loop, bus, tagger = make_loop(ListSource(frames), watcher, on_ai_update=updates.append)
    await loop.run()

    tagger.result = ({"food_present": True}, frames[-1].t - 0.2)
    tagger.on_result()  # the loop's `_attach_landed`

    assert len(updates) == 1
    resent, original = updates[0], bus.ticks[-1]
    assert resent["tick_id"] == original["tick_id"]
    assert resent["watch"] == original["watch"]
    assert resent["ai"]["food_present"] is True


async def test_watcher_lifecycle_and_log_line(caplog) -> None:
    class TinyWatcher:
        def __init__(self) -> None:
            self.started = self.closed = 0
            self.offers = 0
            self.on_wakeup = None

        async def start(self) -> None:
            self.started += 1

        async def aclose(self) -> None:
            self.closed += 1

        def offer(self, frame_t, jpeg, sensor=None) -> None:
            self.offers += 1

        def take_tick(self, now):
            return {"v": 1, "frames": self.offers}

        def stats(self) -> dict:
            return {"model": "tiny", "frames": self.offers, "usable": self.offers, "dropped": 0,
                    "wakeups": 0, "errors": 0, "inference_ms_p50": 0.0,
                    "inference_ms_p99": 0.0, "hot": []}

    w = TinyWatcher()
    bus, tagger = Bus(), FakeTagger()
    loop = T0Loop(ListSource(replay_frames(2)), ring=FrameRing(), tagger=tagger, bus=bus,
                  flow="off", log_every=1, watcher=w)
    with caplog.at_level(logging.INFO, logger="longevity.loop"):
        await loop.run()

    assert (w.started, w.closed, w.offers) == (1, 1, 2)
    assert w.on_wakeup is None  # US-W08 wires it, not this story
    assert "watcher tiny frames=2" in caplog.text


# --- quick_quality ------------------------------------------------------------


def test_quick_quality_values_and_cost() -> None:
    sharp = quick_quality(_jpeg(0))
    assert set(sharp) == {"sharpness", "lux_proxy"}
    assert all(isinstance(v, float) for v in sharp.values())
    assert sharp["sharpness"] > 20.0
    assert 0.0 < sharp["lux_proxy"] <= 1000.0

    flat = quick_quality(_jpeg(0, level=128))
    assert flat["sharpness"] < 5.0
    assert 150.0 < flat["lux_proxy"] < 280.0  # sRGB 128 is ~21.6% linear luminance

    dark = quick_quality(_jpeg(0, level=5))
    assert dark["lux_proxy"] < 10.0

    assert quick_quality(b"not a jpeg") == {"sharpness": 0.0, "lux_proxy": 0.0}

    jpeg = _jpeg(1)
    for _ in range(5):
        quick_quality(jpeg)
    times = []
    for _ in range(30):
        t = time.perf_counter()
        quick_quality(jpeg)
        times.append((time.perf_counter() - t) * 1000)
    # Measured ~0.45 ms on this Mac; the bound is generous so a busy CI box cannot flake.
    assert statistics.median(times) < 20.0
