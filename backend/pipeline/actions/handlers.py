"""Action handling (SPEC §4.4).

Each action in the T1 response maps to exactly one durable effect:

===============  ==========================================================
``annotate``     a line in ``today_summary`` -- part 4 of the next envelope
``log_insight``  a row in ``insights``, feeding daily and weekly reports
``watch``        a row in ``pending_checks`` the trigger gate polls
``speak``        the rate limiter, then the TTS seam
``act``          an ``act`` message down the ingest socket (PLAN 4.1)
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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from longevity import wire

from ..config import Timings
from ..db import Database
from ..models import Insight, PendingCheck, TodaySummaryLine
from .speech import SpeechLimiter

if TYPE_CHECKING:  # `pipeline.reasoner` imports this module: keep it one-way.
    from ..reasoner.schema import T1Response
    from .questions import QuestionManager

log = logging.getLogger(__name__)

__all__ = [
    "ActionHandler", "FAST_PATHED", "ACT_SENT", "ACTED", "ACT_FAILED",
    "ACT_FAILED_LINES", "make_act_sender",
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

#: Spoken once when an act fails, per ``kind``: what did not happen, then the
#: cheapest way back (brian-ui voice.md). Unknown kinds use ``""``.
ACT_FAILED_LINES: dict[str, str] = {
    "calendar_block": "The walk did not go on your calendar. 20 min outside before sunset still counts.",
    "screen_shield": "The screen shield did not turn on. The phone in another room until 07:00 does the same.",
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
    ) -> None:
        self.db = db
        self.speech = speech
        self.timings = timings
        self.questions = questions
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
                )
                result.setdefault("outcomes", {})[index] = {"outcome": outcome}
                if outcome.startswith("handed_off"):
                    result["spoke"] = True
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
                )
                result.setdefault("outcomes", {})[index] = {"outcome": outcome}
                if outcome.startswith("handed_off"):
                    result["spoke"] = True
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
        try:
            decision = next((d for d in self.db.decisions_between(decision_t, decision_t)
                             if d.id == decision_id), None)
            if decision is not None:
                for row in decision.actions:
                    if row.get("type") == "act" and row.get("id") == act_id:
                        row["outcome"] = outcome
                        row["detail"] = detail
                        self.db.insert_decision(decision)
                        break
        except Exception:
            log.exception("could not record act_result %s on decision %s", act_id,
                          decision_id)
        log.info("act %s %s: %s (%s)", act_id, kind, outcome, detail)
        if not ok:
            self._say_act_failed(kind, decision_t if t is None else t)
        return outcome

    def _say_act_failed(self, kind: str, t: float) -> None:
        """One line through the speech limiter (STATE §8), like a missed dose."""

        line = ACT_FAILED_LINES.get(kind, ACT_FAILED_LINES[""])
        if self.speech.allow(t):
            self.speech.speak(line, "normal", t=t)
        else:
            log.info("act failure line suppressed by the limiter (%s)", kind)
