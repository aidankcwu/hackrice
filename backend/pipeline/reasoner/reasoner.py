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
from collections import Counter
from typing import Any, Callable

from ..actions.handlers import LOOK_CHAINED, ActionHandler
from ..actions.speech import SpeechLimiter
from ..config import Settings
from ..db import Database, day_key
from ..frames import FrameStore
from ..models import Decision, Escalation, PendingQuestion
from .client import AnswerParser, ReasonerClient
from .decider import Decider, Verdict, build_state
from .decider_settings import DeciderSettings
from .envelope import CLERK_FRAMES, build_envelope, local_time, select_frames, RECENT_QUESTIONS
from .session_context import constant_block, said_block
from .evidence import EvidenceStore
from .prompts import DEFAULT_PERSONA, LEARNED_MAX, NO_SEVEN_DAY
from .schema import (
    ActAction,
    AnnotateAction,
    AskAction,
    LogInsightAction,
    LookAction,
    RememberAction,
    SpeakAction,
    T1Response,
    WatchAction,
    normalize,
)
from .writers import Writers, _fallback_summary

log = logging.getLogger(__name__)

__all__ = ["Reasoner", "FRAMES_PER_ESCALATION", "FAST_PATH_NOTE", "NO_AGENT",
           "LOOK_NOTE", "LOOK_SOUND", "settled_fact"]

#: Frames copied as evidence and sent to the clerk per escalation. SPEC §4.3
#: said four; the envelope now sends one low-detail change frame and the sharp
#: trigger frame (``envelope.CLERK_FRAMES``), and the admission copy must match
#: it exactly -- the envelope re-runs the same selection over the copied refs.
FRAMES_PER_ESCALATION = CLERK_FRAMES

#: Longest one clerk call may hold the single T1 slot. 9 s, not 15: the client
#: gives up on a call after 8 s (``reasoner.client``), so past that the slot is
#: only blocking the next wake-up -- including the silent half of a cue.
T1_DEADLINE_S = 9.0

#: ``fast_path`` outcome when no voice agent is wired: the gate then sends the
#: cue down the ordinary clerk path (mirrored as ``gate.NO_AGENT``).
NO_AGENT = "no_agent"

#: The line the clerk gets on a fast-pathed escalation, after the tick table.
#: Its speak/ask would be dropped anyway (``fast_pathed``); saying so up front
#: keeps it from spending output tokens on a hand-off nobody will read.
FAST_PATH_NOTE = (
    "Already handed to the voice agent, which is saying it to the wearer now: "
    "{topic}. Do not speak or ask about this moment; any speak or ask is dropped. "
    "Annotate it, and log_insight or remember only if it earns one."
)


#: What the clerk reads on a re-run after a ``look``: the question it asked of
#: the frame and the labeler's answer, after the tick table.
LOOK_NOTE = "You looked closer at the frame. Question: {question} Answer: {answer}"

#: The sound a decider-fired ``act`` plays. The decider says only that a cue
#: is worth it; the softest one is the right default for a non-verbal nudge.
LOOK_SOUND = "soft"

#: Decisions whose state is kept for a possible re-run after a ``look``.
LOOK_CONTEXT_MAX = 8


def _number(value: float) -> str:
    """``2.0`` -> ``2``; ``1.5`` stays ``1.5``. Nobody drank 2.0 beers."""

    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def settled_fact(parse: Any) -> str:
    """What an answer established, in the few words an episode label can hold.

    ``"confirmed, 2 beer"`` / ``"not theirs"`` / ``""``. Built from the parsed
    fields, never from the raw transcript: the transcript is whatever the
    microphone heard, and the parse is the part of it the system was willing to
    believe. Empty when the answer settled nothing worth naming -- an
    un-understood reply must not append noise to the label.
    """

    bits: list[str] = []
    confirmed = getattr(parse, "confirmed", None)
    if confirmed is True:
        bits.append("confirmed")
    elif confirmed is False:
        bits.append("not theirs")

    count = getattr(parse, "count", None)
    food = (getattr(parse, "food_type", None) or "").strip()
    if count is not None:
        bits.append(f"{_number(count)} {food}" if food else _number(count))
    elif food:
        bits.append(food)

    if not bits:
        note = (getattr(parse, "note", "") or "").strip()
        if note and getattr(parse, "understood", False):
            bits.append(note)
    return ", ".join(bits)


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
        t1_deadline_s: float = T1_DEADLINE_S,
        parser: AnswerParser | None = None,
        questions: Any | None = None,
        conversation: Any | None = None,
        *,
        decider: Decider | None = None,
        writers: Writers | None = None,
        decider_settings: DeciderSettings | None = None,
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
        self.parser = parser
        self._questions = questions

        self._conversation = conversation

        #: The decider (docs/PERCEPTION.md, "Decider and writers"). ``None``
        #: is the clerk path exactly as before; with one, every escalation
        #: asks it first and the clerk runs only as the fallback.
        self.decider = decider
        self.writers = writers
        self.decider_settings = (
            decider_settings if decider_settings is not None
            else (DeciderSettings() if decider is not None else None)
        )

        self.evidence = EvidenceStore(db)
        #: Called as ``(escalation, decision_id, frames)`` right after an
        #: escalation's frames are copied, so a consumer can point at them
        #: (``<decision_id>/<frame_ref>``) -- the protocol's adherence matcher
        #: (PLAN 2.2). Synchronous; an exception is logged, never raised.
        self.on_evidence: Callable[[Escalation, str, dict[str, bytes]], None] | None = None
        self.handler = ActionHandler(
            db, speech, settings.timings, questions, conversation,
            sound_max_per_hour=(
                self.decider_settings.act_sound_max_per_hour
                if self.decider_settings is not None
                else DeciderSettings.model_fields["act_sound_max_per_hour"].default
            ),
        )
        # The handler delivers a look's answer back here (``rerun_after_look``).
        self.handler.reasoner = self
        #: What a decision was decided on, kept for one re-run after its
        #: ``look`` answers: decision id -> (escalation, frames, decider state
        #: or None on the clerk path). Popped by the re-run, so the re-run's
        #: own decision is never in here: a look is never chained.
        self._look_context: dict[str, tuple[Escalation, dict[str, bytes], dict | None]] = {}
        #: Re-runs after a look, and ones dropped on a busy slot.
        self.look_reruns = 0
        self.look_reruns_dropped = 0

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
        self.answers_completed = 0
        self.answers_dropped = 0
        self.remembered = 0
        self.last_latency_ms: int | None = None
        self.last_decision_t: float | None = None
        #: Bumped by a judge-session start. A call admitted under an older epoch
        #: still writes its decision row (SPEC §6) but applies no actions: the
        #: previous wearer's coffee must not speak into, or schedule a watch
        #: for, the next wearer's window.
        self.epoch = 0
        self.skipped_stale = 0
        #: Persona cues the gate handed straight to the voice agent.
        self.fast_pathed = 0
        #: Escalations the decider settled (no clerk call).
        self.decided_by_decider = 0
        #: Escalations handed to the clerk after the decider ran, by reason
        #: (``error``, ``uncertain:speak`` ...).
        self.fell_back: Counter[str] = Counter()

    # -- admission --------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def questions(self) -> Any | None:
        return self._questions

    @questions.setter
    def questions(self, value: Any | None) -> None:
        self._questions = value
        self.handler.questions = value

    @property
    def conversation(self) -> Any | None:
        return self._conversation

    @conversation.setter
    def conversation(self, value: Any | None) -> None:
        self._conversation = value
        self.handler.conversation = value

    def try_escalate(self, esc: Escalation) -> bool:
        """Claim the T1 slot for ``esc``. Synchronous, non-blocking.

        ``True``  -- claimed; frames are already copied and inference is
        scheduled on the running loop.
        ``False`` -- dropped; a decision row with ``dropped=True`` has already
        been written and the gate should simply move on.
        """

        return self._admit(esc)

    def fast_path(self, esc: Escalation) -> str:
        """Hand a persona cue straight to the voice agent, then wake the clerk.

        The clerk used to sit on the spoken path: every cue paid its full call
        (p50 2.2 s, p90 3.0 s) before the voice agent even started, and the
        clerk's only contribution to the words was a topic string that T0's
        caption already supplied. Now the gate's cue goes to the agent here,
        synchronously, and the clerk runs beside it for what only it does --
        the memory line, the episode label, insights -- with its own speak/ask
        dropped as ``fast_pathed`` (``handed_off`` on the escalation).

        Returns the agent's outcome. Anything but ``handed_off:<id>`` means
        nothing happened here -- no decision row, no clerk call -- and the gate
        decides what next: a busy mouth is retried on the next fresh tick, no
        phone sends the cue down the clerk path, a repeat spends the moment.
        The decision id is taken *before* the hand-off so the conversation row
        points at the decision that records the moment, and given back if the
        agent refused.
        """

        conversation = self._conversation
        if conversation is None:
            return NO_AGENT
        decision_id = self._next_decision_id()
        try:
            outcome = conversation.request(
                esc.cue_topic or esc.reason, esc.cue_mode,
                decision_id=decision_id, episode_id=esc.episode_id, esc=esc,
                reason=esc.reason,
            )
        except Exception:  # pragma: no cover - request() never raises by contract
            log.exception("fast-path hand-off failed")
            outcome = "no_transport"
        if not outcome.startswith("handed_off:"):
            self._return_decision_id(decision_id)
            return outcome
        esc.handed_off = outcome.split(":", 1)[1]
        esc.extra_text = [*esc.extra_text,
                          FAST_PATH_NOTE.format(topic=esc.cue_topic or esc.reason)]
        self.fast_pathed += 1
        log.info("%s · %s · fast path -> %s", local_time(esc.t, "%H:%M:%S"),
                 esc.trigger, esc.handed_off)
        # The words are already on their way; this only decides whether the
        # clerk gets to write the moment down (a busy slot drops it, SPEC §5.4).
        self._admit(esc, decision_id)
        return outcome

    def _admit(self, esc: Escalation, decision_id: str | None = None) -> bool:
        self.escalations += 1

        if not self._slot.acquire(blocking=False):
            self._drop(esc, "t1_busy", decision_id=decision_id)
            self.dropped_busy += 1
            return False

        claimed = False
        try:
            self._busy = True
            decision_id = decision_id or self._next_decision_id()

            # Copy the evidence NOW: the ring buffer is 90 s wide and the model
            # call has no such guarantee (SPEC §2.5).
            selected = select_frames(esc.window, esc.tick, k=FRAMES_PER_ESCALATION)
            frames = self.evidence.copy(
                decision_id,
                [(tick.frame_ref, tick.t) for tick in selected],
                self.frame_store,
            )
            if self.on_evidence is not None:
                try:
                    self.on_evidence(esc, decision_id, frames)
                except Exception:  # an observer must never cost the escalation
                    log.exception("evidence observer failed for %s", decision_id)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.error("try_escalate called with no running event loop")
                self._drop(esc, "t1_no_loop", decision_id=decision_id)
                return False

            loop.create_task(self._run(esc, frames, decision_id, self.epoch))
            claimed = True
            return True
        finally:
            if not claimed:
                self._busy = False
                self._slot.release()

    #: How long an answer parse may wait for the T1 slot before it is given up.
    ANSWER_WAIT_S = 20.0

    def try_answer(self, question: PendingQuestion, transcript: str, t: float) -> bool:
        """Schedule an answer parse, waiting (bounded) for the shared T1 slot.

        Escalations never queue (SPEC §3), but an answer is the wearer's reply
        to a question the system chose to ask: dropping it because a wake-up
        happened to be in flight wastes the whole exchange (seen live: "just
        the water" finalised as "reasoner busy"). So when the slot is taken the
        parse waits for it, up to ``ANSWER_WAIT_S``, and is finalised as a
        failure only if the slot never frees.
        """

        if self._slot.acquire(blocking=False):
            return self._start_answer(question, transcript, t)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.error("try_answer called with no running event loop")
            return False
        loop.create_task(self._answer_when_free(question, transcript, t))
        return True

    async def _answer_when_free(
        self, question: PendingQuestion, transcript: str, t: float
    ) -> None:
        deadline = time.monotonic() + self.ANSWER_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if self._slot.acquire(blocking=False):
                if self._start_answer(question, transcript, t):
                    return
                break
        manager = self.questions
        if manager is not None:
            manager.finalize_failure(question, "reasoner busy", time.time(), "t1_busy")

    def _start_answer(self, question: PendingQuestion, transcript: str, t: float) -> bool:
        """The slot is held by the caller; start the parse or release it."""

        claimed = False
        try:
            self._busy = True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.error("try_answer called with no running event loop")
                return False
            started = [False]
            task = loop.create_task(self._answer_run(
                question, transcript, t, started=started
            ))
            task.add_done_callback(
                lambda done: self._release_cancelled_before_start(done, started)
            )
            claimed = True
            return True
        finally:
            if not claimed:
                self._busy = False
                self._slot.release()

    async def _answer_run(
        self, question: PendingQuestion, transcript: str, t: float,
        *, started: list[bool] | None = None,
    ) -> None:
        if started is not None:
            started[0] = True
        try:
            if self.parser is None or self.questions is None:
                raise RuntimeError("answer parser is not configured")
            episode = next((e for e in self.db.list_episodes()
                            if e.id == question.episode_id), None)
            try:
                parsed = await asyncio.wait_for(
                    self.parser.parse(question, transcript, episode),
                    timeout=self.t1_deadline_s,
                )
            except (asyncio.TimeoutError, TimeoutError):
                self.answers_dropped += 1
                self.questions.finalize_failure(
                    question, "parse failed: TimeoutError", t, "t1_timeout"
                )
                return
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception as exc:
                self.answers_dropped += 1
                kind = type(exc).__name__
                log.exception("answer parse failed: %s", question.id)
                self.questions.finalize_failure(
                    question, f"parse failed: {kind}", t, f"t1_error:{kind}"
                )
                return
            self.questions.apply_parse(question, parsed, t)
            self._extend_episode_label(question.episode_id, parsed)
            self.answers_completed += 1
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:
            self.answers_dropped += 1
            kind = type(exc).__name__
            log.exception("answer handling failed: %s", question.id)
            if self.questions is not None:
                self.questions.finalize_failure(
                    question, f"parse failed: {kind}", t, f"t1_error:{kind}"
                )
            else:
                log.error("cannot finalise answer %s: questions not configured",
                          question.id)
        finally:
            self._busy = False
            try:
                self._slot.release()
            except RuntimeError:  # pragma: no cover
                pass

    def _release_cancelled_before_start(
        self, task: asyncio.Task[Any], started: list[bool]
    ) -> None:
        if task.cancelled() and not started[0] and self._busy:
            self._busy = False
            try:
                self._slot.release()
            except RuntimeError:  # pragma: no cover - defensive
                pass

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

    def _return_decision_id(self, decision_id: str) -> None:
        """Give back an id nothing was written under, if it was the last one.

        A fast-path cue refused by a busy voice agent retries every tick; burning
        an id each time would leave the decisions feed full of holes.
        """

        with self._counter_lock:
            if decision_id == f"d_{self._next_seq - 1:04d}":
                self._next_seq -= 1

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

    def bump_epoch(self) -> int:
        """Invalidate in-flight work from the previous wearer."""
        self.epoch += 1
        return self.epoch

    async def _run(
        self, esc: Escalation, frames: dict[str, bytes], decision_id: str,
        epoch: int | None = None,
    ) -> None:
        started = time.perf_counter()
        self.last_decision_t = esc.t
        self.last_latency_ms = None
        try:
            decided = await self._decide(esc, frames, decision_id, started)
            if decided is None:
                return
            resp, meta, path, writers_ran = decided
            self._finish(esc, decision_id, resp, meta, path, writers_ran, started,
                         epoch)
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

    def _finish(
        self, esc: Escalation, decision_id: str, resp: T1Response,
        meta: dict[str, Any], path: str, writers_ran: list[str], started: float,
        epoch: int | None, *, after_look: bool = False,
    ) -> Decision:
        """Normalise, write the decision row, apply the actions. Shared by a
        fresh run and the re-run after a ``look``; on the re-run any ``look``
        is dropped as ``look_chained`` before it can defer anything."""

        chained: list[dict[str, Any]] = []
        if after_look:
            kept = [a for a in resp.actions if getattr(a, "type", None) != "look"]
            chained = [dict(a.model_dump(), outcome=LOOK_CHAINED)
                       for a in resp.actions if getattr(a, "type", None) == "look"]
            if chained:
                log.info("look chain capped for %s", decision_id)
                resp.actions = kept

        norm = normalize(resp, t=esc.t)
        if norm.deferred_for_look:
            log.info("%s: speak/ask deferred until the look answers", decision_id)
        # A watch-triggered decision may not schedule another watch: the
        # model otherwise re-arms itself every cooldown forever (seen live:
        # eight chained "track the caffeine pattern" escalations).
        if esc.trigger.startswith("watch:"):
            kept = [a for a in norm.actions if getattr(a, "type", None) != "watch"]
            if len(kept) != len(norm.actions):
                log.info("watch chain capped for %s", esc.trigger)
                norm.actions = kept
        if esc.trigger.startswith("answer:"):
            kept = [a for a in norm.actions if getattr(a, "type", None) != "ask"]
            if len(kept) != len(norm.actions):
                log.info("ask chain capped for %s", esc.trigger)
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
            actions=[a.model_dump() for a in norm.actions] + chained,
            spoke=False,
            dropped=False,
            latency_ms=int(latency_ms),
            model=str(meta.get("model") or ""),
            path=path,
            writers=writers_ran,
        )
        # Written before the actions apply: a handler that throws must not
        # cost us the decision row (SPEC §6).
        self.db.insert_decision(decision)

        if epoch is not None and epoch != self.epoch:
            self.skipped_stale += 1
            log.info("%s: session changed while reasoning; actions skipped",
                     decision_id)
            self.completed += 1
            return decision

        outcome = self.handler.apply(
            decision_id, esc.t, norm, episode_id=esc.episode_id, esc=esc
        )

        # `speak` and `ask` both record what became of them on their own
        # action row (docs/CONVERSATION_DESIGN.md §7): `handed_off:<id>`,
        # `conversation_active`, `conversation_cooldown`, `no_transport`,
        # or -- with no voice agent wired -- the older `sent` /
        # `suppressed:<guard>` shape. `decision.actions` was built from
        # `norm.actions` in order, so the handler's index is this index.
        outcomes = outcome.get("outcomes") or {}
        for index, patch in outcomes.items():
            if 0 <= index < len(decision.actions):
                decision.actions[index].update(patch)

        if outcome.get("spoke") or esc.handed_off:
            # A fast-pathed moment spoke through the voice agent before the
            # clerk even started; the decision that records it says so.
            decision.spoke = True
            self.spoke_count += 1

        if outcomes or decision.spoke:
            self.db.insert_decision(decision)

        self._remember(decision_id, esc.t, norm)
        self._label_episode(esc.episode_id, norm)

        self.completed += 1
        log.info(self.feed_line(decision))
        return decision

    # -- the re-run after a look -------------------------------------------

    def rerun_after_look(self, decision_id: str, question: str, answer: str) -> bool:
        """Decide ``decision_id``'s moment again with the look's answer in hand.

        Synchronous and non-blocking, like ``try_escalate``: the T1 slot is
        claimed or the re-run is dropped (a busy slot, or a decision this
        reasoner did not decide). The re-run reuses the state the decision was
        made on, with ``state["look"] = {question, answer}`` for the decider or
        a ``LOOK_NOTE`` line for the clerk, and writes a new decision row with
        path ``<path>:look``. A ``look`` fired by the re-run is dropped as
        ``look_chained``: never chained.
        """

        context = self._look_context.pop(decision_id, None)
        if context is None:
            log.info("no context to re-run decision %s after its look", decision_id)
            self.look_reruns_dropped += 1
            return False
        if not self._slot.acquire(blocking=False):
            log.info("look re-run of %s dropped: T1 busy", decision_id)
            self.look_reruns_dropped += 1
            return False
        claimed = False
        try:
            self._busy = True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.error("rerun_after_look called with no running event loop")
                self.look_reruns_dropped += 1
                return False
            esc, frames, state = context
            loop.create_task(self._rerun_look(esc, frames, state, decision_id,
                                              question, answer, self.epoch))
            claimed = True
            return True
        finally:
            if not claimed:
                self._busy = False
                self._slot.release()

    async def _rerun_look(
        self, esc: Escalation, frames: dict[str, bytes], state: dict | None,
        origin_id: str, question: str, answer: str, epoch: int,
    ) -> None:
        started = time.perf_counter()
        decision_id = self._next_decision_id()
        self.last_latency_ms = None
        try:
            esc = esc.model_copy(update={
                "extra_text": [*esc.extra_text,
                               LOOK_NOTE.format(question=question, answer=answer)],
            })
            decided = None
            if state is not None and self.decider is not None:
                settings = self.decider_settings or DeciderSettings()
                looked = dict(state, look={"question": question, "answer": answer})
                try:
                    verdict = await self.decider.decide(looked)
                except asyncio.CancelledError:  # pragma: no cover - shutdown path
                    raise
                except Exception as exc:
                    log.warning("decider failed on the look re-run of %s (%s: %s); "
                                "falling back to the clerk", origin_id,
                                type(exc).__name__, exc)
                    self.fell_back["error"] += 1
                    verdict = None
                if verdict is not None:
                    unsure = verdict.uncertain(settings.decide_uncertain_low,
                                               settings.decide_uncertain_high)
                    if unsure is not None:
                        self.fell_back[f"uncertain:{unsure}"] += 1
                        decided = await self._clerk(
                            esc, frames, decision_id, started,
                            path=f"clerk_fallback:uncertain:{unsure}:look")
                    else:
                        writing = time.perf_counter()
                        resp, ran = await self._write(looked, verdict, settings)
                        self.decided_by_decider += 1
                        meta = {"model": verdict.model,
                                "latency_ms": int(verdict.latency_ms
                                                  + (time.perf_counter() - writing) * 1000)}
                        decided = (resp, meta, "decider:look", ran)
                else:
                    decided = await self._clerk(esc, frames, decision_id, started,
                                                path="clerk_fallback:error:look")
            else:
                decided = await self._clerk(esc, frames, decision_id, started,
                                            path="clerk:look")
            if decided is None:
                return
            resp, meta, path, writers_ran = decided
            self.look_reruns += 1
            self._finish(esc, decision_id, resp, meta, path, writers_ran, started,
                         epoch, after_look=True)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("look re-run failed for %s", origin_id)
        finally:
            if self.last_latency_ms is None:
                self.last_latency_ms = int((time.perf_counter() - started) * 1000)
            self._busy = False
            try:
                self._slot.release()
            except RuntimeError:  # pragma: no cover - defensive
                pass

    def _keep_look_context(
        self, decision_id: str, esc: Escalation, frames: dict[str, bytes],
        state: dict | None,
    ) -> None:
        self._look_context[decision_id] = (esc, frames, state)
        while len(self._look_context) > LOOK_CONTEXT_MAX:
            self._look_context.pop(next(iter(self._look_context)))

    def _drop_after(
        self, esc: Escalation, decision_id: str, reason: str, started: float
    ) -> None:
        decision = self._drop(esc, reason, decision_id=decision_id)
        decision.latency_ms = int((time.perf_counter() - started) * 1000)
        self.db.insert_decision(decision)

    # -- who decides ------------------------------------------------------
    #
    # The evidence copy already happened at admission, so nothing here needs
    # the envelope until the clerk is actually called. The decider runs on the
    # cheap text state first; the envelope (base64 frames, tick table) is
    # built only on the clerk path -- the default, or a fallback.

    async def _decide(
        self, esc: Escalation, frames: dict[str, bytes], decision_id: str,
        started: float,
    ) -> tuple[T1Response, dict[str, Any], str, list[str]] | None:
        """The response, its meta, the path that decided and the writers that
        ran; ``None`` when the escalation was dropped (its row is written)."""

        if self.decider is None:
            self._keep_look_context(decision_id, esc, frames, None)
            return await self._clerk(esc, frames, decision_id, started, path="clerk")

        today, seven_day = self._context(esc)
        state = build_state(
            esc, esc.window, [line.line for line in today], self._open_episodes(esc),
            self.current_persona(), seven_day, esc.t,
        )
        self._keep_look_context(decision_id, esc, frames, state)
        try:
            verdict = await self.decider.decide(state)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:  # DeciderError, or anything else: the clerk is never deleted
            log.warning("decider failed for %s (%s: %s); falling back to the clerk",
                        decision_id, type(exc).__name__, exc)
            self.fell_back["error"] += 1
            return await self._clerk(esc, frames, decision_id, started,
                                     path="clerk_fallback:error",
                                     today=today, seven_day=seven_day)

        settings = self.decider_settings or DeciderSettings()
        unsure = verdict.uncertain(settings.decide_uncertain_low,
                                   settings.decide_uncertain_high)
        if unsure is not None:
            log.info("%s: decider unsure about %s (%.2f); clerk decides", decision_id,
                     unsure, verdict.probabilities.get(unsure, 0.0))
            self.fell_back[f"uncertain:{unsure}"] += 1
            return await self._clerk(esc, frames, decision_id, started,
                                     path=f"clerk_fallback:uncertain:{unsure}",
                                     today=today, seven_day=seven_day)

        writing = time.perf_counter()
        resp, writers_ran = await self._write(state, verdict, settings)
        self.decided_by_decider += 1
        meta = {
            "model": verdict.model,
            "latency_ms": int(verdict.latency_ms + (time.perf_counter() - writing) * 1000),
        }
        return resp, meta, "decider", writers_ran

    async def _clerk(
        self, esc: Escalation, frames: dict[str, bytes], decision_id: str,
        started: float, *, path: str,
        today: list[Any] | None = None, seven_day: str | None = None,
    ) -> tuple[T1Response, dict[str, Any], str, list[str]] | None:
        """The one slow clerk call, bounded by the T1 deadline."""

        messages = self._envelope(esc, frames, today=today, seven_day=seven_day)
        try:
            resp, meta = await asyncio.wait_for(
                self.client.complete(messages), timeout=self.t1_deadline_s
            )
        except (asyncio.TimeoutError, TimeoutError):
            self.dropped_timeout += 1
            self._drop_after(esc, decision_id, "t1_timeout", started)
            return None
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:
            self.dropped_error += 1
            log.exception("T1 call failed for %s", decision_id)
            self._drop_after(
                esc, decision_id, f"t1_error:{type(exc).__name__}", started
            )
            return None
        return resp, meta, path, []

    @staticmethod
    def _urgency(score: float) -> str:
        return "high" if score >= 1.5 else "normal" if score >= 0.5 else "low"

    @staticmethod
    def _deliver(score: float) -> str:
        """High urgency speaks now; anything less waits for a quiet moment."""
        return "now" if score >= 1.5 else "quiet"

    async def _write(
        self, state: dict, verdict: Verdict, settings: DeciderSettings,
    ) -> tuple[T1Response, list[str]]:
        """A clerk-shaped response from a verdict: one writer per fired action.

        ``annotate`` always fires. A fired ``act`` is a soft sound cue, which
        needs no writer; a fired ``look`` asks the look-question writer. Without
        writers -- the fake reasoner path -- everything but the annotate and
        the sound is dropped and the summary line is the trigger itself.
        """

        fired = verdict.fires(settings.thresholds())
        writers = self.writers
        ran: list[str] = []
        actions: list[Any] = []

        if writers is None:
            line = _fallback_summary(state)
        else:
            line = await writers.summary_line(state, verdict)
            ran.append("summary_line")
        actions.append(AnnotateAction(line=line))

        for name in fired:
            if name == "annotate":
                continue
            if name == "act":
                actions.append(ActAction(kind="sound", args={"name": LOOK_SOUND}))
                ran.append("act:sound")
                continue
            if writers is None:
                ran.append(f"{name}:no_writer")
                continue
            if name == "log_insight":
                writer, got = "insight", await writers.insight(state, verdict)
                if got is not None:
                    actions.append(LogInsightAction(category=got[0], text=got[1]))
            elif name == "remember":
                writer, got = "persona_fact", await writers.persona_fact(state, verdict)
                if got is not None:
                    actions.append(RememberAction(line=got))
            elif name == "watch":
                writer, got = "watch_condition", await writers.watch_condition(state, verdict)
                if got is not None:
                    actions.append(WatchAction(after_s=got[0], condition=got[1],
                                               reason=verdict.topic))
            elif name == "speak":
                writer = "handoff_topic"
                got = await writers.handoff_topic(state, verdict, action="speak")
                if got is not None:
                    actions.append(SpeakAction(text=got,
                                               urgency=self._urgency(verdict.urgency),
                                               deliver=self._deliver(verdict.urgency)))
            elif name == "ask":
                writer, got = "question", await writers.question(state, verdict)
                if got is not None:
                    actions.append(AskAction(text=got[0], answer_kind=got[1],
                                             fills=got[2], reason=verdict.topic,
                                             deliver=self._deliver(verdict.urgency)))
            elif name == "look":
                got = await writers.look_question(state, verdict)
                if got is not None:
                    actions.append(LookAction(question=got, reason=verdict.topic))
                ran.append("look_question" if got is not None else "look:failed")
                continue
            else:  # pragma: no cover - ACTIONS is closed
                continue
            ran.append(writer if got is not None else f"{writer}:failed")

        resp = T1Response(
            interpretation=line,
            confidence=max(verdict.probabilities.values(), default=0.0),
            actions=actions,
        )
        return resp, ran

    def _open_episodes(self, esc: Escalation) -> list[Any]:
        try:
            return [ep for ep in self.db.list_episodes(day=day_key(esc.t)) if ep.open]
        except Exception:  # pragma: no cover - defensive
            log.exception("could not read open episodes; deciding without them")
            return []

    # -- envelope ---------------------------------------------------------

    def _context(self, esc: Escalation) -> tuple[list[Any], str]:
        """Today's summary lines and the seven-day text, read once per
        escalation and shared by the decider state and the envelope."""

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
        return today, seven_day

    def _envelope(
        self, esc: Escalation, frames: dict[str, bytes], *,
        today: list[Any] | None = None, seven_day: str | None = None,
    ) -> list[dict[str, Any]]:
        if today is None or seven_day is None:
            read_today, read_seven = self._context(esc)
            today = read_today if today is None else today
            seven_day = read_seven if seven_day is None else seven_day

        try:
            recent_questions = self.db.list_questions(limit=RECENT_QUESTIONS)
        except Exception:  # pragma: no cover - defensive
            log.exception("could not read recent questions; sending none")
            recent_questions = []

        try:
            said = said_block(self.db, esc.t)
            constant = constant_block(self.db, esc.t)
        except Exception:  # pragma: no cover - a context block must never cost a wake-up
            log.exception("could not build the session context; sending none")
            said, constant = None, None

        return build_envelope(
            esc,
            frames,
            today,
            seven_day,
            self.current_persona(),
            k=FRAMES_PER_ESCALATION,
            learned=self.learned_lines(),
            recent_questions=recent_questions,
            said=said,
            constant=constant,
        )

    # -- the growing persona ----------------------------------------------
    #
    # The system prompt is rebuilt on every wake-up rather than cached at
    # construction, because both halves of it can change while the process
    # runs: the operator can rewrite the persona from the dashboard, and T1
    # adds to what it has learned with every `remember` it emits.

    def current_persona(self) -> str:
        """The operator's override if there is one, else the persona we were given."""

        try:
            override = self.db.get_persona()
        except Exception:  # pragma: no cover - defensive
            log.exception("could not read the persona override; using the default")
            return self.persona
        return override or self.persona

    def learned_lines(self) -> list[str]:
        """Active profile lines, oldest first, capped for the prompt."""

        try:
            rows = self.db.profile_lines(limit=LEARNED_MAX)
        except Exception:  # pragma: no cover - defensive
            log.exception("could not read the learned lines; sending none")
            return []
        return [str(row["line"]) for row in rows if row.get("line")]

    def _remember(self, decision_id: str, t: float, resp: Any) -> None:
        """Store every ``remember`` line on the decision (Part A).

        Done here rather than in :class:`~pipeline.actions.handlers.ActionHandler`
        because this is the only layer that owns the persona the lines feed
        back into. A duplicate is not an error: the model re-derives the same
        fact across a day, and the db drops it silently.
        """

        for action in resp.actions:
            if getattr(action, "type", None) != "remember":
                continue
            try:
                line_id = self.db.add_profile_line(action.line, t, decision_id)
            except Exception:
                log.exception("could not remember a line for %s", decision_id)
                continue
            if line_id is None:
                log.info("%s: remember ignored, already known: %s",
                         decision_id, action.line)
            else:
                self.remembered += 1
                log.info("%s: remembered %s -- %s", decision_id, line_id, action.line)

    def _label_episode(self, episode_id: str | None, resp: Any) -> None:
        """Name the episode after the first ``annotate`` line, once.

        "Once" is the whole rule: an episode runs for minutes and wakes T1
        several times, and the last line ("still at the desk") is a far worse
        name for it than the first ("cold brew, desk, 14:20").
        """

        if not episode_id:
            return
        line = next(
            (a.line for a in resp.actions
             if getattr(a, "type", None) == "annotate" and (a.line or "").strip()),
            None,
        )
        if not line:
            return
        try:
            if self.db.episode_label(episode_id):
                return
            self.db.set_episode_label(episode_id, line.strip())
        except Exception:  # pragma: no cover - defensive
            log.exception("could not label episode %s", episode_id)

    def _extend_episode_label(self, episode_id: str | None, parse: Any) -> None:
        """Append what an answer settled to its episode's label.

        ``cold brew, desk`` becomes ``cold brew, desk · confirmed, 2``. Skipped
        when the same fact is already on the label, so a re-delivered answer
        cannot stutter it.
        """

        if not episode_id:
            return
        fact = settled_fact(parse)
        if not fact:
            return
        try:
            current = (self.db.episode_label(episode_id) or "").strip()
            if fact in current:
                return
            self.db.set_episode_label(
                episode_id, f"{current} \u00b7 {fact}" if current else fact
            )
        except Exception:  # pragma: no cover - defensive
            log.exception("could not extend the label on episode %s", episode_id)

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
            "answers_completed": self.answers_completed,
            "answers_dropped": self.answers_dropped,
            "remembered": self.remembered,
            "fast_pathed": self.fast_pathed,
            "decided_by_decider": self.decided_by_decider,
            "fell_back": dict(self.fell_back),
            "look_reruns": self.look_reruns,
            "look_reruns_dropped": self.look_reruns_dropped,
            "frames_copied": self.evidence.copied,
            "frames_missing": self.evidence.missing,
            "model": getattr(self.client, "model", ""),
            "deadline_s": self.t1_deadline_s,
            "last_latency_ms": self.last_latency_ms,
            "last_decision_t": self.last_decision_t,
            **{f"speech_{k}": v for k, v in self.speech.stats().items()},
        }
