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
from ..models import Decision, Escalation, PendingQuestion
from .client import AnswerParser, ReasonerClient
from .envelope import build_envelope, local_time, select_frames, RECENT_QUESTIONS
from .evidence import EvidenceStore
from .prompts import DEFAULT_PERSONA, LEARNED_MAX, NO_SEVEN_DAY
from .schema import normalize

log = logging.getLogger(__name__)

__all__ = ["Reasoner", "FRAMES_PER_ESCALATION", "settled_fact"]

#: SPEC §4.3 -- "Four images is the right number; the fifth adds latency and
#: little information."
FRAMES_PER_ESCALATION = 4


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
        t1_deadline_s: float = 15.0,
        parser: AnswerParser | None = None,
        questions: Any | None = None,
        conversation: Any | None = None,
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

        self.evidence = EvidenceStore(db)
        self.handler = ActionHandler(db, speech, settings.timings, questions,
                                     conversation)

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
                actions=[a.model_dump() for a in norm.actions],
                spoke=False,
                dropped=False,
                latency_ms=int(latency_ms),
                model=str(meta.get("model") or ""),
            )
            # Written before the actions apply: a handler that throws must not
            # cost us the decision row (SPEC §6).
            self.db.insert_decision(decision)

            if epoch is not None and epoch != self.epoch:
                self.skipped_stale += 1
                log.info("%s: session changed while reasoning; actions skipped",
                         decision_id)
                self.completed += 1
                return

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

            if outcome.get("spoke"):
                decision.spoke = True
                self.spoke_count += 1

            if outcomes or outcome.get("spoke"):
                self.db.insert_decision(decision)

            self._remember(decision_id, esc.t, norm)
            self._label_episode(esc.episode_id, norm)

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

        try:
            recent_questions = self.db.list_questions(limit=RECENT_QUESTIONS)
        except Exception:  # pragma: no cover - defensive
            log.exception("could not read recent questions; sending none")
            recent_questions = []

        return build_envelope(
            esc,
            frames,
            today,
            seven_day,
            self.current_persona(),
            k=FRAMES_PER_ESCALATION,
            learned=self.learned_lines(),
            recent_questions=recent_questions,
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
            "frames_copied": self.evidence.copied,
            "frames_missing": self.evidence.missing,
            "model": getattr(self.client, "model", ""),
            "deadline_s": self.t1_deadline_s,
            "last_latency_ms": self.last_latency_ms,
            "last_decision_t": self.last_decision_t,
            **{f"speech_{k}": v for k, v in self.speech.stats().items()},
        }
