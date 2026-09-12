"""Acceptance checks for A6 — tick assembly and emission.

PERSON_A.md's done-when: "ticks arrive at a steady 1 Hz with no seq gaps and no clock
drift over 15 minutes."

Fifteen real minutes is not a useful thing to run repeatedly, so this does both: a
long accelerated run that proves seq integrity, schema shape, and flat memory over
900 ticks, and a short real-time run that measures actual cadence and drift.

Uses its own synthetic CaptureSource so it does not depend on the replay or webcam
adapters — this checks the loop, not the source.

Run:  uv run python tools/check_loop.py
"""

from __future__ import annotations

import asyncio
import io
import random
import sys
import time
import tracemalloc
from collections.abc import AsyncIterator

import numpy as np
from PIL import Image

from longevity.emit import TickBus
from longevity.loop import T0Loop
from longevity.ring import FrameRing
from longevity.sources.base import CaptureSource, Frame
from longevity.vlm import FakeClient, T0Tagger

FAIL = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global FAIL
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAIL += 1


def make_jpeg(seed: int, px: int = 512) -> bytes:
    """A photo-like frame, NOT random noise.

    Pure noise encodes to ~145 KB and costs 2.7 ms to decode-and-measure; a real
    512 px q70 glasses frame is 10-18 KB and costs 1.6 ms. Testing against noise
    manufactures a budget failure that cannot happen in production.
    """
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:px, 0:px].astype(np.float32)
    base = x / px * 90 + y / px * 70 + 60
    img = np.dstack([base, base * 0.95, base * 0.85])
    for _ in range(6):
        cx, cy, r = rng.integers(0, px, 3)
        mask = ((x - cx) ** 2 + (y - cy) ** 2) < (r % 120 + 20) ** 2
        img[mask] = img[mask] * 0.6 + rng.integers(40, 210, 3) * 0.4
    img += rng.normal(0, 3, img.shape)
    buf = io.BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(
        buf, format="JPEG", quality=70
    )
    return buf.getvalue()


class SyntheticSource(CaptureSource):
    """A CaptureSource that emits N frames on a monotonic target at `period`."""

    name = "synthetic"

    def __init__(self, n: int, period: float, device: dict | None = None,
                 sim_period: float | None = None) -> None:
        self._n, self._period, self._device = n, period, device
        # `sim_period` decouples the frame's *timestamp* clock from the wall clock, so
        # 900 ticks can simulate a 15-minute session in 9 seconds and the ring's 90 s
        # TTL is genuinely exercised rather than skipped.
        self._sim = sim_period
        self._pool = [make_jpeg(i) for i in range(12)]

    async def frames(self) -> AsyncIterator[Frame]:
        start = time.monotonic()
        sim_start = time.time()
        for i in range(self._n):
            target = start + i * self._period
            delay = target - time.monotonic()
            # Yield to the event loop even when behind schedule. Without this a source
            # whose period is shorter than the loop's synchronous work never gives the
            # VLM task a chance to run and coverage silently reads 0%. At 1 Hz there is
            # a 300x margin, but `replay --speed 100` would hit it.
            await asyncio.sleep(delay if delay > 0 else 0)
            t = sim_start + i * self._sim if self._sim else time.time()
            yield Frame(t=t, jpeg=self._pool[i % len(self._pool)],
                        device=dict(self._device) if self._device else None)


async def build(n: int, period: float, device=None, latency=0.4, sim_period=None):
    ring = FrameRing()
    bus = TickBus()
    ticks: list[dict] = []
    bus.subscribe(ticks.append)
    tagger = T0Tagger(FakeClient(latency=latency), log_every=0)
    loop = T0Loop(SyntheticSource(n, period, device, sim_period), ring=ring,
                  tagger=tagger, bus=bus, log_every=0)
    await loop.run()
    return loop, ring, ticks


async def check_long_run() -> None:
    n = 900
    print(f"\n1. Accelerated {n}-tick run (a 15-minute session)")
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    rnd = random.Random(3)
    # 10 ms of wall time per tick, but timestamps advance a full second each: 900 ticks
    # = a simulated 15-minute session in ~9 s of real time.
    loop, ring, ticks = await build(
        n, period=0.01, sim_period=1.0, latency=lambda: rnd.uniform(0.002, 0.02)
    )
    peak = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()

    seqs = [t["seq"] for t in ticks]
    report("no seq gaps", seqs == list(range(1, n + 1)),
           f"{len(seqs)} ticks, {seqs[0]}..{seqs[-1]}")
    report("tick_id matches seq", all(t["tick_id"] == f"t_{t['seq']:08d}" for t in ticks))
    report("frame_ref matches seq", all(t["frame_ref"] == f"f_{t['seq']:08d}" for t in ticks))
    report("sensor block always present (§12.1)", all("sensor" in t for t in ticks))
    report("all 7 sensor fields on every tick",
           all(len(t["sensor"]) == 7 for t in ticks))
    report("device absent without a phone (§12.1)", all("device" not in t for t in ticks))

    with_ai = sum("ai" in t for t in ticks)
    report("ai block present on some but not all ticks", 0 < with_ai < n,
           f"{with_ai}/{n} = {with_ai / n:.0%} coverage")
    ages = [t["ai"]["age_ms"] for t in ticks if "ai" in t]
    report("age_ms is never negative", all(a >= 0 for a in ages),
           f"max age {max(ages)}ms" if ages else "")
    as_ofs = [t["ai"]["as_of"] for t in ticks if "ai" in t]
    report("no ai result reused across ticks (invariant 3)",
           len(as_ofs) == len(set(as_ofs)), f"{len(as_ofs)} served, {len(set(as_ofs))} distinct")

    st = ring.stats()
    # Under acceleration the ring's wall-clock TTL cannot fire (900 simulated seconds
    # elapse in 9 real ones), so what is under test here is the safety cap that keeps
    # `replay --speed` from exhausting RAM. The 90 s TTL is checked separately below.
    report("ring stays bounded under accelerated replay", st.count <= 256,
           f"{st.count} entries, {st.bytes / 1e6:.2f} MB (cap, not TTL)")
    grew_mb = (peak - base) / 1e6
    report("memory flat over the run", grew_mb < 25, f"heap grew {grew_mb:.1f} MB")
    print(f"     {loop.stats.line()}")


async def check_ttl_eviction() -> None:
    """The 90 s TTL (§2.5, §12.3), driven by an injected clock."""
    print("\n2. Ring buffer 90 s TTL")
    ring = FrameRing()
    jpeg = make_jpeg(1)
    t0 = 1_800_000_000.0
    for i in range(900):
        ring.put(f"f_{i:08d}", jpeg, t0 + i, now=t0 + i)
    st = ring.stats(now=t0 + 899)
    report("holds ~90 frames after 900 puts at 1 Hz", 88 <= st.count <= 92,
           f"{st.count} entries, {st.bytes / 1e6:.2f} MB")
    report("a fresh ref resolves", ring.get("f_00000899", now=t0 + 899) is not None)
    report("a 90 s-old ref is gone", ring.get("f_00000000", now=t0 + 899) is None)


async def check_realtime_cadence() -> None:
    n = 20
    print(f"\n2. Real-time 1 Hz run ({n} ticks, ~{n}s) — cadence and drift")
    loop, ring, ticks = await build(n, period=1.0, device={"accel_rms": 0.04, "gps_speed": 0.2})

    ts = [t["t"] for t in ticks]
    intervals = [b - a for a, b in zip(ts, ts[1:])]
    worst = max(abs(i - 1.0) for i in intervals)
    # Measured tick-to-tick, not from process start: corpus/JPEG setup before the first
    # tick is not drift, and counting it would mask real drift behind a constant offset.
    drift = (ts[-1] - ts[0]) - (n - 1) * 1.0
    print(f"     mean interval={sum(intervals) / len(intervals):.4f}s "
          f"max_dev={worst * 1000:.1f}ms  drift={drift * 1000:+.1f}ms over {n} ticks")
    report("steady 1 Hz", worst < 0.05, f"worst deviation {worst * 1000:.1f}ms")
    report("no cumulative clock drift", abs(drift) < 0.15, f"{drift * 1000:+.1f}ms")
    report("device block present when the adapter supplies it",
           all("device" in t for t in ticks))
    # The first tick is warmup: no previous frame for frame_delta/flow, and numpy's
    # FFT plans are cold. Steady-state cost is what invariant 1 is about.
    steady = sorted(loop.stats.sensor_ms[1:])
    p99 = steady[min(int(len(steady) * 0.99), len(steady) - 1)]
    print(f"     sync work after warmup: mean={sum(steady) / len(steady):.2f}ms "
          f"p99={p99:.2f}ms max={steady[-1]:.2f}ms "
          f"(tick 1 warmup={loop.stats.sensor_ms[0]:.2f}ms)")
    # SPEC §2.3 estimates "~5 ms" for the sensor block, but that figure comes from a
    # tight benchmark loop. At a real 1 Hz the CPU idles between ticks and every tick
    # is a cold start, so steady state is ~5 ms with ~11 ms outliers. What invariant 1
    # actually requires is that the work be *bounded* and never block the clock — 11 ms
    # against a 1000 ms period is a 1% duty cycle, and the drift check above is the
    # real proof. We assert a generous bound and print the truth.
    report("synchronous work is bounded well inside the 1 s period (invariant 1)",
           p99 < 25.0, f"p99={p99:.2f}ms over {len(steady)} steady-state ticks")
    print(f"     {loop.stats.line()}")


async def check_consumer_isolation() -> None:
    print("\n3. A broken consumer does not stop the clock")
    ring, bus = FrameRing(), TickBus()
    seen: list[int] = []
    bus.subscribe(lambda t: seen.append(t["seq"]))
    bus.subscribe(lambda t: 1 / 0)
    tagger = T0Tagger(FakeClient(latency=0.001), log_every=0)
    loop = T0Loop(SyntheticSource(25, 0.002), ring=ring, tagger=tagger, bus=bus, log_every=0)
    await loop.run()
    report("loop completed despite a throwing subscriber", loop.stats.ticks == 25,
           f"{loop.stats.ticks} ticks emitted")
    report("healthy subscriber still received everything", seen == list(range(1, 26)))


async def main() -> int:
    print("A6 — tick assembly and emission")
    await check_long_run()
    await check_ttl_eviction()
    await check_consumer_isolation()
    await check_realtime_cadence()
    print(f"\n{'ALL CHECKS PASSED' if FAIL == 0 else f'{FAIL} CHECK(S) FAILED'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
