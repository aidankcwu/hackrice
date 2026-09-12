"""In-process bridge from longevity's T0 producer to pipeline's consumers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from longevity import wire
from longevity.emit import TickBus as T0TickBus
from longevity.loop import T0Loop
from longevity.ring import FrameRing
from longevity.server.ingest import GlassesLink
from longevity.sources.base import CaptureSource
from longevity.vlm import T0Tagger, build_client

from ..bus import TickBus
from ..config import Settings
from ..models import PendingQuestion, Tick
from .speak import current_speech, speech_for

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

        self.settings = settings
        #: How long the phone is told to keep the microphone open (§4). The
        #: bridge is handed the same number ``make_speak_fn`` gets its Settings
        #: from, so the window the phone opens and the deadline the manager
        #: computes come from one config object.
        self.ask_listen_s = settings.timings.ask_listen_s
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
        self._speech_stats = None

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

    # --- ask / answer (ASK_DESIGN §8.2) ----------------------------------------

    def supports_ask(self) -> bool:
        """Did the connected phone advertise that it can open the mic (§8.7)?"""
        return self.link.supports("ask")

    async def send_question(self, q: PendingQuestion) -> bool:
        """Speak one question, then tell the phone to listen. True iff both landed.

        One coroutine, in order, because the order is the contract: the phone must
        not open the microphone until the audio it is answering has finished
        playing, and the only thing that guarantees the `ask` arrives after the
        audio is sending it after the audio. Synthesis is awaited here rather than
        fired like `speak` — a question whose audio never rendered must not leave
        an open row waiting for an answer to a sentence nobody heard.

        One *connection*, not a broadcast: the socket that carries the audio is
        pinned up front and carries the `ask` too. Broadcasting would let the two
        halves land on different phones when one reconnects mid-exchange — the
        mic opening on a device that never heard the question, and the row left
        waiting for an answer from a wearer who was never asked (§8.7).

        Returns False if the pick found nothing or either send failed on the
        pinned socket; the manager then finalises the row `suppressed` with
        `send_failed` (§8.2). Failure is reported, never raised: this runs as a
        task the manager owns.
        """
        ws = self.link.pick("ask")
        if ws is None:
            log.warning("question %s: no connected phone can open the mic", q.id)
            return False
        spoken = await speech_for(self.link, self.settings).send_to(
            ws, q.question, "normal"
        )
        if not spoken:
            log.warning("question %s: nobody heard the audio; not opening the mic", q.id)
            return False
        sent = await self.link.send_to(
            ws, wire.ask_message(q.id, self.ask_listen_s, q.answer_kind, q.question)
        )
        if not sent:
            log.warning("question %s: audio went out but the ask did not", q.id)
            return False
        log.info(
            "question %s sent: %r (%s, listening %.1fs)",
            q.id, q.question, q.answer_kind, self.ask_listen_s,
        )
        return True

    def stats(self) -> dict[str, object]:
        return {
            "loop": self.loop.stats.line(),
            "tagger": self.tagger.stats_line(),
            "converted": self.converted,
            "dropped": self.dropped,
        }

    def speech_stats(self) -> dict[str, object]:
        stats = self._speech_stats
        if stats is None:
            # `send_question` may have built the synthesiser before wiring handed
            # one over (or instead of it, in a test); its counters are still the
            # honest answer to "did anything reach the glasses".
            speech = current_speech(self.link)
            stats = speech.stats if speech is not None else None
        return stats.as_dict() if stats is not None else {"mode": "none"}
