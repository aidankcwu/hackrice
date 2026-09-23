"""Action handling (SPEC §4.4).

Each action in the T1 response maps to exactly one durable effect:

===============  ==========================================================
``annotate``     a line in ``today_summary`` -- part 4 of the next envelope
``log_insight``  a row in ``insights``, feeding daily and weekly reports
``watch``        a row in ``pending_checks`` the trigger gate polls; with a
                 ``concept``, an armed condition on the watcher instead, which
                 re-escalates as ``watch_armed`` when the concept comes back
``speak``        the rate limiter, then the TTS seam
``act``          an ``act`` message down the ingest socket (PLAN 4.1); a
                 ``sound`` kind first passes its own hourly limiter
``look``         one targeted labeler call on the newest frame; the answer is
                 polled (never awaited on a tick) and re-runs the decision, or
                 goes to the voice agent when a conversation is open
``nothing``      no-op
===============  ==========================================================

Actions are not mutually exclusive: one response may annotate and watch, or
speak and log an insight. A handler that throws must not lose the others, so
each is applied independently and failures are logged rather than propagated.

Every timestamp written here is ``esc.t`` -- simulated time, one clock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from longevity import wire

from ..config import Timings
from ..db import Database
from ..models import Insight, PendingCheck, TodaySummaryLine
from .sound import SOUND_KIND, SoundLimiter, is_sound_act
from .speech import SpeechLimiter

if TYPE_CHECKING:  # `pipeline.reasoner` imports this module: keep it one-way.
    from ..reasoner.schema import T1Response
    from .questions import QuestionManager

log = logging.getLogger(__name__)

__all__ = [
    "ActionHandler", "FAST_PATHED", "ACT_SENT", "ACTED", "ACT_FAILED",
    "ACT_FAILED_LINES", "SOUND_RATE_LIMITED", "LOOKED", "LOOK_UNAVAILABLE",
    "LOOK_CHAINED", "LOOK_TIMEOUT", "LOOK_ANSWERED", "LOOK_ANSWER_WAIT_S",
    "WATCH_ARMED", "WATCH_ARMED_UNAVAILABLE", "MAX_ARMED_WATCHES", "armed_watch_id",
    "make_act_sender",
]

#: Outcome on a clerk ``speak``/``ask`` for a moment the gate already handed
#: straight to the voice agent (``Escalation.handed_off``): the words are being
#: said, so the clerk's hand-off would only open a second conversation.
FAST_PATHED = "fast_pathed"

#: Outcomes an ``act`` action row moves through (PLAN 4.1). ``sent`` until the
#: phone's ``act_result`` arrives; a veto is recorded as ``vetoed:<reason>``.
ACT_SENT = "sent"
ACTED = "acted"
ACT_FAILED = "act_failed"
#: A sound ``act`` refused by the handler's own hourly limiter.
SOUND_RATE_LIMITED = "sound_rate_limited"

#: Outcomes a ``look`` action row moves through. ``looked`` until the answer
#: lands (then ``answered:<where>`` -- ``conversation`` or ``rerun``) or the
#: wait runs out (``look_timeout``). ``look_unavailable``: no capture to ask
#: (sim mode). ``look_chained``: a look fired by the re-run itself, dropped.
LOOKED = "looked"
LOOK_ANSWERED = "answered"
LOOK_UNAVAILABLE = "look_unavailable"
LOOK_CHAINED = "look_chained"
LOOK_TIMEOUT = "look_timeout"

#: Outcomes of a ``watch`` with a concept (docs/PERCEPTION.md "Gate and
#: actions"). ``watch_armed``: the watcher holds it and will wake the gate.
#: ``watch_armed_unavailable``: no capture or no watcher (sim mode, WATCHER=0),
#: so it fell back to a timed pending check at the deadline.
WATCH_ARMED = "watch_armed"
WATCH_ARMED_UNAVAILABLE = "watch_armed_unavailable"
#: Armed watches the handler remembers, mirroring the watcher's ``max_armed``:
#: past this the oldest is forgotten here as it is evicted there.
MAX_ARMED_WATCHES = 8

#: How long an answer is polled for after a ``look`` went out: the tagger's
#: 3 s ceiling plus room for the mailbox to start the call. The capture bridge
#: overrides it with ``look_wait_s`` when it knows the tagger's real ceiling.
LOOK_ANSWER_WAIT_S = 4.0
#: The polling step. A tick is never blocked: the poll runs as its own task.
LOOK_POLL_S = 0.1

#: Spoken once when an act fails, per ``kind``: what did not happen, then the
#: cheapest way back (brian-ui voice.md). Unknown kinds use ``""``.
ACT_FAILED_LINES: dict[str, str] = {
    "calendar_block": "The walk did not go on your calendar. 20 min outside before sunset still counts.",
    "screen_shield": "The screen shield did not turn on. The phone in another room until 07:00 does the same.",
    #: A failed chime does not earn a sentence.
    SOUND_KIND: "",
    "": "That did not work on your phone.",
}

#: ``send_act`` signature: one ``wire.act_message`` in, ``True`` when a send was
#: scheduled (not that the phone did it -- that is ``act_result``).
ActSendFn = Callable[[str], bool]
#: ``act_veto`` signature: ``(kind, args, t)`` -> a reason to hold the act back,
#: or ``None`` to let it through. Consulted before every send, like the gates in
#: front of ``speak``.
ActVetoFn = Callable[[str, dict[str, Any], float], "str | None"]


def _rid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def armed_watch_id(decision_id: str, concept: str) -> str:
    """The watch id an armed ``watch`` gets: derived from the decision so the
    ``watch_armed`` escalation carries the decision back by name, and per
    concept so one decision can arm two."""

    return f"{decision_id}/{concept}"


def _console_send(message: str) -> bool:
    """No phone to act on (sim, unit tests): log the act, count it as sent."""

    log.info("ACT: %s", message)
    return True


def make_act_sender(link: Any) -> ActSendFn:
    """``send_act`` over the ingest socket: ``link.send_text``, where speech ends.

    Fire-and-forget like ``capture.speak.make_speak_fn``: the send is scheduled
    on the running loop and never raises. ``False`` means no phone is connected
    (or there is no loop), so nothing went out.
    """

    def send(message: str) -> bool:
        if not getattr(link, "clients", None):
            log.info("act skipped: no phone connected")
            return False
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(link.send_text(message), name="glasses-act")
        except Exception:
            log.exception("could not schedule act to phone")
            return False
        task.add_done_callback(_consume_failure)
        return True

    return send


def _consume_failure(task: "asyncio.Task[Any]") -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("act send failed")


class ActionHandler:
    """Applies a normalised :class:`T1Response` to the database and the speaker."""

    def __init__(
        self, db: Database, speech: SpeechLimiter, timings: Timings,
        questions: "QuestionManager | None" = None,
        conversation: Any | None = None,
        *,
        capture: Any | None = None,
        sound_max_per_hour: int = 6,
    ) -> None:
        self.db = db
        self.speech = speech
        self.timings = timings
        self.questions = questions
        #: The trigger gate, for ``escalate_armed`` when an armed watch fires.
        #: Set by the wiring; ``None`` means a wake-up is only logged.
        self.gate: Any | None = None
        #: Armed watches awaiting the watcher: watch id -> (decision id,
        #: concept, deadline on the decision clock). Bounded to
        #: ``MAX_ARMED_WATCHES``, oldest evicted, mirroring the watcher.
        self._armed: dict[str, tuple[str, str, float]] = {}
        self._capture: Any | None = None
        #: The capture bridge (``LongevityCapture``): ``look(question)``,
        #: ``take_look_answer()``, ``arm``/``disarm``. ``None`` in sim mode,
        #: where a ``look`` is ``look_unavailable`` and an armed ``watch``
        #: ``watch_armed_unavailable``.
        self.capture = capture
        #: The reasoner that owns this handler, for ``rerun_after_look``. Set
        #: by the reasoner itself; ``None`` means an answer with no open
        #: conversation has nowhere to go and is only recorded.
        self.reasoner: Any | None = None
        #: Hourly cap on sound cues, separate from speech (docs/PERCEPTION.md).
        self.sound = SoundLimiter(sound_max_per_hour)
        #: The one look whose answer is awaited: decision id, decision t,
        #: question, deadline (wall clock). A newer look replaces it -- the
        #: tagger's mailbox is one slot too, so the older answer never lands.
        self._look: tuple[str, float, str, float] | None = None
        self._look_task: "asyncio.Task[None] | None" = None
        #: The voice agent. When one is wired, ``speak`` and ``ask`` stop being
        #: utterances and become hand-offs: the clerk names a topic and a
        #: reason, and the agent writes the words
        #: (docs/CONVERSATION_DESIGN.md §1). Without one -- a bare handler in a
        #: unit test, a pipeline built before the agent existed -- the old
        #: direct paths still apply, so nothing is silently muted.
        self.conversation = conversation
        #: Where ``act`` messages go. The wiring points it at the ingest
        #: socket (:func:`make_act_sender`); without a phone it logs.
        self.send_act: ActSendFn = _console_send
        #: Persona veto in front of every act, the way the speech gates sit in
        #: front of ``speak``. ``None``: nothing vetoes.
        self.act_veto: ActVetoFn | None = None
        #: Acts sent and not yet answered: act id -> (decision id, decision t,
        #: kind). Popped by the first ``act_result``, so a repeat is ignored.
        self._acts: dict[str, tuple[str, float, str]] = {}

    @property
    def capture(self) -> Any | None:
        return self._capture

    @capture.setter
    def capture(self, value: Any | None) -> None:
        """Assigning the capture bridge also registers this handler as where its
        armed wake-ups go (``capture.on_armed_wake``), so the wiring's one
        assignment is the whole registration. A fake without the attribute is
        left alone."""

        self._capture = value
        if value is not None and hasattr(value, "on_armed_wake"):
            value.on_armed_wake = self.on_armed_wake

    def apply(
        self, decision_id: str, t: float, resp: "T1Response", *,
        episode_id: str | None = None, esc: Any | None = None,
    ) -> dict[str, Any]:
        """Run every action. Returns a small summary for the decision row.

        ``result["outcomes"]`` maps an action's index in ``resp.actions`` --
        which is also its index in ``decision.actions`` -- to the keys the
        reasoner should merge onto that action's row (``outcome``, and
        ``question_id`` for a question that actually went out).
        """

        result: dict[str, Any] = {
            "spoke": False,
            "insights": 0,
            "watches": 0,
            "annotated": False,
        }

        has_ask = any(action.type == "ask" for action in resp.actions)

        for index, action in enumerate(resp.actions):
            try:
                self._one(decision_id, t, action, result, episode_id, has_ask,
                          index=index, esc=esc)
            except Exception:
                log.exception(
                    "action %s failed for decision %s", action.type, decision_id
                )

        return result

    # -- per-action ------------------------------------------------------

    def _one(
        self, decision_id: str, t: float, action: Any, result: dict[str, Any],
        episode_id: str | None, has_ask: bool, *, index: int = 0,
        esc: Any | None = None,
    ) -> None:
        kind = action.type
        handed_off = getattr(esc, "handed_off", None) if esc is not None else None

        if kind in ("speak", "ask") and handed_off:
            # The fast path: this moment went to the voice agent before the
            # clerk was even called. Record what the clerk wanted, drop it.
            result.setdefault("outcomes", {})[index] = {"outcome": FAST_PATHED}
            log.info("%s dropped: fast_pathed to %s (decision %s)", kind,
                     handed_off, decision_id)
            return

        if kind == "annotate":
            self.db.append_summary_line(
                TodaySummaryLine(t=t, line=action.line, decision_id=decision_id)
            )
            result["annotated"] = True

        elif kind == "log_insight":
            self.db.insert_insight(
                Insight(
                    id=_rid("i"),
                    t=t,
                    category=action.category or "general",
                    text=action.text,
                    decision_id=decision_id,
                )
            )
            result["insights"] += 1

        elif kind == "watch":
            concept = getattr(action, "concept", None)
            if concept:
                outcome, watch_id = self.arm_watch(decision_id, t, concept, action)
                result.setdefault("outcomes", {})[index] = {
                    "outcome": outcome, "watch_id": watch_id}
                result["watches"] += 1
                return
            after_s = action.after_s
            if after_s is None and action.condition is None:
                # A watch with neither a delay nor a condition would never
                # fire; give it the default delay rather than dropping it.
                after_s = self.timings.watch_default_after_s
            due_t = None if after_s is None else t + float(after_s)
            self.db.insert_pending_check(
                PendingCheck(
                    id=_rid("w"),
                    created_t=t,
                    due_t=due_t,
                    condition=action.condition,
                    reason=action.reason,
                    decision_id=decision_id,
                )
            )
            result["watches"] += 1

        elif kind == "speak":
            if has_ask:
                log.info("speak_dropped_for_ask (decision %s)", decision_id)
            elif self.conversation is not None:
                # Not an utterance any more: a topic and a reason handed to the
                # agent that owns the mouth (§1). It writes the words, and it
                # may decide the right shape is a question.
                outcome = self.conversation.request(
                    action.text, "statement", decision_id=decision_id,
                    episode_id=episode_id, esc=esc,
                    deliver=action.deliver, expire_s=action.expire_s,
                )
                result.setdefault("outcomes", {})[index] = {"outcome": outcome}
                if outcome.startswith("handed_off"):
                    result["spoke"] = True
                elif outcome == "deliver_waiting":
                    # Held for a quiet moment (US-M04); the voice agent reports
                    # the final outcome in its own counters.
                    log.info("speak hand-off waiting for a quiet tick, decision %s",
                             decision_id)
                else:
                    log.info("speak hand-off dropped (%s) for decision %s",
                             outcome, decision_id)
            elif self.questions is not None and self.questions.listening():
                log.info("speak_dropped_listening (decision %s)", decision_id)
            elif self.speech.allow(t):
                self.speech.speak(action.text, action.urgency, t=t)
                result["spoke"] = True
            else:
                log.info(
                    "speak proposed but suppressed by the limiter (decision %s)",
                    decision_id,
                )

        elif kind == "ask":
            if self.conversation is not None:
                outcome = self.conversation.request(
                    action.text, "question", decision_id=decision_id,
                    episode_id=episode_id, esc=esc, reason=action.reason,
                    deliver=action.deliver, expire_s=action.expire_s,
                )
                result.setdefault("outcomes", {})[index] = {"outcome": outcome}
                if outcome.startswith("handed_off"):
                    result["spoke"] = True
                elif outcome == "deliver_waiting":
                    # Held for a quiet moment (US-M04); the voice agent reports
                    # the final outcome in its own counters.
                    log.info("ask hand-off waiting for a quiet tick, decision %s",
                             decision_id)
                else:
                    log.info("ask hand-off dropped (%s) for decision %s",
                             outcome, decision_id)
                return
            if self.questions is None:
                log.info("ask skipped: no question manager (decision %s)", decision_id)
                return
            row, reason = self.questions.ask(
                decision_id=decision_id, t=t, episode_id=episode_id, action=action
            )
            payload = action.model_dump()
            if row is not None:
                payload["question_id"] = row.id
            payload["outcome"] = "sent" if reason is None else f"suppressed:{reason}"
            result.setdefault("outcomes", {})[index] = {
                k: payload[k] for k in ("question_id", "outcome") if k in payload
            }
            result.setdefault("asks", []).append(payload)

        elif kind == "act":
            row = self.act(decision_id, t, getattr(action, "kind", ""),
                           getattr(action, "args", None) or {})
            result.setdefault("outcomes", {})[index] = {
                k: row[k] for k in ("id", "outcome", "detail") if k in row
            }

        elif kind == "look":
            outcome = self.look(decision_id, t, action.question)
            result.setdefault("outcomes", {})[index] = {"outcome": outcome}

        elif kind == "remember":
            # Applied by `Reasoner._remember`, which owns the persona these
            # lines feed back into. Named here anyway: without the branch it
            # falls through to the "unknown action type" warning below, which
            # would be untrue of every `remember` the model ever emits.
            pass

        elif kind == "nothing":
            pass

        else:  # pragma: no cover - the schema is closed
            log.warning("unknown action type %r on decision %s", kind, decision_id)

    # -- act (PLAN 4.1) ----------------------------------------------------

    def act(
        self, decision_id: str, t: float, kind: str, args: dict[str, Any],
    ) -> dict[str, Any]:
        """Send one ``act`` to the phone. Returns the decision's action row.

        The row carries ``outcome``: ``sent`` (waiting for ``act_result``),
        ``vetoed:<reason>``, or ``act_failed`` when nothing could be sent -- a
        failure the wearer hears about once, same as a failed result. Never
        raises: the caller writes the row whatever happened here.
        """

        act_id = _rid("a")
        row: dict[str, Any] = {"type": "act", "id": act_id, "kind": kind,
                               "args": dict(args)}
        if is_sound_act(row) and not self.sound.allow(t):
            # Its own limiter, before the veto and the send: a cue past the
            # hourly cap costs nothing and says nothing.
            row["outcome"] = SOUND_RATE_LIMITED
            log.info("act %s sound rate limited, decision %s", act_id, decision_id)
            return row
        try:
            reason = self.act_veto(kind, row["args"], t) if self.act_veto else None
        except Exception:
            log.exception("act veto raised; holding the act back")
            reason = "veto_error"
        if reason:
            row["outcome"] = f"vetoed:{reason}"
            log.info("act %s %s vetoed (%s), decision %s", act_id, kind, reason,
                     decision_id)
            return row
        try:
            sent = bool(self.send_act(wire.act_message(act_id, kind, row["args"])))
        except Exception:
            log.exception("act %s could not be sent", act_id)
            sent = False
        if not sent:
            row["outcome"] = ACT_FAILED
            row["detail"] = "no_transport"
            self._say_act_failed(kind, t)
            return row
        row["outcome"] = ACT_SENT
        self._acts[act_id] = (decision_id, t, kind)
        log.info("act %s %s sent for decision %s", act_id, kind, decision_id)
        return row

    def on_act_result(
        self, act_id: str, ok: bool, detail: str = "", t: float | None = None,
    ) -> str | None:
        """The phone's ``act_result``: flip the decision's row, speak on failure.

        Returns the outcome written (``acted`` / ``act_failed``), or ``None``
        for an id this handler is not waiting on -- a repeat, or a result for
        an act sent before a restart -- which changes nothing and says nothing.
        """

        pending = self._acts.pop(act_id, None)
        if pending is None:
            log.info("act_result for unknown or settled act %s ignored", act_id)
            return None
        decision_id, decision_t, kind = pending
        outcome = ACTED if ok else ACT_FAILED
        self._patch_action_row(
            decision_id, decision_t,
            lambda row: row.get("type") == "act" and row.get("id") == act_id,
            {"outcome": outcome, "detail": detail},
        )
        log.info("act %s %s: %s (%s)", act_id, kind, outcome, detail)
        if not ok:
            self._say_act_failed(kind, decision_t if t is None else t)
        return outcome

    def _patch_action_row(
        self, decision_id: str, decision_t: float,
        match: Callable[[dict[str, Any]], bool], patch: dict[str, Any],
    ) -> bool:
        """Merge ``patch`` onto the first matching action row of a written
        decision. False when the row was not found; never raises."""

        try:
            decision = next((d for d in self.db.decisions_between(decision_t, decision_t)
                             if d.id == decision_id), None)
            if decision is None:
                return False
            for row in decision.actions:
                if match(row):
                    row.update(patch)
                    self.db.insert_decision(decision)
                    return True
        except Exception:
            log.exception("could not patch an action row on decision %s", decision_id)
        return False

    def _say_act_failed(self, kind: str, t: float) -> None:
        """One line through the speech limiter (STATE §8), like a missed dose."""

        line = ACT_FAILED_LINES.get(kind, ACT_FAILED_LINES[""])
        if not line:
            return
        if self.speech.allow(t):
            self.speech.speak(line, "normal", t=t)
        else:
            log.info("act failure line suppressed by the limiter (%s)", kind)

    # -- armed watch (docs/PERCEPTION.md "Gate and actions") ---------------

    def arm_watch(
        self, decision_id: str, t: float, concept: str, action: Any,
    ) -> tuple[str, str]:
        """Arm the watcher on ``concept`` for ``action.within_s``. Returns
        ``(outcome, watch_id)``.

        Without a capture or a watcher the arming degrades to today's timed
        pending check at the deadline (``watch_armed_unavailable``), so the
        clerk's "come back to this" is never silently lost.
        """

        within_s = float(getattr(action, "within_s", None) or 600)
        watch_id = armed_watch_id(decision_id, concept)
        arm = getattr(self.capture, "arm", None)
        armed = False
        if callable(arm):
            try:
                armed = bool(arm(concept, within_s, watch_id))
            except Exception:
                log.exception("armed watch %s could not be set", watch_id)
        if not armed:
            log.info("armed watch %s unavailable: no watcher; pending check at +%.0fs",
                     watch_id, within_s)
            self.db.insert_pending_check(
                PendingCheck(
                    id=_rid("w"), created_t=t, due_t=t + within_s,
                    condition=action.condition or concept,
                    reason=action.reason or f"{concept} within {within_s:.0f}s",
                    decision_id=decision_id,
                )
            )
            return WATCH_ARMED_UNAVAILABLE, watch_id
        self._armed.pop(watch_id, None)
        self._armed = {k: v for k, v in self._armed.items() if v[2] >= t}
        self._armed[watch_id] = (decision_id, concept, t + within_s)
        while len(self._armed) > MAX_ARMED_WATCHES:
            oldest = next(iter(self._armed))
            del self._armed[oldest]
            log.info("armed watch %s forgotten (more than %d armed)", oldest,
                     MAX_ARMED_WATCHES)
        log.info("armed watch %s: %s within %.0fs (decision %s)", watch_id, concept,
                 within_s, decision_id)
        return WATCH_ARMED, watch_id

    def on_armed_wake(self, watch_id: str, concept: str, frame_t: float) -> Any:
        """The bridge's ``on_armed_wake``: an armed concept came back.

        Runs on the asyncio loop (the T0 loop forwards wake-ups there). The
        arming is spent here; the gate's ``escalate_armed`` builds the
        ``watch_armed`` escalation carrying the original decision id. An id
        this handler is not holding (evicted, from before a restart, or
        disarmed) is ignored. Returns what the gate returned, or ``None``.
        """

        pending = self._armed.pop(watch_id, None)
        if pending is None:
            log.info("armed wake for unknown watch %s ignored", watch_id)
            return None
        decision_id, armed_concept, _deadline = pending
        gate = self.gate
        escalate = getattr(gate, "escalate_armed", None)
        if not callable(escalate):
            log.info("armed watch %s fired but no gate is wired", watch_id)
            return None
        try:
            return escalate(watch_id, concept or armed_concept, frame_t, decision_id)
        except Exception:
            log.exception("armed watch %s could not escalate", watch_id)
            return None

    def disarm_watch(self, watch_id: str) -> None:
        """Forget an armed watch here and on the watcher."""

        self._armed.pop(watch_id, None)
        disarm = getattr(self.capture, "disarm", None)
        if callable(disarm):
            try:
                disarm(watch_id)
            except Exception:
                log.exception("armed watch %s could not be disarmed", watch_id)

    # -- look (docs/PERCEPTION.md "Gate and actions") ----------------------

    def look(self, decision_id: str, t: float, question: str) -> str:
        """Ask one question of the newest frame. Returns the row's outcome.

        The question goes to the labeler's mailbox through the capture bridge
        and this returns at once; the answer is polled by a task
        (:meth:`poll_look_answer`) for ``LOOK_ANSWER_WAIT_S``, never awaited on
        a tick. Without a capture (sim mode) the look is ``look_unavailable``.
        """

        capture = self.capture
        if capture is None or not callable(getattr(capture, "look", None)):
            log.info("look unavailable: no capture (decision %s)", decision_id)
            return LOOK_UNAVAILABLE
        try:
            capture.look(question)
        except Exception:
            log.exception("look could not be issued (decision %s)", decision_id)
            return LOOK_UNAVAILABLE
        wait_s = float(getattr(capture, "look_wait_s", LOOK_ANSWER_WAIT_S) or LOOK_ANSWER_WAIT_S)
        if self._look is not None:
            log.info("look for decision %s superseded by decision %s", self._look[0],
                     decision_id)
        self._look = (decision_id, t, question, time.monotonic() + wait_s)
        log.info("look sent for decision %s: %r", decision_id, question)
        self._start_look_poll()
        return LOOKED

    def _start_look_poll(self) -> None:
        if self._look_task is not None and not self._look_task.done():
            return  # the running poll picks up the newer look from ``self._look``
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.info("look answer will not be polled: no running event loop")
            return
        self._look_task = loop.create_task(self._await_look_answer(), name="look-answer")
        self._look_task.add_done_callback(_consume_failure)

    async def _await_look_answer(self) -> None:
        while self._look is not None:
            if self.poll_look_answer():
                return
            if time.monotonic() >= self._look[3]:
                decision_id, decision_t, question, _ = self._look
                self._look = None
                log.info("look for decision %s timed out: %r", decision_id, question)
                self._patch_action_row(
                    decision_id, decision_t, lambda row: row.get("type") == "look",
                    {"outcome": LOOK_TIMEOUT},
                )
                return
            await asyncio.sleep(LOOK_POLL_S)

    def poll_look_answer(self) -> bool:
        """One synchronous poll of the capture's look mailbox.

        True when an answer landed and was delivered (or nothing is awaited);
        False while still waiting. Safe to call from anywhere per tick.
        """

        pending = self._look
        if pending is None:
            return True
        capture = self.capture
        take = getattr(capture, "take_look_answer", None)
        landed = take() if callable(take) else None
        if landed is None:
            return False
        self._look = None
        decision_id, decision_t, question, _ = pending
        _frame_t, answer = landed
        self._deliver_look_answer(decision_id, decision_t, question, str(answer))
        return True

    def _deliver_look_answer(
        self, decision_id: str, decision_t: float, question: str, answer: str,
    ) -> str:
        """Where the answer goes: an open conversation, else one re-run of the
        decision. Returns ``answered:<where>``, recorded on the look's row."""

        conversation = self.conversation
        where = "dropped"
        if conversation is not None and self._conversation_open(conversation):
            outcome = conversation.request(
                f"looked closer at the frame: {question} {answer}", "statement",
                decision_id=decision_id, reason="look",
            )
            where = f"conversation:{outcome}"
        elif self.reasoner is not None and callable(
            getattr(self.reasoner, "rerun_after_look", None)
        ):
            try:
                ran = self.reasoner.rerun_after_look(decision_id, question, answer)
            except Exception:
                log.exception("look re-run failed for decision %s", decision_id)
                ran = False
            where = "rerun" if ran else "rerun_dropped"
        outcome = f"{LOOK_ANSWERED}:{where}"
        log.info("look answer for decision %s -> %s: %r", decision_id, where, answer)
        self._patch_action_row(
            decision_id, decision_t, lambda row: row.get("type") == "look",
            {"outcome": outcome, "answer": answer},
        )
        return outcome

    @staticmethod
    def _conversation_open(conversation: Any) -> bool:
        current = getattr(conversation, "current", None)
        if callable(current):
            try:
                return current() is not None
            except Exception:
                return False
        return bool(getattr(conversation, "active", False))
