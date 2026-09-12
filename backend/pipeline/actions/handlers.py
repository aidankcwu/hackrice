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

log = logging.getLogger(__name__)

__all__ = ["ActionHandler"]


def _rid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


class ActionHandler:
    """Applies a normalised :class:`T1Response` to the database and the speaker."""

    def __init__(
        self, db: Database, speech: SpeechLimiter, timings: Timings
    ) -> None:
        self.db = db
        self.speech = speech
        self.timings = timings

    def apply(
        self, decision_id: str, t: float, resp: "T1Response"
    ) -> dict[str, Any]:
        """Run every action. Returns a small summary for the decision row."""

        result: dict[str, Any] = {
            "spoke": False,
            "insights": 0,
            "watches": 0,
            "annotated": False,
        }

        for action in resp.actions:
            try:
                self._one(decision_id, t, action, result)
            except Exception:
                log.exception(
                    "action %s failed for decision %s", action.type, decision_id
                )

        return result

    # -- per-action ------------------------------------------------------

    def _one(
        self, decision_id: str, t: float, action: Any, result: dict[str, Any]
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
            if self.speech.allow(t):
                self.speech.speak(action.text, action.urgency, t=t)
                result["spoke"] = True
            else:
                log.info(
                    "speak proposed but suppressed by the limiter (decision %s)",
                    decision_id,
                )

        elif kind == "nothing":
            pass

        else:  # pragma: no cover - the schema is closed
            log.warning("unknown action type %r on decision %s", kind, decision_id)
