"""In-process bridge from longevity's T0 producer to pipeline's consumers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from longevity import wire
from longevity.emit import TickBus as T0TickBus
from longevity.loop import T0Loop
from longevity.ring import FrameRing
from longevity.server.ingest import GlassesLink
from longevity.sources.base import CaptureSource
from longevity.tick import frame_ref
from longevity.vlm import KINDS, T0Tagger, VLMClient, build_client
from longevity.watcher import Wakeup, Watcher, WatcherConfig
from longevity.watcher_model import build_watcher_model

from ..bus import TickBus
from ..config import Settings
from ..models import PendingQuestion, Tick
from .settings import POINT_CONCEPTS, CaptureSettings
from .speak import current_speech, speech_for

log = logging.getLogger(__name__)

#: The trailing window `stats()["labeler"]` counts calls over.
_HOUR_S = 3600.0


class _LabelerMeter:
    """Per-kind call starts over the trailing hour and the last wake call's latency.

    The tagger counts calls per kind since start, and its scheduler counts starts
    over the hour without their kinds; neither keeps per-kind timestamps or
    latencies. Both come from the public surface instead: `note_started` wraps the
    scheduler's (the tagger calls it with the kind just before it starts each
    call), and `_MeteredClient.tag` is then invoked by that call's task before its
    first await, so starts and `tag` calls pair up in order.
    """

    def __init__(self) -> None:
        self.starts: deque[tuple[float, str]] = deque()
        self._unpaired: deque[str] = deque(maxlen=8)
        self.last_heartbeat_t: float | None = None
        self.last_wake_latency_ms: float | None = None

    def wrap(self, scheduler: Any) -> None:
        inner = scheduler.note_started

        def note_started(kind: str, now: float) -> None:
            inner(kind, now)
            self.starts.append((now, kind))
            self._unpaired.append(kind)
            if kind == "heartbeat":
                self.last_heartbeat_t = now

        scheduler.note_started = note_started

    def pair(self) -> str | None:
        return self._unpaired.popleft() if self._unpaired else None

    def per_hour(self, now: float) -> dict[str, int]:
        while self.starts and self.starts[0][0] <= now - _HOUR_S:
            self.starts.popleft()
        counts = dict.fromkeys(KINDS, 0)
        for _, kind in self.starts:
            counts[kind] = counts.get(kind, 0) + 1
        return counts


class _MeteredClient:
    """A VLM client that times each ``wake`` call for `_LabelerMeter`."""

    def __init__(self, inner: VLMClient, meter: _LabelerMeter) -> None:
        self._inner = inner
        self._meter = meter

    def tag(self, jpeg: bytes, **kwargs: Any) -> Any:
        # Synchronous on purpose: the kind is paired at call time, in start order.
        kind = self._meter.pair()
        return self._timed(kind, self._inner.tag(jpeg, **kwargs))

    async def _timed(self, kind: str | None, call: Any) -> Any:
        started = time.perf_counter()
        result = await call
        if kind == "wake":
            self._meter.last_wake_latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return result

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __getattr__(self, name: str) -> Any:  # usage, calls, ... of the real client
        return getattr(self._inner, name)


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
        capture_settings: CaptureSettings | None = None,
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
        # Read after load_dotenv, so WATCHER=... in backend/.env applies.
        cs = self.capture_settings = capture_settings or CaptureSettings()
        self.watcher_error: str | None = None
        self.watcher = self._build_watcher(cs) if cs.watcher else None
        # The watcher scores up to watcher_fps_max frames a second, so the ring must
        # hold a full TTL of frames at that rate (7 fps -> 646, not 256).
        self.ring = FrameRing(
            ttl_s=settings.frame_ttl_s,
            fps_hint=cs.watcher_fps_max if self.watcher is not None else None,
        )
        self.link = GlassesLink()
        # The VLM budget is the full tick interval. It used to be interval - 0.1
        # because a call over budget was cancelled and lost, so the budget had to
        # leave room for the next call; calls now overlap (two in flight) and an
        # over-budget call finishes and is used while fresh, so the budget is only
        # the on-time line in the stats and there is no cliff to stay clear of.
        # Freshness for a late result is B's own window (Timings.ai_max_age_ms),
        # so T0 never attaches a block the gate would then ignore as stale.
        # Override with VLM_BUDGET_S.
        budget_s = settings.vlm_budget_s or max(0.5, settings.tick_interval_s)
        self.vlm_budget_s = budget_s
        # VLM_MAX_IN_FLIGHT=1 is the kill switch back to the serial tagger
        # (one call at a time); still only the ceiling cancels a call.
        self._meter = _LabelerMeter()
        client = build_client(vlm)
        self.tagger = T0Tagger(
            _MeteredClient(client, self._meter) if client is not None else None,
            budget_s=budget_s,
            max_age_s=settings.timings.ai_max_age_ms / 1000,
            max_in_flight=settings.vlm_max_in_flight,
            tick_interval_s=settings.tick_interval_s,
            heartbeat_s=cs.labeler_heartbeat_s,
            transition_s=cs.labeler_transition_s,
            steady_s=cs.effective_labeler_steady_s(),
            cooling_s=cs.labeler_cooling_s,
            max_per_hour=cs.labeler_max_per_hour,
            dormant_after_s=cs.labeler_dormant_after_s,
            dormant_heartbeat_s=cs.labeler_dormant_heartbeat_s,
            error_backoff_n=cs.labeler_error_backoff_n,
            point_concepts=POINT_CONCEPTS,
        )
        self._meter.wrap(self.tagger.scheduler)
        self.his_bus = T0TickBus()
        self.source = self._build_source(
            source, dir=dir, speed=speed, loop=loop, camera=camera,
            period_s=settings.tick_interval_s,
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

        # Publish-on-landing: when Gemini lands, T0 re-sends the newest tick
        # (same tick_id, t and seq) with its ai block attached, through the
        # same `forward`. Without it a landed result waited in the tagger for
        # the next frame -- 0.1-1.5 s on the cue path, the most on exactly the
        # calls that already ran late. The gate and the pipeline's downstream
        # both replace a re-sent tick rather than counting it twice.
        # PUBLISH_ON_LANDING=0 is the stage switch back to "a landed result
        # rides the next frame": no tick_id is ever published twice.
        self.loop = T0Loop(
            self.source, ring=self.ring, tagger=self.tagger, bus=self.his_bus,
            flow=flow, on_ai_update=forward if settings.publish_on_landing else None,
            watcher=self.watcher,
        )
        self.his_bus.subscribe(forward)
        #: Where an armed watch's wake-up goes: ``(watch_id, concept, frame_t)``.
        #: The action handler registers itself here when it is handed this
        #: bridge (``ActionHandler.capture``). Called on the asyncio loop.
        self.on_armed_wake: Callable[[str, str, float], None] | None = None
        self.loop.on_wakeup_forwarded = self._on_wakeup_forwarded

    def _build_watcher(self, cs: CaptureSettings) -> Watcher | None:
        """The watcher, or None with `watcher_error` set. Never raises: a missing
        extra or a failed download must not stop the backend from starting."""
        try:
            model = build_watcher_model(cs.watcher_model)
            return Watcher(
                model,
                WatcherConfig(
                    k=cs.watch_k, n=cs.watch_n, cooldown_s=cs.watch_cooldown_s,
                    novelty_enter=cs.watch_novelty_enter,
                    novelty_ema_s=cs.watch_novelty_ema_s,
                    min_sharpness=cs.watch_min_sharpness, min_lux=cs.watch_min_lux,
                ),
                thresholds=cs.thresholds(),
                point_concepts=POINT_CONCEPTS,
            )
        except Exception as exc:  # noqa: BLE001 - run without a watcher instead
            self.watcher_error = f"{type(exc).__name__}: {exc}"
            log.error(
                "watcher %r unavailable, running without it (labeler on every tick): %s. "
                "Fix: `uv sync --extra watcher` for MobileCLIP, WATCHER_MODEL=fake, "
                "or WATCHER=0.", cs.watcher_model, self.watcher_error,
            )
            return None

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

    # --- targeted look (docs/PERCEPTION.md "Labeler" 4) ------------------------

    def look(self, question: str) -> None:
        """Ask one question of the newest frame through the labeler's mailbox.

        Returns at once; the answer is read back with `take_look_answer()`. The
        frame is the newest in the ring, or the newest the loop has seen when the
        ring cannot serve it (a replay corpus whose timestamps the 90 s window
        has long expired) -- the same pick a wake-up makes.
        """
        stored = self.ring.get(frame_ref(self.loop.seq)) if self.loop.seq else None
        last = getattr(self.loop, "_last_frame", None)
        if stored is not None and (last is None or stored.t >= last[0]):
            frame_t, jpeg = stored.t, stored.jpeg
        elif last is not None:
            frame_t, jpeg = last
        else:
            log.info("look dropped: no frame to label yet (%r)", question)
            return
        self.tagger.request("look", frame_t, jpeg, question=question)

    # --- armed watches (docs/PERCEPTION.md "Gate and actions") -----------------

    def arm(self, concept: str, within_s: float, watch_id: str) -> bool:
        """Arm the watcher: wake with ``watch_armed:<watch_id>`` the first usable
        frame with `concept` above its enter threshold within `within_s`. False
        when there is no watcher, so the caller can fall back to a timed check."""
        if self.watcher is None:
            return False
        self.watcher.arm(concept, float(within_s), watch_id)
        return True

    def disarm(self, watch_id: str) -> None:
        if self.watcher is not None:
            self.watcher.disarm(watch_id)

    def _on_wakeup_forwarded(self, wake: Wakeup) -> None:
        """The loop's hook: an armed wake-up becomes one `on_armed_wake` call."""
        family, _, watch_id = wake.reason.partition(":")
        if family != "watch_armed" or not watch_id:
            return
        callback = self.on_armed_wake
        if callback is None:
            log.info("armed watch %s woke but nobody is listening", watch_id)
            return
        callback(watch_id, wake.concepts[0] if wake.concepts else "", wake.frame_t)

    @property
    def look_wait_s(self) -> float:
        """How long a look's answer is worth polling for: the tagger's hard
        ceiling plus one tick for the mailbox to start the call."""
        return float(self.tagger.stats()["ceiling_s"]) + float(self.settings.tick_interval_s)

    def take_look_answer(self) -> tuple[float, str] | None:
        """The newest targeted-look answer as `(frame_t, answer)`, exactly once."""
        return self.loop.take_look_answer()

    def stats(self) -> dict[str, object]:
        return {
            "loop": self.loop.stats.line(),
            "tagger": self.tagger.stats_line(),
            "converted": self.converted,
            "dropped": self.dropped,
            "watcher": self.watcher.stats() if self.watcher is not None else None,
            "labeler": self.labeler_stats(),
            "watcher_error": self.watcher_error,
        }

    def labeler_stats(self) -> dict[str, object]:
        """Labeler health (docs/PERCEPTION.md): with the watcher on, heartbeat age
        and wake latency replace AI coverage, and frames sent per hour is the
        privacy number. Every started call sends exactly one frame."""
        now = time.time()  # the tagger's clock, which the scheduler's starts use
        per_hour = self._meter.per_hour(now)
        sched = self.tagger.scheduler.stats()
        last_hb = self._meter.last_heartbeat_t
        return {
            "calls_per_hour": per_hour,
            "capped": sched["capped"],
            "last_heartbeat_age_s": None if last_hb is None else round(now - last_hb, 1),
            "last_wake_latency_ms": self._meter.last_wake_latency_ms,
            "frames_sent_per_hour": sum(per_hour.values()),
            "mode": sched["mode"],
            "steady_s": self.tagger.scheduler.steady_s,
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
