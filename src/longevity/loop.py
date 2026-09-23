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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .emit import JSONLWriter, SQLiteMirror, TickBus
from .ring import FrameRing
from .sensors import SensorComputer, quick_quality
from .server.ingest import Packet
from .sources.base import CaptureSource
from .sources.glasses import GlassesSource
from .tick import ai_block, build_tick, frame_ref
from .vlm import T0Tagger, split_look_answer
from .watcher import Wakeup, Watcher

log = logging.getLogger(__name__)

#: A wake-up whose concepts no labeler result has confirmed or denied within this
#: many seconds (frame time) is forgotten without marking the watcher either way.
WAKE_CONFIRM_WINDOW_S = 10.0


@dataclass
class LoopStats:
    ticks: int = 0
    started: float = field(default_factory=time.monotonic)
    sensor_ms: list[float] = field(default_factory=list)
    with_ai: int = 0
    with_device: int = 0
    watch_ticks: int = 0
    """Ticks that carried a `watch` block."""
    slow_ticks: int = 0
    """Ticks whose synchronous work exceeded 5 ms — invariant 1 getting tight."""
    wakeups_forwarded: int = 0
    """Watcher wake-ups turned into ``wake`` labeler requests."""
    labeler_requests_by_kind: dict[str, int] = field(default_factory=dict)
    """Labeler requests this loop issued, per kind (the watcher path only)."""
    look_answers: int = 0
    """Targeted-look answers stripped out of landed results."""

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
            f"watch={self.watch_ticks}/{self.ticks} "
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
        on_ai_update: Callable[[dict[str, Any]], None] | None = None,
        watcher: Watcher | None = None,
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
        # Publish-on-landing (opt-in). Without it a result that lands 1.1 s into a
        # 1.5 s tick waits ~0.4 s for the next tick before anyone sees it -- and the
        # faster Gemini gets, the more of its speed that wait throws away. With it,
        # the landed result is attached to the newest tick (already emitted, no `ai`
        # yet) and that tick is re-sent to `on_ai_update` with the same tick_id, t
        # and seq: tick timestamps keep their meaning, only the `ai` block arrives
        # early. It is opt-in because a consumer that appends every tick it receives
        # to a window would count the re-send twice; it must replace by tick_id.
        self._on_ai_update = on_ai_update
        self._last_tick: dict[str, Any] | None = None
        if on_ai_update is not None:
            tagger.on_result = self._attach_landed

        # The watcher scores every frame the phone sends, not one per tick
        # (docs/PERCEPTION.md "Watcher"). Under `glasses` that means a hook on the
        # link, fed a cheap 128 px quality pair because those frames never get a full
        # sensor block; any other source hands the watcher its tick frame in
        # `_on_frame`, with the sensor block it already paid for.
        #
        # With a watcher the labeler no longer runs on every tick: its wake-ups
        # become ``wake`` requests the moment they fire, and the tagger's scheduler
        # decides per tick whether this is a ``hot`` or ``heartbeat`` tick or
        # nothing at all (docs/PERCEPTION.md "Labeler"). Without one, `offer()`
        # per tick, exactly as before.
        self._watcher = watcher
        self._link = source.link if watcher is not None and isinstance(source, GlassesSource) else None
        if self._link is not None:
            if self._link.on_packet is not None:
                log.warning("T0: replacing an existing on_packet hook on the glasses link")
            self._link.on_packet = self._on_packet
        if watcher is not None:
            watcher.on_wakeup = self._on_wakeup
        # The asyncio loop `run()` executes on: the watcher's worker thread hands
        # wake-ups over through it, so the tagger is only ever touched from here.
        self._aloop: asyncio.AbstractEventLoop | None = None
        # `(t, jpeg)` of the newest frame this loop has seen (every packet under
        # glasses, every tick frame otherwise). A wake-up labels the newest frame,
        # and this is what stands in when the ring cannot serve it (replay corpora
        # carry timestamps the 90 s window has long expired).
        self._last_frame: tuple[float, bytes] | None = None
        # Wake-ups awaiting a labeler verdict: frame time of the wake request ->
        # the concepts it flagged. The result on that frame (or a newer one)
        # confirms or denies each, which is what tunes the watcher's re-wake cooldown.
        self._pending_wakes: dict[float, tuple[str, ...]] = {}
        #: The newest targeted-look answer, `(frame_t, answer)`, until taken.
        self.last_look_answer: tuple[float, str] | None = None
        #: Called on the asyncio loop with each wake-up just forwarded to the
        #: labeler, so a consumer past the tick (the backend's armed watches,
        #: reason ``watch_armed:<id>``) learns of it without reading the watcher.
        self.on_wakeup_forwarded: Callable[[Wakeup], None] | None = None

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
        self._aloop = asyncio.get_running_loop()
        await self._tagger.start()
        if self._watcher is not None:
            await self._watcher.start()
        try:
            async for frame in self._source.frames():
                self._on_frame(frame)
        finally:
            if self._link is not None and self._link.on_packet == self._on_packet:
                self._link.on_packet = None
            await self._tagger.aclose()
            if self._watcher is not None:
                await self._watcher.aclose()
            self._aloop = None

    def _on_packet(self, packet: Packet) -> None:
        """Every accepted glasses packet, synchronously from ingest. ~0.5 ms."""
        self._last_frame = (packet.t, packet.jpeg)
        self._watcher.offer(packet.t, packet.jpeg, sensor=quick_quality(packet.jpeg))  # type: ignore[union-attr]

    # -- wake-ups: watcher thread -> asyncio loop -> labeler --

    def _on_wakeup(self, wake: Wakeup) -> None:
        """The watcher's `on_wakeup`, called from its worker thread. Thread-safe.

        Nothing here touches the tagger or the ring: the wake-up is handed to the
        asyncio loop, which runs `_forward_wakeup` at its next iteration -- well
        before the next tick. Without a running loop (before `run()`, or after it
        ended) the wake-up is dropped: there is no labeler to hand it to.
        """
        aloop = self._aloop
        if aloop is None or aloop.is_closed():
            return
        try:
            aloop.call_soon_threadsafe(self._forward_wakeup, wake)
        except RuntimeError:  # closed between the check and the call
            pass

    def _forward_wakeup(self, wake: Wakeup) -> None:
        """On the asyncio loop: one ``wake`` request on the newest frame we have.

        "The call labels the newest frame, not the flagged one" (docs/PERCEPTION.md
        "Labeler" 1). That is the newest frame in the ring, or the newest frame
        this loop has seen when the ring cannot serve it, whichever is younger.
        The watcher's mailbox is drained here so an unconsumed wake-up never has
        to be superseded; the `Wakeup` in hand already carries everything needed.
        """
        if self._watcher is None or self._aloop is None:  # `run()` already ended
            return
        self._watcher.take_wakeup()
        stored = self._ring.get(frame_ref(self.seq)) if self.seq else None
        last = self._last_frame
        if stored is not None and (last is None or stored.t >= last[0]):
            frame_t, jpeg = stored.t, stored.jpeg
        elif last is not None:
            frame_t, jpeg = last
        else:
            log.debug("T0: wake-up %s dropped, no frame to label yet", wake.reason)
            return
        self._request("wake", frame_t, jpeg)
        self.stats.wakeups_forwarded += 1
        if wake.concepts:
            held = self._pending_wakes.get(frame_t, ())
            self._pending_wakes[frame_t] = held + tuple(c for c in wake.concepts if c not in held)
        if self.on_wakeup_forwarded is not None:
            try:
                self.on_wakeup_forwarded(wake)
            except Exception:  # noqa: BLE001 - a listener must not stop the loop
                log.exception("on_wakeup_forwarded listener raised; continuing")

    def _request(self, kind: str, frame_t: float, jpeg: bytes) -> None:
        self._tagger.request(kind, frame_t, jpeg)  # type: ignore[arg-type]
        by_kind = self.stats.labeler_requests_by_kind
        by_kind[kind] = by_kind.get(kind, 0) + 1

    def _schedule_labeler(self, frame: Any, watch: dict[str, Any] | None) -> None:
        """Ask the scheduler what this tick is and issue that request, if any.

        `cooling` is read before `hot` on purpose: a concept that goes cold on the
        clock between the two reads then looks cooling for one more tick rather
        than hot, and "hot" is the reading that would restart a transition.
        """
        assert self._watcher is not None
        cooling = self._watcher.cooling_concepts()
        hot = self._watcher.hot_concepts() - cooling
        novelty = float(watch.get("novelty", 0.0)) if watch else 0.0
        kind = self._tagger.scheduler.on_tick(
            frame.t, hot=hot, cooling=cooling, novelty=novelty, clock=time.time(),
        )
        if kind is not None:
            self._request(kind, frame.t, frame.jpeg)

    # -- landed results: look answers out, wake-ups confirmed, then the ai block --

    def _landed_ai(self, claimed: tuple[dict[str, Any], float] | None, *, now: float) -> dict[str, Any] | None:
        """The `ai` block for a claimed result, or None.

        A targeted look's answer is split off *before* the block is built and kept
        for `take_look_answer()`, so no tick ever carries the look key. With a
        watcher, the result also settles the newest wake-up it can vouch for.
        """
        if claimed is None:
            return None
        fields, as_of = claimed
        fields, answer = split_look_answer(fields)
        if answer is not None:
            self.last_look_answer = (as_of, answer)
            self.stats.look_answers += 1
        if self._watcher is not None:
            self._settle_wakes(fields, as_of, now)
        return ai_block(fields, as_of=as_of, now=now)

    def _settle_wakes(self, fields: dict[str, Any], as_of: float, now: float) -> None:
        """Confirm or deny the newest pending wake-up this result is evidence for.

        A result confirms a concept when its §9 boolean is true and denies it
        otherwise, which resets or doubles the watcher's re-wake cooldown. Only a
        result on the wake's frame or a newer one counts; wake-ups older than
        `WAKE_CONFIRM_WINDOW_S` are forgotten without a verdict, and once a newer
        wake-up is settled, older ones are superseded rather than judged twice.
        """
        pending = self._pending_wakes
        for t in [t for t in pending if now - t > WAKE_CONFIRM_WINDOW_S]:
            del pending[t]
        eligible = [t for t in pending if t <= as_of]
        if not eligible:
            return
        newest = max(eligible)
        concepts = pending[newest]
        for t in eligible:
            del pending[t]
        assert self._watcher is not None
        for concept in concepts:
            if fields.get(concept) is True:
                self._watcher.mark_confirmed(concept)
            else:
                self._watcher.mark_unconfirmed(concept)

    def take_look_answer(self) -> tuple[float, str] | None:
        """The newest targeted-look answer as `(frame_t, answer)`, exactly once."""
        answer, self.last_look_answer = self.last_look_answer, None
        return answer

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
        if self._last_frame is None or frame.t >= self._last_frame[0]:
            self._last_frame = (frame.t, frame.jpeg)

        # 2b. The watcher. Glasses frames already reached it through the packet hook;
        #     every other source feeds it here, once per frame. `take_tick` returns
        #     the aggregate since the last tick and never waits on inference. It is
        #     taken before the labeler is scheduled so `hot` and `novelty` are this
        #     tick's.
        watch = None
        if self._watcher is not None:
            if self._link is None:
                self._watcher.offer(frame.t, frame.jpeg, sensor=sensor)
            watch = self._watcher.take_tick(frame.t)

        # 3. Offer the frame to the VLM and claim anything that has landed. Both of
        #    these return immediately; the network is never on this code path.
        #    `take(now=...)` drops a result whose frame is past the freshness window,
        #    so a call that finished late is used only while it is still evidence.
        #    Without a watcher every tick is a ``hot`` request, as it always was;
        #    with one the scheduler picks the kind, and wake-ups arrive on their own.
        if self._watcher is None:
            self._tagger.offer(frame.t, frame.jpeg)
        else:
            self._schedule_labeler(frame, watch)
        ai = self._landed_ai(self._tagger.take(now=frame.t), now=frame.t)

        # 4. Assemble. `device` is already derived by the glasses adapter and is None
        #    for webcam/replay, which is exactly what §12.1 promises B.
        tick = build_tick(
            seq=seq, t=frame.t, sensor=sensor, device=frame.device, watch=watch, ai=ai
        )

        # 5. Hand it off. None of these may block or throw upward.
        self._last_tick = tick
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
        st.watch_ticks += watch is not None
        if elapsed_ms > 5.0:
            st.slow_ticks += 1
        if self._log_every and st.ticks % self._log_every == 0:
            if self._watcher is not None:
                w = self._watcher.stats()
                sched = self._tagger.scheduler.stats()
                requests = " ".join(f"{k}={n}" for k, n in sorted(st.labeler_requests_by_kind.items()))
                log.info(
                    "T0 %s | %s | watcher %s frames=%d usable=%d dropped=%d wakeups=%d "
                    "errors=%d p50=%.1fms p99=%.1fms hot=%s | labeler mode=%s "
                    "forwarded=%d requests[%s]",
                    st.line(), self._tagger.stats_line(), w["model"], w["frames"],
                    w["usable"], w["dropped"], w["wakeups"], w["errors"],
                    w["inference_ms_p50"], w["inference_ms_p99"], ",".join(w["hot"]) or "-",
                    sched["mode"], st.wakeups_forwarded, requests or "-",
                )
            else:
                log.info("T0 %s | %s", st.line(), self._tagger.stats_line())

    def _attach_landed(self) -> None:
        """A result just landed: attach it to the newest tick if that tick has none.

        Runs synchronously from the tagger task, so it is as bounded as `_on_frame`.
        The result's frame is never newer than the newest tick (a call only starts on
        a frame that was already offered), so `age_ms` against that tick is >= 0.
        """
        last = self._last_tick
        if last is None or "ai" in last or self._on_ai_update is None:
            return
        ai = self._landed_ai(self._tagger.claim(now=last["t"]), now=last["t"])
        if ai is None:
            return
        updated = dict(last, ai=ai)
        self._last_tick = updated
        self.stats.with_ai += 1
        try:
            self._on_ai_update(updated)
        except Exception:  # noqa: BLE001 - a consumer must not kill the tagger
            log.exception("on_ai_update consumer raised; continuing")
