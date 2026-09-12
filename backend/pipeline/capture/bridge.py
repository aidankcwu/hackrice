"""In-process bridge from longevity's T0 producer to pipeline's consumers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from longevity.emit import TickBus as T0TickBus
from longevity.loop import T0Loop
from longevity.ring import FrameRing
from longevity.server.ingest import GlassesLink
from longevity.sources.base import CaptureSource
from longevity.vlm import T0Tagger, build_client

from ..bus import TickBus
from ..config import Settings
from ..models import Tick

log = logging.getLogger(__name__)


class LongevityCapture:
    """Own Person A's capture graph and synchronously forward validated ticks."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: Literal["glasses", "webcam", "replay"],
        our_bus: TickBus,
        dir: str | None,
        speed: float,
        loop: bool,
        camera: int,
        vlm: Literal["gemini", "fake", "off"],
        flow: str | None,
    ) -> None:
        # Settings reads .env into its model but does not export unrelated keys such
        # as GEMINI_API_KEY. T0's client reads os.environ when it is constructed.
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

        self.ring = FrameRing(ttl_s=settings.frame_ttl_s)
        self.link = GlassesLink()
        # The VLM budget follows the tick interval: a call that would return at
        # 1.2 s is worth keeping when the next frame is not due until 1.5 s.
        # Person A's default (1.0 s) dates from 1 Hz ticks and cut coverage to
        # ~70-80% at a ~840 ms median. Override with VLM_BUDGET_S.
        budget_s = settings.vlm_budget_s or max(0.5, settings.tick_interval_s - 0.1)
        self.vlm_budget_s = budget_s
        self.tagger = T0Tagger(build_client(vlm), budget_s=budget_s)
        self.his_bus = T0TickBus()
        self.source = self._build_source(
            source, dir=dir, speed=speed, loop=loop, camera=camera,
            period_s=settings.tick_interval_s,
        )
        self.loop = T0Loop(
            self.source, ring=self.ring, tagger=self.tagger, bus=self.his_bus,
            flow=flow,
        )
        self.converted = 0
        self.dropped = 0
        self._task: asyncio.Task[None] | None = None

        def forward(raw: dict) -> None:
            try:
                tick = Tick.model_validate(raw)
            except Exception as exc:  # validation must never escape into T0
                self.dropped += 1
                log.warning("dropping invalid T0 tick: %s", exc)
                return
            self.converted += 1
            our_bus.publish(tick)

        self.his_bus.subscribe(forward)

    def _build_source(
        self, source: str, *, dir: str | None, speed: float, loop: bool,
        camera: int, period_s: float,
    ) -> CaptureSource:
        if source == "glasses":
            from longevity.sources.glasses import GlassesSource
            return GlassesSource(self.link, interval=period_s)
        if source == "webcam":
            from longevity.sources.webcam import WebcamSource
            return WebcamSource(index=camera, interval=period_s)
        if source == "replay":
            if not dir:
                raise ValueError("--source replay needs --dir pointing at a corpus")
            from longevity.sources.replay import ReplaySource
            return ReplaySource(dir, speed=speed, loop=loop, interval=period_s)
        raise ValueError(f"unknown capture source: {source!r}")

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.loop.run(), name="t0-loop")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.source.aclose()

    def stats(self) -> dict[str, object]:
        return {
            "loop": self.loop.stats.line(),
            "tagger": self.tagger.stats_line(),
            "converted": self.converted,
            "dropped": self.dropped,
        }
