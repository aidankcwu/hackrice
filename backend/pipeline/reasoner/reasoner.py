"""T1 -- the reasoner (SPEC §4, §5.4, §6).

One LLM call per escalation, and exactly one at a time.
:meth:`Reasoner.try_escalate` is **synchronous and non-blocking**: it claims the
single T1 slot or it does not, writes a :class:`~pipeline.models.Decision`
either way, and returns. The trigger gate never awaits us.

Three invariants this module exists to hold:

1. **Drop, never queue** (SPEC §5.2/§5.4). An escalation arriving against a busy
   reasoner is dropped and logged as a decision row, never buffered.
2. **Copy the frames first.** The durable copy happens at admission, before any
   model call, because the ring buffer is 90 seconds wide and inference is not
   guaranteed to be fast (SPEC §2.5).
3. **Every escalation produces a decision row** -- silent ones, dropped ones,
   timed-out ones, and ones whose action handler threw (SPEC §6). A system that
   correctly says nothing 90% of the time looks broken on stage without them.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

from ..actions.handlers import ActionHandler
from ..actions.speech import SpeechLimiter
from ..config import Settings
from ..db import Database, day_key
from ..frames import FrameStore
from ..models import Decision, Escalation
from .client import ReasonerClient
from .envelope import build_envelope, local_time, select_frames
from .evidence import EvidenceStore
from .prompts import DEFAULT_PERSONA, NO_SEVEN_DAY
from .schema import normalize

log = logging.getLogger(__name__)

__all__ = ["Reasoner", "FRAMES_PER_ESCALATION"]

#: SPEC §4.3 -- "Four images is the right number; the fifth adds latency and
#: little information."
FRAMES_PER_ESCALATION = 4


class Reasoner:
    """The only component that reasons, and the only one that can act."""

    def __init__(
        self,
        db: Database,
        frame_store: FrameStore,
        client: ReasonerClient,
        speech: SpeechLimiter,
        settings: Settings,
        seven_day_summary: Callable[[], str] | None = None,
        persona: str | None = None,
        t1_deadline_s: float = 15.0,
    ) -> None:
        # Cadence-aware AI freshness for the envelope (SPEC §12.2, S9).
        try:
            from . import envelope as _envelope
            _envelope.AI_MAX_AGE_MS = settings.timings.ai_max_age_ms
        except Exception:  # pragma: no cover - settings without timings in tests
            pass
        self.db = db
        self.frame_store = frame_store
        self.client = client
        self.speech = speech
        self.settings = settings
        self.seven_day_summary = seven_day_summary
        self.persona = persona if persona is not None else DEFAULT_PERSONA
        self.t1_deadline_s = float(t1_deadline_s)

        self.evidence = EvidenceStore(db)
        self.handler = ActionHandler(db, speech, settings.timings)

        #: The single T1 slot. A plain flag under a non-blocking lock -- an
        #: awaited semaphore would queue, and queueing is the one thing §5.2
        #: forbids.
        self._slot = threading.Lock()
        self._busy = False

        #: Monotonic in-memory decision-id counter. IDs used to be allocated as
        #: ``COUNT(*)+1`` at call time, which meant a busy-drop arriving while
        #: an escalation was still awaiting its model call (and had therefore
        #: taken an id but not yet inserted its row) would compute the SAME
        #: id -- the drop's row would then be clobbered by the original run's
        #: ``INSERT OR REPLACE`` (SPEC §5.4 requires drops to be logged, not
        #: silently lost). Allocating from a counter that advances the instant
        #: an id is handed out, under this dedicated lock, makes every id
        #: unique regardless of insert timing.
        self._counter_lock = threading.Lock()
        self._next_seq = self._initial_decision_seq(db)

        self.escalations = 0
        self.completed = 0
        self.dropped_busy = 0
        self.dropped_timeout = 0
        self.dropped_error = 0
        self.spoke_count = 0
        self.last_latency_ms: int | None = None
        self.last_decision_t: float | None = None

    # -- admission --------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    def try_escalate(self, esc: Escalation) -> bool:
        """Claim the T1 slot for ``esc``. Synchronous, non-blocking.

        ``True``  -- claimed; frames are already copied and inference is
        scheduled on the running loop.
        ``False`` -- dropped; a decision row with ``dropped=True`` has already
        been written and the gate should simply move on.
        """

        self.escalations += 1

        if not self._slot.acquire(blocking=False):
            self._drop(esc, "t1_busy")
            self.dropped_busy += 1
            return False

        claimed = False
        try:
            self._busy = True
            decision_id = self._next_decision_id()

            # Copy the evidence NOW: the ring buffer is 90 s wide and the model
            # call has no such guarantee (SPEC §2.5).
            selected = select_frames(esc.window, esc.tick, k=FRAMES_PER_ESCALATION)
            frames = self.evidence.copy(
                decision_id,
                [(tick.frame_ref, tick.t) for tick in selected],
                self.frame_store,
            )

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.error("try_escalate called with no running event loop")
                self._drop(esc, "t1_no_loop", decision_id=decision_id)
                return False

            loop.create_task(self._run(esc, frames, decision_id))
            claimed = True
            return True
        finally:
            if not claimed:
                self._busy = False
                self._slot.release()

    @staticmethod
    def _initial_decision_seq(db: Database) -> int:
        """The first id number to hand out, from existing rows at construction.

        Prefers the max numeric suffix already in use (``d_0042`` -> 42) so a
        reasoner restarted against a non-empty database keeps allocating
        strictly-increasing ids; falls back to the row count if no id parses.
        """

        with db._lock:
            ids = [r[0] for r in db.conn.execute("SELECT id FROM decisions").fetchall()]
        max_suffix = 0
        for raw in ids:
            try:
                max_suffix = max(max_suffix, int(str(raw).rsplit("_", 1)[-1]))
            except (ValueError, IndexError):
                continue
        return (max_suffix + 1) if max_suffix else (len(ids) + 1)

    def _next_decision_id(self) -> str:
        """Allocate the next decision id from the in-memory counter.

        Advances the counter under ``_counter_lock`` before returning, so two
        concurrent callers (a claimed escalation and a contended drop) always
        get distinct ids even though the claimed one won't INSERT its row
        until its model call returns.
        """

        with self._counter_lock:
            n = self._next_seq
            self._next_seq += 1
            return f"d_{n:04d}"

    def _drop(
        self, esc: Escalation, reason: str, decision_id: str | None = None
    ) -> Decision:
        """Write the decision row for an escalation that never ran (SPEC §6)."""

        decision = Decision(
            id=decision_id or self._next_decision_id(),
            t=esc.t,
            trigger=esc.trigger,
            trigger_tick_id=esc.tick.tick_id,
            episode_id=esc.episode_id,
            interpretation="",
            confidence=0.0,
            actions=[],
            spoke=False,
            dropped=True,
            drop_reason=reason,
            model=getattr(self.client, "model", ""),
        )
        self.db.insert_decision(decision)
        log.info(
            "%s · %s · dropped (%s)",
            local_time(esc.t, "%H:%M"),
            esc.trigger,
            reason,
        )
        return decision

    # -- inference --------------------------------------------------------

    async def _run(
        self, esc: Escalation, frames: dict[str, bytes], decision_id: str
    ) -> None:
        started = time.perf_counter()
        self.last_decision_t = esc.t
        self.last_latency_ms = None
        try:
            messages = self._envelope(esc, frames)

            try:
                resp, meta = await asyncio.wait_for(
                    self.client.complete(messages), timeout=self.t1_deadline_s
                )
            except (asyncio.TimeoutError, TimeoutError):
                self.dropped_timeout += 1
                self._drop_after(esc, decision_id, "t1_timeout", started)
                return
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                raise
            except Exception as exc:
                self.dropped_error += 1
                log.exception("T1 call failed for %s", decision_id)
                self._drop_after(
                    esc, decision_id, f"t1_error:{type(exc).__name__}", started
                )
                return

            norm = normalize(resp, t=esc.t)
            # A watch-triggered decision may not schedule another watch: the
            # model otherwise re-arms itself every cooldown forever (seen live:
            # eight chained "track the caffeine pattern" escalations).
            if esc.trigger.startswith("watch:"):
                kept = [a for a in norm.actions if getattr(a, "type", None) != "watch"]
                if len(kept) != len(norm.actions):
                    log.info("watch chain capped for %s", esc.trigger)
                    norm.actions = kept
            latency_ms = meta.get("latency_ms")
            if latency_ms is None:
                latency_ms = int((time.perf_counter() - started) * 1000)
            self.last_latency_ms = int(latency_ms)
            self.last_decision_t = esc.t

            decision = Decision(
                id=decision_id,
                t=esc.t,
                trigger=esc.trigger,
                trigger_tick_id=esc.tick.tick_id,
                episode_id=esc.episode_id,
                interpretation=norm.interpretation,
                confidence=norm.confidence,
                actions=[a.model_dump() for a in norm.actions],
                spoke=False,
                dropped=False,
                latency_ms=int(latency_ms),
                model=str(meta.get("model") or ""),
            )
            # Written before the actions apply: a handler that throws must not
            # cost us the decision row (SPEC §6).
            self.db.insert_decision(decision)

            outcome = self.handler.apply(decision_id, esc.t, norm)

            if outcome.get("spoke"):
                decision.spoke = True
                self.spoke_count += 1
                self.db.insert_decision(decision)

            self.completed += 1
            log.info(self.feed_line(decision))
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("T1 run failed for %s", decision_id)
        finally:
            if self.last_latency_ms is None:
                self.last_latency_ms = int((time.perf_counter() - started) * 1000)
            self._busy = False
            try:
                self._slot.release()
            except RuntimeError:  # pragma: no cover - defensive
                pass

    def _drop_after(
        self, esc: Escalation, decision_id: str, reason: str, started: float
    ) -> None:
        decision = self._drop(esc, reason, decision_id=decision_id)
        decision.latency_ms = int((time.perf_counter() - started) * 1000)
        self.db.insert_decision(decision)

    # -- envelope ---------------------------------------------------------

    def _envelope(
        self, esc: Escalation, frames: dict[str, bytes]
    ) -> list[dict[str, Any]]:
        try:
            # Keyed off the escalation's own clock, not wall clock: one clock
            # everywhere means a replayed or sped-up day still reads its own
            # summary back (docs/API.md, "One clock").
            today = self.db.today_summary_lines(day=day_key(esc.t))
        except Exception:  # pragma: no cover - defensive
            log.exception("could not read today's summary; sending an empty one")
            today = []

        seven_day = NO_SEVEN_DAY
        if self.seven_day_summary is not None:
            try:
                seven_day = self.seven_day_summary() or NO_SEVEN_DAY
            except Exception:
                log.exception("7-day summary callable raised; using the placeholder")

        return build_envelope(
            esc,
            frames,
            today,
            seven_day,
            self.persona,
            k=FRAMES_PER_ESCALATION,
        )

    # -- reporting --------------------------------------------------------

    @staticmethod
    def feed_line(decision: Decision) -> str:
        """docs/API.md feed format.

        ``12:31 · food_in_frame · mixed lunch w/ people · annotate, log_insight · silent``
        """

        kinds: list[str] = []
        for action in decision.actions:
            kind = str(action.get("type", "?"))
            if kind not in kinds:
                kinds.append(kind)
        return " · ".join(
            [
                local_time(decision.t, "%H:%M"),
                decision.trigger,
                decision.interpretation or "(no interpretation)",
                ", ".join(kinds) or "none",
                "spoke" if decision.spoke else "silent",
            ]
        )

    def stats(self) -> dict[str, Any]:
        return {
            "busy": self._busy,
            "escalations": self.escalations,
            "completed": self.completed,
            "dropped_busy": self.dropped_busy,
            "dropped_timeout": self.dropped_timeout,
            "dropped_error": self.dropped_error,
            "spoke": self.spoke_count,
            "frames_copied": self.evidence.copied,
            "frames_missing": self.evidence.missing,
            "model": getattr(self.client, "model", ""),
            "deadline_s": self.t1_deadline_s,
            "last_latency_ms": self.last_latency_ms,
            "last_decision_t": self.last_decision_t,
            **{f"speech_{k}": v for k, v in self.speech.stats().items()},
        }
