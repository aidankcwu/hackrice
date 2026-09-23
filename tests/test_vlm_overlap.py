"""The T0 tagger overlaps calls and uses late results while they are fresh.

Why these tests exist: the old tagger ran one call at a time and cancelled it at the
tick budget. On the 13 Sep run that threw away 38% of calls, and the printed p50 only
counted the survivors. Each test pins one piece of the replacement: overlap, late
landing, newest-frame-wins, the freshness window, the ceiling, honest stats, and the
opt-in publish-on-landing path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from longevity import vlm
from longevity.loop import T0Loop


class GatedClient:
    """A client whose calls finish only when the test says so, in any order."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Future[dict[str, Any]]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.cancelled = 0
        self.jpegs: list[bytes] = []

    async def tag(self, jpeg: bytes) -> dict[str, Any]:
        self.jpegs.append(jpeg)
        gate: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.gates.append(gate)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            return await gate
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1

    def finish(self, i: int, **fields: Any) -> None:
        self.gates[i].set_result({"scene": "office", "conf": 0.9, **fields})

    async def aclose(self) -> None:
        return None


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def test_two_calls_overlap_and_a_third_frame_waits_newest_wins() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0)
    await tagger.start()
    tagger.offer(1.0, b"a")
    await settle()
    tagger.offer(2.0, b"b")
    await settle()
    # Both slots busy: these two frames overwrite each other in the mailbox.
    tagger.offer(3.0, b"c")
    tagger.offer(4.0, b"d")
    await settle()
    assert len(client.gates) == 2 and client.max_in_flight == 2

    client.finish(0)
    await settle()
    # The freed slot went to the newest frame, not the one it displaced.
    assert client.jpegs == [b"a", b"b", b"d"]
    assert tagger._slot.dropped == 1
    await tagger.aclose()


async def test_a_call_past_the_budget_is_not_cancelled_and_its_result_is_used() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, max_age_s=3.75, log_every=0)
    await tagger.start()
    tagger.offer(100.0, b"a")
    await settle()
    await asyncio.sleep(0.03)  # well past the budget
    assert tagger.take(now=101.5) is None  # tick N+1: not landed yet
    client.finish(0, scene="cafe")
    await settle()

    claimed = tagger.take(now=103.0)  # tick N+2, frame is 3.0 s old: still fresh
    assert claimed is not None and claimed[0]["scene"] == "cafe" and claimed[1] == 100.0
    assert client.cancelled == 0
    assert (tagger.overruns, tagger.late, tagger.returned) == (1, 1, 1)
    # Consume-once: the next tick does not see it again.
    assert tagger.take(now=104.5) is None
    await tagger.aclose()


async def test_a_late_result_older_than_the_freshness_window_is_dropped() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, max_age_s=3.75, log_every=0)
    await tagger.start()
    tagger.offer(100.0, b"a")
    await settle()
    client.finish(0)
    await settle()
    assert tagger.take(now=104.5) is None
    assert tagger.discarded == 1 and tagger.ticks_served == 0
    await tagger.aclose()


async def test_an_older_frame_landing_after_a_newer_one_is_discarded() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0)
    await tagger.start()
    tagger.offer(1.0, b"old")
    await settle()
    tagger.offer(2.0, b"new")
    await settle()
    client.finish(1, scene="park")
    await settle()
    client.finish(0, scene="home")  # the slow, older call lands last
    await settle()

    claimed = tagger.take(now=2.5)
    assert claimed is not None and claimed[0]["scene"] == "park"
    assert tagger.discarded == 1
    await tagger.aclose()


async def test_two_results_in_one_tick_newest_frame_wins() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0)
    await tagger.start()
    tagger.offer(1.0, b"a")
    await settle()
    tagger.offer(2.0, b"b")
    await settle()
    client.finish(0, scene="home")
    client.finish(1, scene="park")
    await settle()
    claimed = tagger.take(now=2.5)
    assert claimed is not None and claimed[1] == 2.0
    assert tagger.discarded == 1
    await tagger.aclose()


async def test_the_ceiling_still_cancels_and_is_counted_in_the_latency_stats() -> None:
    client = vlm.FakeClient(latency=5.0)
    tagger = vlm.T0Tagger(client, budget_s=0.01, ceiling_s=0.05, log_every=0)
    await tagger.start()
    tagger.offer(1.0, b"a")
    await asyncio.sleep(0.15)
    assert client.cancelled == 1
    assert (tagger.timeouts, tagger.overruns, tagger.returned) == (1, 1, 0)
    # The timeout is in the distribution at the ceiling, not missing from it.
    assert tagger.stats()["latency_p50_ms"] == pytest.approx(50.0)
    await tagger.aclose()


async def test_latency_percentiles_include_overruns_at_their_real_latency() -> None:
    """Survivor-only p50 read ~1.2 s while the real median sat on the 1.4 s budget."""
    latencies = iter([0.01, 0.01, 0.08, 0.08, 0.08])
    client = vlm.FakeClient(latency=lambda: next(latencies))
    tagger = vlm.T0Tagger(client, budget_s=0.04, max_in_flight=1, log_every=0)
    await tagger.start()
    for i in range(5):
        tagger.offer(float(i), b"x")
        await asyncio.sleep(0.12)
    stats = tagger.stats()
    assert stats["returned"] == 5 and stats["overruns"] == 3 and stats["late"] == 3
    assert stats["over_budget_rate"] == pytest.approx(0.6)
    # 3 of 5 calls took ~80 ms: the median must be one of them, not a survivor's 10 ms.
    assert stats["latency_p50_ms"] >= 70
    assert stats["latency_p90_ms"] >= 70
    assert "p90=" in tagger.stats_line() and "late=3" in tagger.stats_line()
    await tagger.aclose()


async def test_ceiling_never_undercuts_the_budget() -> None:
    tagger = vlm.T0Tagger(vlm.FakeClient(), budget_s=2.0, ceiling_s=1.0, log_every=0)
    assert tagger.stats()["ceiling_s"] == 2.0
    assert vlm.T0Tagger(vlm.FakeClient(), budget_s=1.5).stats()["ceiling_s"] == 3.0


async def test_tick_cadence_never_waits_on_a_slow_api() -> None:
    """offer/take stay synchronous and instant with two stuck calls in flight."""
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0)
    await tagger.start()
    loop = asyncio.get_running_loop()
    for i in range(20):
        start = loop.time()
        tagger.offer(float(i), b"x")
        assert tagger.take(now=float(i)) is None
        assert loop.time() - start < 0.005
        await settle()
    assert client.max_in_flight == 2
    await tagger.aclose()


# -- publish on landing (T0Loop) ------------------------------------------


class _Frame:
    def __init__(self, t: float) -> None:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (32, 24), (40, 80, 120)).save(buf, "JPEG")
        self.t = t
        self.jpeg = buf.getvalue()
        self.device = None


class _Bus:
    def __init__(self) -> None:
        self.ticks: list[dict[str, Any]] = []

    def publish(self, tick: dict[str, Any]) -> None:
        self.ticks.append(tick)


def _loop(tagger: vlm.T0Tagger, updates: list[dict[str, Any]] | None):
    from longevity.ring import FrameRing

    bus = _Bus()
    loop = T0Loop(
        source=None, ring=FrameRing(ttl_s=90), tagger=tagger, bus=bus,  # type: ignore[arg-type]
        log_every=0, on_ai_update=None if updates is None else updates.append,
    )
    return loop, bus


async def test_a_landed_result_is_published_on_the_newest_tick_without_waiting() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0)
    updates: list[dict[str, Any]] = []
    loop, bus = _loop(tagger, updates)
    await tagger.start()

    loop._on_frame(_Frame(100.0))
    await settle()
    client.finish(0, scene="cafe")
    await settle()

    # Re-sent immediately: same tick, now with the ai block, age measured to it.
    assert len(updates) == 1
    (update,) = updates
    assert update["tick_id"] == bus.ticks[0]["tick_id"] and update["t"] == 100.0
    assert update["ai"]["scene"] == "cafe" and update["ai"]["age_ms"] == 0
    assert "ai" not in bus.ticks[0]  # the original emission is untouched

    # Consume-once: the next tick does not carry it a second time.
    loop._on_frame(_Frame(101.5))
    assert "ai" not in bus.ticks[1]
    assert tagger.ticks_served == 1 and loop.stats.with_ai == 1
    await tagger.aclose()


async def test_without_the_hook_a_landed_result_rides_the_next_tick() -> None:
    client = GatedClient()
    tagger = vlm.T0Tagger(client, budget_s=0.01, log_every=0)
    loop, bus = _loop(tagger, None)
    await tagger.start()
    loop._on_frame(_Frame(100.0))
    await settle()
    client.finish(0, scene="cafe")
    await settle()
    loop._on_frame(_Frame(101.5))
    assert bus.ticks[1]["ai"]["scene"] == "cafe"
    assert bus.ticks[1]["ai"]["age_ms"] == 1500
    await tagger.aclose()


# -- the bridge's budget ----------------------------------------------------


def test_bridge_budget_is_the_full_tick_interval_and_freshness_is_bs_window(tmp_path) -> None:
    """interval - 0.1 existed to stay clear of the cancel cliff; overlap removed it."""
    bridge = pytest.importorskip("pipeline.capture.bridge")
    from pipeline.bus import TickBus
    from pipeline.config import Settings

    settings = Settings(db_path=tmp_path / "unused.db", tick_interval_s=1.5)
    capture = bridge.LongevityCapture(
        settings, source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=1,
        loop=False, camera=0, vlm="fake", flow=None,
    )
    assert capture.vlm_budget_s == 1.5
    stats = capture.tagger.stats()
    assert stats["budget_s"] == 1.5 and stats["ceiling_s"] == 3.0
    assert capture.tagger._max_age == settings.timings.ai_max_age_ms / 1000 == 3.75
