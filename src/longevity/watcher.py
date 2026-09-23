"""The watcher: every frame scored, a wake-up when a moment is notable (docs/PERCEPTION.md).

One image-embedding model runs on every frame next to the sensor block and answers one
question: is anything here worth a Gemini call? It never names things, never speaks and
never blocks the tick. The rules, all load-bearing:

1. **Inference runs on one daemon thread fed by a one-slot mailbox.** A frame arriving
   while the model is busy replaces the waiting one (counted as `dropped`), never queues
   behind it. `offer()` never touches the model. `run_inline=True` scores on the caller's
   thread instead, which is what makes tests deterministic.
2. **Unusable frames wake nothing.** The quality gate (sharpness, lux from the sensor
   block) runs before the model; a gated frame counts in `frames` but not `usable` and
   updates no state.
3. **Two-threshold hysteresis per concept**: `cold -> hot` when the score is above `enter`
   on k of the last n usable frames, `hot -> cooling` when below `exit` on k of n,
   `cooling -> cold` after the concept's cooldown on the clock, `cooling -> hot` when the
   enter rule holds again. Only the cold -> hot edge issues a wake-up, which is what keeps
   a coffee cup on the desk from waking Gemini all afternoon. The k-of-n windows restart
   at every transition so a 1-of-n rule cannot flap between hot and cooling.
4. **Novelty** (1 - cosine to a running mean embedding, time constant `novelty_ema_s`
   in frame time) above `novelty_enter` on k of n frames is a wake-up on its own, so
   things the prompt bank never named still get looked at. It re-arms only once novelty
   has been below the threshold on k of n frames again.
5. **Armed conditions** (`arm`) are one-shot watches the labeler or a clerk sets: the
   first usable frame with that concept above its enter threshold wakes with reason
   ``watch_armed:<id>`` and the arming is gone. At most `max_armed`, oldest evicted.
6. **Wake-ups inside one tick coalesce** into one `Wakeup`; the first creates it, later
   ones add their concepts. `take_wakeup()` is consume-once. `take_tick()` names the
   wake-up in `woke` but does not consume it (the loop hands it to the labeler), and
   seals it so a wake-up never belongs to two ticks.
7. **A wake the labeler does not confirm doubles that concept's re-wake cooldown**
   (30 -> 60 -> 120 s, capped at 4x); a confirmed wake resets it.

Frame time (`frame_t`, from the caller) drives the novelty EMA; the clock only drives
cooldowns and arming expiry. Embeddings live in RAM and die with the process: they are
never in the tick, never on disk, never sent anywhere.

Thresholds and the point-concept set arrive as plain values, not a settings object:
this package must not import from ``backend`` (the dependency runs the other way).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
import math
import threading
import time
from typing import Callable, Mapping

import numpy as np

from .watcher_model import WatcherModel, bank_from_prompts, scores as concept_scores

log = logging.getLogger(__name__)

WATCH_BLOCK_VERSION = 1

#: The unconfirmed-wake backoff cap, as a multiple of `cooldown_s` (30 -> 60 -> 120 s).
MAX_COOLDOWN_MULTIPLIER = 4


@dataclass(frozen=True)
class WatcherConfig:
    """The watcher's knobs, mirroring `CaptureSettings.watch_*` names and defaults."""

    k: int = 2
    n: int = 3
    cooldown_s: float = 30.0
    novelty_enter: float = 0.35
    novelty_ema_s: float = 30.0
    min_sharpness: float = 20.0
    min_lux: float = 10.0
    max_armed: int = 8


@dataclass(frozen=True)
class Wakeup:
    """What the labeler is handed: which frame, which concepts, how novel, and why.

    `reason` is that of the first wake-up in the tick: "concept", "novelty" or
    "watch_armed:<id>". `frame_t` is the newest frame that contributed, since it is the
    one that shows every concept listed.
    """

    frame_t: float
    concepts: tuple[str, ...]
    novelty: float
    reason: str


@dataclass
class _Armed:
    concept: str
    deadline: float
    watch_id: str


@dataclass
class _Concept:
    """Per-concept hysteresis state. Windows are over usable frames only."""

    state: str = "cold"
    above: deque[bool] = field(default_factory=deque)
    below: deque[bool] = field(default_factory=deque)
    cooling_since: float = 0.0
    cooldown: float = 0.0

    def reset_windows(self) -> None:
        self.above.clear()
        self.below.clear()


# --- Newest-wins mailbox, thread flavour ---------------------------------------


class _Slot:
    """A one-item mailbox where a new frame overwrites an unread one (invariant 2).

    The threaded twin of `vlm._Slot`: the worker blocks in `get()`, the T0 loop
    never blocks in `put()`.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._item: tuple[float, bytes, Mapping[str, float] | None] | None = None
        self._open = True
        self.dropped = 0

    def put(self, item: tuple[float, bytes, Mapping[str, float] | None]) -> None:
        with self._cond:
            if self._item is not None:
                self.dropped += 1
            self._item = item
            self._cond.notify()

    def get(self) -> tuple[float, bytes, Mapping[str, float] | None] | None:
        """Block until a frame is waiting, or return None once the slot is closed."""
        with self._cond:
            while self._item is None and self._open:
                self._cond.wait()
            item, self._item = self._item, None
            return item

    def open(self) -> None:
        with self._cond:
            self._open = True

    def close(self) -> None:
        with self._cond:
            self._open = False
            self._item = None
            self._cond.notify_all()


# --- The watcher -------------------------------------------------------------------


class Watcher:
    """Scores every frame and says when a moment is notable. Every public method is
    non-blocking and thread-safe; only the worker (or `offer` under `run_inline`)
    ever touches the model."""

    def __init__(
        self,
        model: WatcherModel,
        config: WatcherConfig,
        thresholds: Mapping[str, tuple[float, float]],
        point_concepts: frozenset[str],
        *,
        clock: Callable[[], float] = time.monotonic,
        run_inline: bool = False,
    ) -> None:
        self._model = model
        self._config = config
        self._k = max(1, config.k)
        self._n = max(self._k, config.n)
        self._thresholds = {c: (float(e), float(x)) for c, (e, x) in thresholds.items()}
        self._point = frozenset(point_concepts)
        self._clock = clock
        self._inline = run_inline

        self._bank, self._groups, self._null = bank_from_prompts(model)

        self._lock = threading.Lock()
        self._slot = _Slot()
        self._thread: threading.Thread | None = None
        self._running = False

        self._concepts: dict[str, _Concept] = {}
        self._armed: list[_Armed] = []

        # Novelty: the running mean embedding and its own k-of-n re-arm rule.
        self._ema: np.ndarray | None = None
        self._ema_t = 0.0
        self._novelty_armed = True
        self._nov_above: deque[bool] = deque(maxlen=self._n)
        self._nov_below: deque[bool] = deque(maxlen=self._n)

        # The pending wake-up. `sealed` means a tick already named it, so the next
        # wake-up starts a new one instead of coalescing into this one.
        self._pending: Wakeup | None = None
        self._pending_sealed = False
        self._tick_woke: str | None = None

        # Per-tick accumulators, reset by `take_tick`.
        self._tick_frames = 0
        self._tick_usable = 0
        self._tick_scores: dict[str, float] = {}
        self._tick_novelty = 0.0

        #: Called synchronously from the worker thread (or the caller's thread under
        #: `run_inline`) with the coalesced wake-up whenever one is issued. Never
        #: called under the watcher's lock, so a listener may call back in.
        self.on_wakeup: Callable[[Wakeup], None] | None = None

        # Counters.
        self.frames = 0
        self.usable = 0
        self.wakeups = 0
        self.wakeups_by_reason: dict[str, int] = {}
        self.armed_expired = 0
        self.errors = 0
        self._inference: deque[float] = deque(maxlen=200)

    # -- lifecycle --

    async def start(self) -> None:
        """Start the worker thread (a no-op under `run_inline`). Idempotent."""
        self.start_worker()

    def start_worker(self) -> None:
        if self._inline or self._thread is not None:
            return
        self._running = True
        self._slot.open()
        self._thread = threading.Thread(target=self._loop, name="watcher", daemon=True)
        self._thread.start()

    async def aclose(self) -> None:
        self.stop()

    def stop(self) -> None:
        """Stop the worker and wait briefly for it; safe to call twice."""
        self._running = False
        self._slot.close()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    # -- the T0 loop's entry points, all non-blocking --

    def offer(self, frame_t: float, jpeg: bytes, sensor: Mapping[str, float] | None = None) -> None:
        """Hand the watcher the newest frame. Returns immediately (unless `run_inline`).

        A frame that arrives while the model is busy replaces the one waiting; it is
        never queued, because by the time the model is free it describes a world that
        no longer exists.
        """
        if self._inline:
            self._process(frame_t, jpeg, sensor)
            return
        if self._running:
            self._slot.put((frame_t, jpeg, sensor))

    def take_tick(self, now: float) -> dict:
        """The aggregate `watch` block since the last take; resets the accumulators.

        `now` is the tick's frame time, kept for symmetry with `T0Tagger.take`; the
        watcher keeps its own clock for cooldowns. Does not consume the pending
        wake-up, only names it in `woke` and seals it to this tick.
        """
        with self._lock:
            self._expire_cooling()
            block = {
                "v": WATCH_BLOCK_VERSION,
                "model": self._model.name,
                "frames": self._tick_frames,
                "usable": self._tick_usable,
                "scores": dict(self._tick_scores),
                "novelty": float(self._tick_novelty),
                "hot": self._hot_sorted(),
                "woke": self._tick_woke,
            }
            self._tick_frames = 0
            self._tick_usable = 0
            self._tick_scores = {}
            self._tick_novelty = 0.0
            self._tick_woke = None
            if self._pending is not None:
                self._pending_sealed = True
            return block

    def take_wakeup(self) -> Wakeup | None:
        """The pending wake-up, exactly once."""
        with self._lock:
            wakeup, self._pending = self._pending, None
            self._pending_sealed = False
            return wakeup

    # -- armed conditions --

    def arm(self, concept: str, within_s: float, watch_id: str) -> None:
        """Watch for `concept` above its enter threshold for `within_s` on the clock.

        Re-arming an existing `watch_id` replaces it. Past `max_armed` the oldest
        arming is evicted.
        """
        with self._lock:
            self._armed = [a for a in self._armed if a.watch_id != watch_id]
            self._armed.append(_Armed(concept, self._clock() + within_s, watch_id))
            while len(self._armed) > max(0, self._config.max_armed):
                dropped = self._armed.pop(0)
                log.info("watcher: armed watch %s evicted (max_armed=%d)", dropped.watch_id, self._config.max_armed)

    def disarm(self, watch_id: str) -> None:
        with self._lock:
            self._armed = [a for a in self._armed if a.watch_id != watch_id]

    # -- state the labeler reads --

    def hot_concepts(self) -> frozenset[str]:
        """Concepts hot or cooling right now."""
        with self._lock:
            self._expire_cooling()
            return frozenset(self._hot_sorted())

    def cooling_concepts(self) -> frozenset[str]:
        """Concepts cooling right now: a subset of `hot_concepts()`.

        The loop reads this *before* `hot_concepts()`, so a concept that goes cold
        between the two reads shows up as cooling one tick longer rather than as hot.
        """
        with self._lock:
            self._expire_cooling()
            return frozenset(c for c, s in self._concepts.items() if s.state == "cooling")

    def is_point(self, concept: str) -> bool:
        """A moment (a sip, a pill), not a state: the labeler gives it no steady cadence."""
        return concept in self._point

    def mark_confirmed(self, concept: str) -> None:
        """The labeler confirmed the wake: the concept's re-wake cooldown resets."""
        with self._lock:
            self._concept(concept).cooldown = self._config.cooldown_s

    def mark_unconfirmed(self, concept: str) -> None:
        """A false wake: the concept's re-wake cooldown doubles, capped at 4x."""
        with self._lock:
            state = self._concept(concept)
            state.cooldown = min(state.cooldown * 2, MAX_COOLDOWN_MULTIPLIER * self._config.cooldown_s)

    def cooldown_of(self, concept: str) -> float:
        with self._lock:
            return self._concept(concept).cooldown

    # -- observability --

    def stats(self) -> dict:
        with self._lock:
            self._expire_cooling()
            return {
                "model": self._model.name,
                "frames": self.frames,
                "usable": self.usable,
                "dropped": self._slot.dropped,
                "wakeups": self.wakeups,
                "wakeups_by_reason": dict(self.wakeups_by_reason),
                "armed": len(self._armed),
                "armed_expired": self.armed_expired,
                "errors": self.errors,
                "inference_ms_p50": round(self._percentile(0.50), 2),
                "inference_ms_p99": round(self._percentile(0.99), 2),
                "hot": self._hot_sorted(),
                "pending_wakeup": self._pending is not None,
            }

    def _percentile(self, fraction: float) -> float:
        lat = sorted(self._inference)
        if not lat:
            return 0.0
        return lat[min(len(lat) - 1, int((len(lat) - 1) * fraction))] * 1000

    # -- internals --

    def _loop(self) -> None:
        while self._running:
            item = self._slot.get()
            if item is None:
                return
            try:
                self._process(*item)
            except Exception:  # noqa: BLE001 - a bad frame must not stop the watcher
                with self._lock:
                    self.errors += 1
                log.exception("watcher: frame failed")

    def _usable(self, sensor: Mapping[str, float] | None) -> bool:
        if sensor is None:
            return True
        sharpness = sensor.get("sharpness")
        lux = sensor.get("lux_proxy")
        if sharpness is not None and sharpness < self._config.min_sharpness:
            return False
        if lux is not None and lux < self._config.min_lux:
            return False
        return True

    def _process(self, frame_t: float, jpeg: bytes, sensor: Mapping[str, float] | None) -> None:
        if not self._usable(sensor):
            with self._lock:
                self.frames += 1
                self._tick_frames += 1
            return

        # The model runs outside the lock: `take_tick` must never wait on inference.
        started = time.perf_counter()
        embedding = np.asarray(self._model.embed(jpeg), dtype=np.float32)
        elapsed = time.perf_counter() - started
        frame_scores = concept_scores(embedding, self._bank, self._groups, self._null)

        with self._lock:
            self._inference.append(elapsed)
            self.frames += 1
            self.usable += 1
            self._tick_frames += 1
            self._tick_usable += 1
            for concept, score in frame_scores.items():
                if score > self._tick_scores.get(concept, -1.0):
                    self._tick_scores[concept] = score

            novelty = self._update_novelty(embedding, frame_t)
            self._tick_novelty = max(self._tick_novelty, novelty)

            self._expire_cooling()
            fired = False
            fired |= self._check_armed(frame_t, frame_scores, novelty)
            fired |= self._step_concepts(frame_t, frame_scores, novelty)
            fired |= self._step_novelty(frame_t, novelty)
            wakeup = self._pending if fired else None

        if wakeup is not None and self.on_wakeup is not None:
            try:
                self.on_wakeup(wakeup)
            except Exception:  # noqa: BLE001 - a listener must not kill the watcher
                log.exception("watcher: on_wakeup listener failed")

    def _update_novelty(self, embedding: np.ndarray, frame_t: float) -> float:
        """1 - cos(e_t, ema); the first usable frame seeds the mean and scores 0."""
        if self._ema is None:
            self._ema = embedding.copy()
            self._ema_t = frame_t
            return 0.0
        norm = float(np.linalg.norm(self._ema))
        cos = float(embedding @ self._ema) / norm if norm > 0 else 1.0
        novelty = max(0.0, min(2.0, 1.0 - cos))
        dt = max(0.0, frame_t - self._ema_t)
        tau = self._config.novelty_ema_s
        alpha = 1.0 if tau <= 0 else 1.0 - math.exp(-dt / tau)
        self._ema = (1.0 - alpha) * self._ema + alpha * embedding
        self._ema_t = frame_t
        return novelty

    def _concept(self, concept: str) -> _Concept:
        state = self._concepts.get(concept)
        if state is None:
            state = self._concepts[concept] = _Concept(
                above=deque(maxlen=self._n), below=deque(maxlen=self._n), cooldown=self._config.cooldown_s
            )
        return state

    def _expire_cooling(self) -> None:
        """cooling -> cold once the concept's cooldown has elapsed on the clock."""
        now = self._clock()
        for state in self._concepts.values():
            if state.state == "cooling" and now - state.cooling_since >= state.cooldown:
                state.state = "cold"
                state.reset_windows()

    def _check_armed(self, frame_t: float, frame_scores: Mapping[str, float], novelty: float) -> bool:
        if not self._armed:
            return False
        now = self._clock()
        live: list[_Armed] = []
        for armed in self._armed:
            if armed.deadline < now:
                self.armed_expired += 1
                continue
            live.append(armed)
        self._armed = live
        fired = False
        for armed in list(self._armed):
            enter = self._thresholds.get(armed.concept)
            if enter is None or frame_scores.get(armed.concept, 0.0) <= enter[0]:
                continue
            self._armed.remove(armed)
            self._wake(frame_t, (armed.concept,), novelty, f"watch_armed:{armed.watch_id}")
            fired = True
        return fired

    def _step_concepts(self, frame_t: float, frame_scores: Mapping[str, float], novelty: float) -> bool:
        fired = False
        for concept, (enter, exit_) in self._thresholds.items():
            score = frame_scores.get(concept)
            if score is None:
                continue
            state = self._concept(concept)
            state.above.append(score > enter)
            state.below.append(score < exit_)
            enter_rule = sum(state.above) >= self._k
            exit_rule = sum(state.below) >= self._k
            if state.state == "cold":
                if enter_rule:
                    state.state = "hot"
                    state.reset_windows()
                    self._wake(frame_t, (concept,), novelty, "concept")
                    fired = True
            elif state.state == "hot":
                if exit_rule:
                    state.state = "cooling"
                    state.cooling_since = self._clock()
                    state.reset_windows()
            elif state.state == "cooling":
                if enter_rule:
                    # Back to hot without a wake-up: the same moment, not a new one.
                    state.state = "hot"
                    state.reset_windows()
        return fired

    def _step_novelty(self, frame_t: float, novelty: float) -> bool:
        above = novelty > self._config.novelty_enter
        self._nov_above.append(above)
        self._nov_below.append(not above)
        if self._novelty_armed and sum(self._nov_above) >= self._k:
            self._novelty_armed = False
            self._nov_above.clear()
            self._nov_below.clear()
            self._wake(frame_t, (), novelty, "novelty")
            return True
        if not self._novelty_armed and sum(self._nov_below) >= self._k:
            self._novelty_armed = True
            self._nov_above.clear()
            self._nov_below.clear()
        return False

    def _wake(self, frame_t: float, concepts: tuple[str, ...], novelty: float, reason: str) -> None:
        """Issue a wake-up, coalescing into the tick's pending one. Called under the lock."""
        self.wakeups += 1
        family = reason.split(":", 1)[0]
        self.wakeups_by_reason[family] = self.wakeups_by_reason.get(family, 0) + 1
        pending = self._pending
        if pending is None or self._pending_sealed:
            if pending is not None:
                log.info("watcher: unconsumed wake-up %s superseded", pending.reason)
            self._pending = Wakeup(frame_t, concepts, novelty, reason)
            self._pending_sealed = False
        else:
            merged = pending.concepts + tuple(c for c in concepts if c not in pending.concepts)
            self._pending = Wakeup(frame_t, merged, max(pending.novelty, novelty), pending.reason)
        if self._tick_woke is None:
            self._tick_woke = concepts[0] if concepts else reason

    def _hot_sorted(self) -> list[str]:
        return sorted(c for c, s in self._concepts.items() if s.state in ("hot", "cooling"))


__all__ = ["Watcher", "WatcherConfig", "Wakeup", "WATCH_BLOCK_VERSION", "MAX_COOLDOWN_MULTIPLIER"]
