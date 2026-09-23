"""US-W06: every packet reaches the watcher; ticks keep their cadence.
US-W08: the loop drives the labeler from the watcher.

A real `Watcher(run_inline=True)` over `FakeWatcherModel` so the counts are exact, a
fake tagger so no network is touched (or a real `T0Tagger` over `FakeClient` where a
result has to land), and either a real `GlassesLink`/`GlassesSource` pair or a finite
fake source standing in for replay/webcam.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import statistics
import threading
import time

import numpy as np
from PIL import Image

from longevity import ai_fields, vlm, wire
from longevity.emit import TickBus
from longevity.loop import T0Loop
from longevity.ring import FrameRing
from longevity.sensors import quick_quality
from longevity.server import ingest
from longevity.sources.base import CaptureSource, Frame
from longevity.sources.glasses import GlassesSource
from longevity.tick import build_tick
from longevity.vlm import LabelerScheduler
from longevity.watcher import Wakeup, Watcher, WatcherConfig
from longevity.watcher_model import FakeWatcherModel

TICK = 1.5


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
    """The committed `T0Tagger` surface the loop uses, with no network.

    `offers` counts the no-watcher path; `requests` records the watcher path as
    ``(kind, frame_t, jpeg)``. A `result` set by the test is claimed once, at the
    next tick or through `on_result`.
    """

    def __init__(self, **sched) -> None:
        self.on_result = None
        self.result: tuple[dict, float] | None = None
        self.offers = 0
        self.requests: list[tuple[str, float, bytes]] = []
        self.scheduler = LabelerScheduler(tick_interval_s=TICK, **sched)

    async def start(self) -> None: ...
    async def aclose(self) -> None: ...

    def offer(self, frame_t: float, jpeg: bytes) -> None:
        self.offers += 1

    def request(self, kind: str, frame_t: float, jpeg: bytes, *, question=None) -> None:
        self.requests.append((kind, frame_t, jpeg))

    def kinds(self) -> list[str]:
        return [k for k, _, _ in self.requests]

    def take(self, now: float | None = None):
        return self.claim(now)

    def claim(self, now: float | None = None):
        result, self.result = self.result, None
        return result

    def stats_line(self) -> str:
        return "tagger fake"


class ListSource(CaptureSource):
    """Replay-shaped: yields the given frames, then ends.

    Like every real source it gives the event loop a turn between frames, which is
    where a wake-up handed over with `call_soon_threadsafe` runs.
    """

    name = "replay"

    def __init__(self, frames: list[Frame]) -> None:
        self._frames = frames

    async def frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)


class QueueSource(CaptureSource):
    """Replay-shaped, but the test pushes frames while the loop runs; `close()` ends it.

    Every `get` yields to the asyncio loop, which is where a wake-up scheduled with
    `call_soon_threadsafe` runs -- exactly as it would between two real ticks.
    """

    name = "replay"

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()

    async def frames(self):
        while (f := await self._q.get()) is not None:
            yield f

    def put(self, frame: Frame) -> None:
        self._q.put_nowait(frame)

    def close(self) -> None:
        self._q.put_nowait(None)


class Bus(TickBus):
    def __init__(self) -> None:
        super().__init__()
        self.ticks: list[dict] = []

    def publish(self, tick) -> None:
        self.ticks.append(tick)
        super().publish(tick)


def make_watcher() -> Watcher:
    return Watcher(FakeWatcherModel(), WatcherConfig(), {}, frozenset(), run_inline=True)


def make_loop(source, watcher=None, tagger=None, **kw) -> tuple[T0Loop, Bus, FakeTagger]:
    bus, tagger = Bus(), tagger if tagger is not None else FakeTagger()
    loop = T0Loop(source, ring=FrameRing(), tagger=tagger, bus=bus, flow="off", log_every=0,
                  watcher=watcher, **kw)
    return loop, bus, tagger


def replay_frames(n: int) -> list[Frame]:
    t0 = time.time()
    return [Frame(t=t0 + i * TICK, jpeg=_jpeg(i)) for i in range(n)]


# --- a watcher with concepts the test can switch on and off --------------------------

VEG = "vegetation_visible"
PEOPLE = "people_present"
ENTER, EXIT = 0.6, 0.4

#: Real JPEGs (the sensor block and the quality gate need to decode them) whose fake
#: embedding is forced onto a prompt vector: "veg" scores vegetation_visible > 0.9 and
#: nothing else above 0.2, "null" scores everything under 0.1 (see test_watcher.py).
VEG_JPEG = _jpeg(1001)
NULL_JPEG = _jpeg(1002)
SCRIPTED = {
    VEG_JPEG: FakeWatcherModel().text_bank([ai_fields.WATCH_PROMPTS[VEG][1]])[0],
    NULL_JPEG: FakeWatcherModel().text_bank([ai_fields.WATCH_NULL_PROMPTS[1]])[0],
}


def concept_watcher(*, run_inline: bool = True, clock=time.monotonic, cooldown_s: float = 30.0) -> Watcher:
    """1-of-1 hysteresis on VEG, novelty off (the fake's hash vectors make every
    frame change "novel"), so a single VEG frame wakes and a single null one cools."""
    config = WatcherConfig(k=1, n=1, cooldown_s=cooldown_s, novelty_enter=9.0)
    return Watcher(FakeWatcherModel(scripted=SCRIPTED), config, {VEG: (ENTER, EXIT)},
                   frozenset(), clock=clock, run_inline=run_inline)


class Frames:
    """Frames `TICK` apart in simulated time, from a fixed epoch."""

    def __init__(self) -> None:
        self.t0 = time.time()
        self.i = 0

    def __call__(self, jpeg: bytes = NULL_JPEG) -> Frame:
        f = Frame(t=self.t0 + self.i * TICK, jpeg=jpeg)
        self.i += 1
        return f


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


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
    loop, bus, tagger = make_loop(ListSource(frames))
    await loop.run()

    assert tagger.offers == 3 and tagger.requests == []  # `offer` per tick, as before
    assert loop.stats.wakeups_forwarded == 0 and loop.stats.labeler_requests_by_kind == {}
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

        def hot_concepts(self):
            return frozenset()

        def cooling_concepts(self):
            return frozenset()

        def take_wakeup(self):
            return None

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
    assert w.on_wakeup == loop._on_wakeup  # US-W08: the loop is the wake-up listener
    assert "watcher tiny frames=2" in caplog.text
    assert "labeler mode=cold" in caplog.text
    assert tagger.kinds() == ["heartbeat"] and tagger.offers == 0


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


# --- US-W08: the loop drives the labeler from the watcher ----------------------------


async def test_a_wakeup_becomes_a_wake_request_on_the_newest_frame_before_the_next_tick() -> None:
    watcher = concept_watcher()
    source = QueueSource()
    loop, bus, tagger = make_loop(source, watcher)
    frames = Frames()
    task = asyncio.create_task(loop.run())

    source.put(frames(NULL_JPEG))
    await settle()
    assert tagger.kinds() == ["heartbeat"]  # cold: the first tick is a heartbeat

    veg = frames(VEG_JPEG)
    source.put(veg)
    await settle()
    # This tick's watch block already shows the wake, and the scheduler saw the
    # concept hot (transition -> "hot"); the wake request followed at once.
    assert bus.ticks[-1]["watch"]["woke"] == VEG and bus.ticks[-1]["watch"]["hot"] == [VEG]
    assert tagger.kinds() == ["heartbeat", "hot", "wake"]
    kind, frame_t, jpeg = tagger.requests[-1]
    assert (frame_t, jpeg) == (veg.t, VEG_JPEG)  # the newest frame in the ring
    assert watcher.stats()["pending_wakeup"] is False  # the mailbox was drained
    assert loop.stats.wakeups_forwarded == 1
    assert loop.stats.labeler_requests_by_kind == {"heartbeat": 1, "hot": 1, "wake": 1}

    source.put(frames(VEG_JPEG))
    await settle()
    assert tagger.kinds() == ["heartbeat", "hot", "wake", "hot"]  # still hot: no second wake
    source.close()
    await asyncio.wait_for(task, 2.0)
    assert watcher.on_wakeup == loop._on_wakeup


async def test_a_wakeup_from_the_watcher_thread_reaches_the_labeler() -> None:
    """The real worker thread, not `run_inline`: `on_wakeup` fires off the asyncio
    thread and the request is still issued on it."""
    watcher = concept_watcher(run_inline=False)
    source = QueueSource()
    loop, bus, tagger = make_loop(source, watcher)
    frames = Frames()
    task = asyncio.create_task(loop.run())
    threads: set[int] = set()
    forward = loop._forward_wakeup

    def on_loop_thread(wake: Wakeup) -> None:
        threads.add(threading.get_ident())
        forward(wake)

    loop._forward_wakeup = on_loop_thread  # type: ignore[method-assign]

    source.put(frames(NULL_JPEG))
    await settle()
    source.put(frames(VEG_JPEG))
    deadline = time.monotonic() + 2.0
    while "wake" not in tagger.kinds() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    source.close()
    await asyncio.wait_for(task, 2.0)

    assert "wake" in tagger.kinds()
    assert threads == {threading.get_ident()}  # forwarded on the asyncio thread
    assert loop.stats.wakeups_forwarded == 1


async def test_a_wakeup_without_a_running_loop_is_dropped_not_raised() -> None:
    watcher = concept_watcher()
    loop, _, tagger = make_loop(ListSource([]), watcher)
    loop._on_wakeup(Wakeup(1.0, (VEG,), 0.0, "concept"))  # before run(): nowhere to go
    assert tagger.requests == [] and loop.stats.wakeups_forwarded == 0


async def test_steady_hot_issues_about_one_hot_request_per_steady_s() -> None:
    watcher = concept_watcher()
    tagger = FakeTagger(steady_s=10.0, transition_s=60.0)
    frames = Frames()
    loop, bus, _ = make_loop(ListSource([frames(VEG_JPEG) for _ in range(80)]), watcher, tagger)
    await loop.run()  # 120 s simulated: 60 s transition, then 60 s steady hot

    hot = [t for k, t, _ in tagger.requests if k == "hot"]
    transition = [t for t in hot if t < frames.t0 + 60]
    steady = [t for t in hot if t >= frames.t0 + 60]
    assert len(transition) == 40  # every tick for the first 60 s
    assert abs(len(steady) - 6) <= 1  # one per ~10 s over the steady 60 s
    gaps = [b - a for a, b in zip(steady, steady[1:])]
    assert all(g >= 10.0 for g in gaps)
    assert tagger.scheduler.mode == "steady"
    assert tagger.kinds().count("wake") == 1 and "heartbeat" not in tagger.kinds()
    assert loop.stats.labeler_requests_by_kind["hot"] == len(hot)


async def test_cold_issues_heartbeats_at_heartbeat_s() -> None:
    watcher = concept_watcher()
    tagger = FakeTagger(heartbeat_s=60.0)
    frames = Frames()
    loop, bus, _ = make_loop(ListSource([frames(NULL_JPEG) for _ in range(200)]), watcher, tagger)
    await loop.run()  # 300 s simulated, nothing ever hot

    assert set(tagger.kinds()) == {"heartbeat"}
    beats = [t - frames.t0 for _, t, _ in tagger.requests]
    assert beats == [0.0, 60.0, 120.0, 180.0, 240.0]
    assert all("hot" in t["watch"] and t["watch"]["hot"] == [] for t in bus.ticks)
    assert loop.stats.wakeups_forwarded == 0


async def test_cooling_runs_every_tick_and_a_cold_wake_forwards_again() -> None:
    clock = FakeClock()
    watcher = concept_watcher(clock=clock, cooldown_s=30.0)
    tagger = FakeTagger(cooling_s=30.0)
    source = QueueSource()
    loop, bus, _ = make_loop(source, watcher, tagger)
    frames = Frames()
    task = asyncio.create_task(loop.run())

    source.put(frames(VEG_JPEG))  # hot + wake
    await settle()
    source.put(frames(NULL_JPEG))  # hot -> cooling on the exit rule
    await settle()
    assert bus.ticks[-1]["watch"]["hot"] == [VEG]
    assert tagger.kinds() == ["hot", "wake", "hot"]
    assert tagger.scheduler.mode == "cooling"

    clock.advance(31.0)  # cooling -> cold on the watcher's clock
    source.put(frames(NULL_JPEG))
    await settle()
    assert bus.ticks[-1]["watch"]["hot"] == []
    source.put(frames(VEG_JPEG))  # a fresh cold -> hot edge: a second wake
    await settle()
    assert tagger.kinds().count("wake") == 2 and loop.stats.wakeups_forwarded == 2
    source.close()
    await asyncio.wait_for(task, 2.0)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def real_tagger(**fields) -> vlm.T0Tagger:
    """A real tagger over a zero-latency fake client whose every answer is `fields`."""
    client = vlm.FakeClient(latency=0.0, fields={"scene": "office", **fields})
    return vlm.T0Tagger(client, budget_s=0.01, log_every=0, clock=FakeClock())


async def _run_wake_then_land(tagger: vlm.T0Tagger, watcher: Watcher, *, extra_ticks: int = 2, on_ai_update=None) -> T0Loop:
    source = QueueSource()
    loop, bus, _ = make_loop(source, watcher, tagger, on_ai_update=on_ai_update)
    frames = Frames()
    task = asyncio.create_task(loop.run())
    source.put(frames(NULL_JPEG))
    await settle()
    source.put(frames(VEG_JPEG))  # the wake
    await settle()
    for _ in range(extra_ticks):  # results land and are claimed on these ticks
        source.put(frames(VEG_JPEG))
        await settle()
    source.close()
    await asyncio.wait_for(task, 2.0)
    assert loop.stats.wakeups_forwarded == 1
    return loop


async def test_an_unconfirmed_wake_doubles_the_concepts_cooldown() -> None:
    watcher = concept_watcher(cooldown_s=30.0)
    assert watcher.cooldown_of(VEG) == 30.0
    loop = await _run_wake_then_land(real_tagger(vegetation_visible=False), watcher)
    assert loop.stats.with_ai >= 1
    assert watcher.cooldown_of(VEG) == 60.0  # judged once, not once per landed result


async def test_a_confirmed_wake_resets_the_concepts_cooldown() -> None:
    watcher = concept_watcher(cooldown_s=30.0)
    watcher.mark_unconfirmed(VEG)
    assert watcher.cooldown_of(VEG) == 60.0
    loop = await _run_wake_then_land(real_tagger(vegetation_visible=True), watcher)
    assert loop.stats.with_ai >= 1
    assert watcher.cooldown_of(VEG) == 30.0


async def test_a_wake_is_settled_on_landing_too_and_by_the_newest_pending_wake() -> None:
    """Publish-on-landing path: the verdict does not wait for the next tick."""
    watcher = concept_watcher(cooldown_s=30.0)
    updates: list[dict] = []
    loop = await _run_wake_then_land(
        real_tagger(vegetation_visible=False), watcher, extra_ticks=0, on_ai_update=updates.append,
    )
    assert updates and "ai" in updates[-1]
    assert watcher.cooldown_of(VEG) == 60.0


async def test_a_pending_wake_older_than_ten_seconds_is_dropped_without_a_verdict() -> None:
    watcher = concept_watcher(cooldown_s=30.0)
    tagger = FakeTagger()
    source = QueueSource()
    loop, bus, _ = make_loop(source, watcher, tagger)
    frames = Frames()
    task = asyncio.create_task(loop.run())
    source.put(frames(NULL_JPEG))
    await settle()
    veg = frames(VEG_JPEG)
    source.put(veg)
    await settle()
    assert loop._pending_wakes == {veg.t: (VEG,)}

    # A result on an older frame than the wake is no evidence about it.
    tagger.result = ({VEG: False}, veg.t - 1.0)
    source.put(frames(VEG_JPEG))
    await settle()
    assert "ai" in bus.ticks[-1] and watcher.cooldown_of(VEG) == 30.0
    assert loop._pending_wakes == {veg.t: (VEG,)}

    # Eleven seconds on with no verdict: forgotten, cooldown untouched.
    frames.i += 8  # jump the clock past the window
    late = frames(VEG_JPEG)
    assert late.t - veg.t > 10.0
    tagger.result = ({VEG: False}, late.t)
    source.put(late)
    await settle()
    assert "ai" in bus.ticks[-1] and watcher.cooldown_of(VEG) == 30.0
    assert loop._pending_wakes == {}
    source.close()
    await asyncio.wait_for(task, 2.0)


async def test_a_look_answer_is_stripped_from_the_ai_block_and_taken_once() -> None:
    # No watcher needed: the look key must never reach a tick on either path.
    client = vlm.FakeClient(latency=0.0, answer="the cup is empty")
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0, clock=FakeClock())
    updates: list[dict] = []
    frames = Frames()
    loop, bus, _ = make_loop(ListSource([]), tagger=tagger, on_ai_update=updates.append)
    await tagger.start()
    assert loop.take_look_answer() is None

    # Take-at-tick path: the answer lands, then rides the next tick's `take`.
    tagger.on_result = None
    first = frames()
    loop._on_frame(first)
    tagger.request("look", first.t, first.jpeg, question="Is the cup empty?")
    await settle()
    second = frames()
    loop._on_frame(second)
    ai = bus.ticks[-1]["ai"]
    assert vlm.LOOK_ANSWER_KEY not in ai and "answer" not in ai
    assert list(ai) == ["as_of", "age_ms", *ai_fields.FIELD_ORDER]
    assert loop.last_look_answer == (first.t, "the cup is empty")
    assert loop.take_look_answer() == (first.t, "the cup is empty")
    assert loop.take_look_answer() is None  # consume-once

    # Publish-on-landing path: a tick without `ai` yet is re-sent, just as clean.
    tagger.on_result = loop._attach_landed
    third = frames()
    loop._on_frame(third)
    assert "ai" not in bus.ticks[-1]
    tagger.request("look", third.t, third.jpeg, question="Still empty?")
    await settle()
    assert len(updates) == 1 and updates[0]["tick_id"] == bus.ticks[-1]["tick_id"]
    assert vlm.LOOK_ANSWER_KEY not in updates[0]["ai"] and "answer" not in updates[0]["ai"]
    assert updates[0]["ai"]["scene"] == "office"
    assert loop.take_look_answer() == (third.t, "the cup is empty")
    assert loop.stats.look_answers == 2
    assert all(vlm.LOOK_ANSWER_KEY not in t.get("ai", {}) for t in bus.ticks + updates)
    await tagger.aclose()


async def test_without_a_watcher_no_scheduler_is_consulted() -> None:
    tagger = FakeTagger()
    loop, bus, _ = make_loop(ListSource(replay_frames(5)), tagger=tagger)
    await loop.run()
    assert tagger.offers == 5 and tagger.requests == []
    assert tagger.scheduler.mode == "cold" and tagger.scheduler._last_request is None


# --- US-M05: the loop tells a listener about each forwarded wake-up ------------------


async def test_a_forwarded_wakeup_reaches_on_wakeup_forwarded_with_its_armed_reason() -> None:
    """`on_wakeup_forwarded` is called on the asyncio loop after the wake request
    went out, with the same `Wakeup` -- an armed one carries `watch_armed:<id>`.
    A listener that raises does not stop the loop."""
    watcher = concept_watcher()
    source = QueueSource()
    loop, bus, tagger = make_loop(source, watcher)
    frames = Frames()
    heard: list[Wakeup] = []

    def listener(wake: Wakeup) -> None:
        heard.append(wake)
        raise RuntimeError("listener bug")

    loop.on_wakeup_forwarded = listener
    watcher.arm(VEG, 60.0, "d_1/vegetation_visible")
    task = asyncio.create_task(loop.run())

    source.put(frames(NULL_JPEG))
    await settle()
    assert heard == []
    source.put(frames(VEG_JPEG))
    await settle()
    source.put(frames(NULL_JPEG))  # the loop survived the raising listener
    await settle()
    source.close()
    await asyncio.wait_for(task, 2.0)

    assert [w.reason for w in heard] == ["watch_armed:d_1/vegetation_visible"]
    assert heard[0].concepts == (VEG,)
    assert "wake" in tagger.kinds() and loop.stats.wakeups_forwarded == 1
    assert len(bus.ticks) == 3
