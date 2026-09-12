"""Synthetic tick source.

Produces :class:`~pipeline.models.Tick` objects that look enough like Person A's
output (SPEC §12) to build the whole downstream pipeline against, including the
awkward part: the ``ai`` block is only present on ~65% of ticks, matching the
0.5-0.8 Hz coverage SPEC §2.4 predicts. Any consumer that passes against this
source has been forced to tolerate the gaps.

Cadence is a parameter, not 1 Hz: ``interval_s`` is the gap between ticks, and
the glasses emit one every 1.5 s. The *scenario* is still scripted in seconds,
so a 60 s segment is 40 ticks at 1.5 s rather than 60 -- which is exactly the
pressure every "N hits in W seconds" threshold has to survive.

Timestamps are always real wall clock, even at ``speed > 1`` -- the pipeline sees
plausible times and we simply sleep less between ticks.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import random
import time
from typing import AsyncIterator

from PIL import Image, ImageDraw

from ..frames import FrameStore
from ..models import AiBlock, DeviceBlock, SensorBlock, Tick
from .scenario import DEFAULT_SCENARIO, Scenario, Segment

log = logging.getLogger(__name__)

__all__ = ["SimSource", "segment_phash_base", "render_placeholder_jpeg"]

FRAME_W = 512
FRAME_H = 384


def segment_phash_base(segment_index: int, name: str) -> int:
    """A stable 64-bit perceptual-hash base for a segment.

    Ticks inside a segment stay near this value; crossing a boundary jumps far
    from it, which is what frame selection by phash distance (SPEC §4.3) keys on.
    """

    digest = hashlib.sha256(f"{segment_index}:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def render_placeholder_jpeg(
    segment: Segment, seq: int, size: tuple[int, int] = (FRAME_W, FRAME_H)
) -> bytes:
    """A ~512px JPEG standing in for a real frame (SPEC §2.2 sizing)."""

    img = Image.new("RGB", size, segment.bg_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, size[0] - 8, size[1] - 8], outline=(230, 230, 230), width=2)
    draw.text((24, 24), segment.name, fill=(245, 245, 245))
    draw.text((24, 44), f"seq {seq}", fill=(245, 245, 245))
    draw.text((24, 64), f"{segment.scene} / {segment.activity}", fill=(220, 220, 220))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


class SimSource:
    """Async generator of synthetic ticks.

    Usage::

        src = SimSource(DEFAULT_SCENARIO, frame_store, speed=20)
        async for tick in src:
            bus.publish(tick)
    """

    def __init__(
        self,
        scenario: Scenario | None = None,
        frame_store: FrameStore | None = None,
        speed: float = 1.0,
        ai_coverage: float = 0.65,
        seed: int = 0,
        max_ticks: int | None = None,
        interval_s: float = 1.0,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        if not 0.0 <= ai_coverage <= 1.0:
            raise ValueError("ai_coverage must be in [0, 1]")
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        self.scenario = scenario if scenario is not None else DEFAULT_SCENARIO
        self.frame_store = frame_store
        self.speed = speed
        self.ai_coverage = ai_coverage
        self.seed = seed
        self.max_ticks = max_ticks
        #: Seconds of scenario time per tick (SPEC §2.1 is 1 Hz; the glasses
        #: run at 1.5 s). Both ``tick.t`` and the scenario lookup use it, so a
        #: segment keeps its scripted duration in *seconds* at any cadence.
        self.interval_s = interval_s

        self._rng = random.Random(seed)
        self._seq = 0
        #: Simulated clock origin. Tick time is ``start_t + seq * interval_s``
        #: (one clock, driven by tick.t, so ``--speed`` never desyncs
        #: cooldowns/TTLs and the seeded series, keyed on scenario seconds
        #: from this same origin, lands in the same place at any cadence).
        self.start_t = time.time()
        self._last_ai_t: float | None = None
        self._segment_index: int | None = None
        self._phash_current: int = 0

    # -- field synthesis -------------------------------------------------

    def _phash(self, segment_index: int, name: str) -> str:
        """Near-constant within a segment, a large jump across boundaries."""

        if segment_index != self._segment_index:
            self._segment_index = segment_index
            self._phash_current = segment_phash_base(segment_index, name)
        else:
            # Flip a couple of bits so consecutive hashes differ slightly.
            for _ in range(self._rng.randint(1, 3)):
                self._phash_current ^= 1 << self._rng.randrange(64)
        return f"{self._phash_current & 0xFFFF_FFFF_FFFF_FFFF:016x}"

    def _sensor(self, segment: Segment, phash: str) -> SensorBlock:
        rng = self._rng
        jitter = rng.uniform(-0.08, 0.08)

        if segment.outdoor:
            lux = 2200.0 * (1.0 + jitter)
            cct = 6200.0 + rng.uniform(-250, 250)
        elif segment.scene == "home":
            lux = 180.0 * (1.0 + jitter)
            cct = 2900.0 + rng.uniform(-150, 150)
        elif segment.scene == "restaurant":
            lux = 320.0 * (1.0 + jitter)
            cct = 3300.0 + rng.uniform(-200, 200)
        else:  # office and everything else indoor
            lux = 420.0 * (1.0 + jitter)
            cct = 4300.0 + rng.uniform(-200, 200)

        m = segment.motion_level
        frame_delta = max(0.0, m * 0.5 + rng.uniform(-0.03, 0.03))
        flow_mag = max(0.0, m * 0.35 + rng.uniform(-0.02, 0.02))
        sharpness = max(5.0, 95.0 - m * 40.0 + rng.uniform(-6, 6))
        hist_spread = min(1.0, max(0.0, 0.55 + (0.2 if segment.outdoor else 0.0) + jitter))

        return SensorBlock(
            lux_proxy=round(lux, 1),
            cct=round(cct, 1),
            hist_spread=round(hist_spread, 3),
            frame_delta=round(frame_delta, 4),
            flow_mag=round(flow_mag, 4),
            sharpness=round(sharpness, 1),
            phash=phash,
        )

    def _device(self, segment: Segment) -> DeviceBlock:
        rng = self._rng
        accel = max(0.0, segment.motion_level * 0.9 + rng.uniform(-0.02, 0.02))
        walking = segment.activity == "walking"
        speed = (1.3 + rng.uniform(-0.2, 0.2)) if walking else max(0.0, rng.uniform(0, 0.08))
        return DeviceBlock(accel_rms=round(accel, 4), gps_speed=round(speed, 3))

    def _ai(self, segment: Segment, t: float) -> AiBlock | None:
        """Present with probability ``ai_coverage``; otherwise ``None`` (SPEC §12.2)."""

        if self._rng.random() >= self.ai_coverage:
            return None
        age_ms = 0 if self._last_ai_t is None else int((t - self._last_ai_t) * 1000)
        self._last_ai_t = t
        return AiBlock(
            as_of=t,
            age_ms=age_ms,
            scene=segment.scene,
            activity=segment.activity,
            food_present=segment.food_present,
            food_type=segment.food_type,
            caffeine_visible=segment.caffeine_visible,
            alcohol_visible=segment.alcohol_visible,
            screen_present=segment.screen_present,
            vegetation_visible=segment.vegetation_visible,
            people_present=segment.people_present,
            conf=round(self._rng.uniform(0.72, 0.95), 3),
        )

    # -- tick production -------------------------------------------------

    def next_tick(self, t: float | None = None) -> Tick:
        """Build one tick for script time ``seq * interval_s`` seconds in.

        Exposed separately from :meth:`ticks` so tests can drive the source
        without sleeping.
        """

        seq = self._seq
        self._seq += 1
        elapsed = float(seq) * self.interval_s
        now = (self.start_t + elapsed) if t is None else t
        index, segment, _offset = self.scenario.segment_at(elapsed)

        phash = self._phash(index, segment.name)
        frame_ref = f"f_{seq:08d}"

        if self.frame_store is not None:
            self.frame_store.put(frame_ref, render_placeholder_jpeg(segment, seq), now)

        return Tick(
            v=1,
            tick_id=f"t_{seq:08d}",
            t=now,
            seq=seq,
            sensor=self._sensor(segment, phash),
            device=self._device(segment),
            ai=self._ai(segment, now),
            frame_ref=frame_ref,
        )

    async def ticks(self) -> AsyncIterator[Tick]:
        """Yield ticks; ``tick.t`` advances exactly ``interval_s`` per tick
        while the wall-clock gap between ticks is ``interval_s / speed``."""

        interval = self.interval_s / self.speed
        next_at = time.monotonic()
        while self.max_ticks is None or self._seq < self.max_ticks:
            yield self.next_tick()
            next_at += interval
            delay = next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                # Fell behind: resync rather than accumulate debt (SPEC §5.2).
                next_at = time.monotonic()
                await asyncio.sleep(0)

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self.ticks()

    @property
    def seq(self) -> int:
        return self._seq
