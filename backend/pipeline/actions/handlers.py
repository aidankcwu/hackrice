"""Action handling (SPEC §4.4).

Each action in the T1 response maps to exactly one durable effect:

===============  ==========================================================
``annotate``     a line in ``today_summary`` -- part 4 of the next envelope
``log_insight``  a row in ``insights``, feeding daily and weekly reports
``watch``        a row in ``pending_checks`` the trigger gate polls
``speak``        the rate limiter, then the TTS seam
``nothing``      no-op
===============  ==========================================================

Actions are not mutually exclusive: one response may annotate and watch, or
speak and log an insight. A handler that throws must not lose the others, so
each is applied independently and failures are logged rather than propagated.

Every timestamp written here is ``esc.t`` -- simulated time, one clock.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..config import Timings
from ..db import Database
from ..models import Insight, PendingCheck, TodaySummaryLine
from .speech import SpeechLimiter

if TYPE_CHECKING:  # `pipeline.reasoner` imports this module: keep it one-way.
    from ..reasoner.schema import T1Response
    from .questions import QuestionManager

log = logging.getLogger(__name__)

__all__ = ["ActionHandler"]


def _rid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


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
