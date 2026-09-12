"""T0 — the tick producer (PERSON_A.md A6, SPEC §2).

"Emit exactly one timestamp object per second, forever, regardless of what any other
layer is doing." (§2.1)

This is the join: every other module in Person A's half is a component, and this is the
thing that runs them on a clock. It is deliberately boring. The only interesting
property it has is what it *refuses* to do — it never awaits the VLM, never waits on a
consumer, and never lets an exception anywhere downstream stop the next tick.

Cadence is owned by the `CaptureSource`, which drives off a monotonic target rather
than `sleep(1)` in a loop; repeated `sleep(1)` accumulates drift you notice at minute
twelve, not minute one. This loop's job is to not *add* any.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .emit import JSONLWriter, SQLiteMirror, TickBus
from .ring import FrameRing
from .sensors import SensorComputer
from .sources.base import CaptureSource
from .tick import ai_block, build_tick, frame_ref
from .vlm import T0Tagger

log = logging.getLogger(__name__)


@dataclass
class LoopStats:
    ticks: int = 0
    started: float = field(default_factory=time.monotonic)
    sensor_ms: list[float] = field(default_factory=list)
    with_ai: int = 0
    with_device: int = 0
    slow_ticks: int = 0
    """Ticks whose synchronous work exceeded 5 ms — invariant 1 getting tight."""

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def line(self) -> str:
        if not self.ticks:
            return "no ticks yet"
        s = sorted(self.sensor_ms)
        p99 = s[min(int(len(s) * 0.99), len(s) - 1)]
        mean = sum(s) / len(s)
        rate = self.ticks / self.elapsed if self.elapsed else 0.0
        return (
            f"ticks={self.ticks} rate={rate:.3f}Hz "
            f"ai={self.with_ai / self.ticks:.0%} "
            f"device={self.with_device}/{self.ticks} "
            f"sync_mean={mean:.2f}ms sync_p99={p99:.2f}ms slow={self.slow_ticks}"
        )


class T0Loop:
    """Frame in, tick out, once per second."""

    def __init__(
        self,
        source: CaptureSource,
        *,
        ring: FrameRing,
        tagger: T0Tagger,
        bus: TickBus,
        mirror: SQLiteMirror | None = None,
        jsonl: JSONLWriter | None = None,
        flow: str | None = None,
        log_every: int = 30,
    ) -> None:
        self._source = source
        self._ring = ring
        self._tagger = tagger
        self._bus = bus
        self._mirror = mirror
        self._jsonl = jsonl
        self._flow = flow
        self._sensors = SensorComputer(flow=flow)
        self._log_every = log_every
        self.stats = LoopStats()
        self.seq = 0

    def _warm_up(self) -> None:
        """Touch every numpy/Pillow path once before the clock starts.

        At 1 Hz the CPU idles for a full second between ticks, so caches are cold and
        the first real tick costs ~11 ms against a ~5 ms steady state. Warming runs on
        a throwaway computer so tick 1's `frame_delta` still correctly has no
        predecessor.
        """
        import io

        import numpy as np
        from PIL import Image

        grad = (np.mgrid[0:256, 0:256][0] % 251).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(np.dstack([grad] * 3)).save(buf, format="JPEG", quality=70)
        warm = SensorComputer(flow=self._flow)
        for _ in range(3):
            warm.compute(buf.getvalue())

    async def run(self) -> None:
        self._warm_up()
        await self._tagger.start()
        try:
            async for frame in self._source.frames():
                self._on_frame(frame)
        finally:
            await self._tagger.aclose()

    def _on_frame(self, frame: Any) -> None:
        """Everything here is synchronous and bounded. Invariant 1."""
        t0 = time.perf_counter()
        self.seq += 1
        seq = self.seq
        ref = frame_ref(seq)

        # 1. Sensor fields — always present (§12.1), ~1.7 ms measured.
        try:
            sensor = self._sensors.compute(frame.jpeg)
        except Exception:  # noqa: BLE001
            log.exception("sensor computation failed at seq=%d", seq)
            from .sensors import empty_sensor_block

            sensor = empty_sensor_block()

        # 2. Park the frame in the 90 s RAM ring. Never to disk (invariant 4).
        self._ring.put(ref, frame.jpeg, frame.t)

        # 3. Offer the frame to the VLM and claim anything that has landed. Both of
        #    these return immediately; the network is never on this code path.
        self._tagger.offer(frame.t, frame.jpeg)
        claimed = self._tagger.take()
        ai = ai_block(claimed[0], as_of=claimed[1], now=frame.t) if claimed else None

        # 4. Assemble. `device` is already derived by the glasses adapter and is None
        #    for webcam/replay, which is exactly what §12.1 promises B.
        tick = build_tick(
            seq=seq, t=frame.t, sensor=sensor, device=frame.device, ai=ai
        )

        # 5. Hand it off. None of these may block or throw upward.
        self._bus.publish(tick)
        if self._mirror is not None:
            self._mirror.write(tick)
        if self._jsonl is not None:
            self._jsonl.write(tick)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        st = self.stats
        st.ticks += 1
        st.sensor_ms.append(elapsed_ms)
        st.with_ai += ai is not None
        st.with_device += frame.device is not None
        if elapsed_ms > 5.0:
            st.slow_ticks += 1
        if self._log_every and st.ticks % self._log_every == 0:
            log.info("T0 %s | %s", st.line(), self._tagger.stats_line())
