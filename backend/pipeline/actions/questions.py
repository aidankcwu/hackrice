"""Question admission, delivery, answer finalisation, and expiry."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..config import Timings
from ..db import Database
from ..models import Decision, PendingQuestion, TodaySummaryLine
from ..reasoner.client import AnswerParser
from ..reasoner.schema import AnswerParse, AskAction
from .speech import SpeechLimiter

if TYPE_CHECKING:
    from ..reasoner.reasoner import Reasoner

log = logging.getLogger(__name__)

__all__ = ["AskLimiter", "QuestionManager"]


class AskLimiter:
    """Minimum-gap and rolling-hour limiter for admitted questions."""

    def __init__(self, min_gap_s: float, max_per_hour: int) -> None:
        self.min_gap_s = float(min_gap_s)
        self.max_per_hour = int(max_per_hour)
        self._granted: list[float] = []
        self.allowed = 0
        self.suppressed = 0

    def reason(self, t: float, *, skip_min_gap: bool = False) -> str | None:
        self._granted = [g for g in self._granted if g > t - 3600.0]
        if not skip_min_gap and self._granted and t - self._granted[-1] < self.min_gap_s:
            self.suppressed += 1
            return "ask_min_gap"
        if len(self._granted) >= self.max_per_hour:
            self.suppressed += 1
            return "ask_max_per_hour"
        return None

    def grant(self, t: float) -> None:
        self._granted.append(t)
        self.allowed += 1

    @property
    def last_ask_t(self) -> float | None:
        return self._granted[-1] if self._granted else None

    def stats(self) -> dict[str, int]:
        return {"allowed": self.allowed, "suppressed": self.suppressed,
                "in_last_hour": len(self._granted)}


class QuestionManager:
    def __init__(
        self,
        db: Database,
        speech: SpeechLimiter,
        timings: Timings,
        *,
        send: Callable[[PendingQuestion], Awaitable[bool]],
        supports_ask: Callable[[], bool],
        has_transport: Callable[[], bool],
        parser: AnswerParser,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self.speech = speech
        self.timings = timings
        self.send = send
        self.supports_ask = supports_ask
        self.has_transport = has_transport
        self.parser = parser
        self.now_fn = now_fn
        self.limiter = AskLimiter(timings.ask_min_gap, timings.ask_max_per_hour)
        self.reasoner: Reasoner | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._decision_for: dict[str, str] = {}

    def ask(
        self, *, decision_id: str | None, t: float, episode_id: str | None,
        action: AskAction, followup_of: PendingQuestion | None = None,
    ) -> tuple[PendingQuestion | None, str | None]:
        try:
            reason: str | None = None
            if (followup_of is not None and (
                followup_of.followup_of is not None or any(
                    q.followup_of == followup_of.id
                    for q in self.db.questions_for_episode(followup_of.episode_id)
                )
            )):
                reason = "followup_denied"
            elif not self.supports_ask():
                reason = "ask_unsupported"
            elif not self.has_transport():
                reason = "no_transport"
            elif self.db.open_question() is not None:
                reason = "one_open"
            elif (followup_of is None and episode_id is not None and any(
                q.followup_of is None and q.status != "suppressed"
                for q in self.db.questions_for_episode(episode_id)
            )):
                reason = "same_episode"
            else:
                reason = self.limiter.reason(t, skip_min_gap=followup_of is not None)
                if reason is None:
                    # A question is the point of the exchange, so it is not held
                    # to the speech gap -- only to a short overlap gap so it
                    # never talks over an utterance still playing. It still
                    # stamps the limiter, so the next plain speak waits its turn.
                    last_spoken = self.speech.last_spoken_t
                    if (followup_of is None and last_spoken is not None
                            and t - last_spoken < self.timings.ask_speech_gap):
                        reason = "speech_gap"
                    else:
                        self.speech.grant(t)

            row = PendingQuestion(
                id=f"q_{uuid4().hex[:8]}", created_t=t, expires_t=None,
                decision_id=decision_id, episode_id=episode_id,
                question=action.text, answer_kind=action.answer_kind,
                fills=action.fills, status="suppressed" if reason else "open",
                followup_of=followup_of.id if followup_of else None,
                suppressed_reason=reason,
            )
            self.db.insert_question(row)
            if decision_id is not None:
                self._decision_for[row.id] = decision_id
            if reason is not None:
                log.info("ask suppressed (%s) for decision %s", reason, decision_id)
                return row, reason

            self.limiter.grant(t)
            try:
                asyncio.get_running_loop().create_task(self._send(row))
            except RuntimeError:
                row.status = "suppressed"
                row.suppressed_reason = "send_failed"
                self.db.update_question(row)
                self._report_send_failure(row)
                return row, "send_failed"
            return row, None
        except Exception:
            log.exception("question admission failed for decision %s", decision_id)
            return None, "send_failed"

    async def _send(self, row: PendingQuestion) -> None:
        """Deliver one admitted question, or finalise it `send_failed`.

        The whole delivery gets `ask_expire_s` and not a second more. Without a
        deadline a transport that neither returns nor raises — a socket that
        accepts bytes into a dead TCP window, a synthesiser past its own
        timeout — leaves the row `open` forever: `expires_t` is only set *after*
        a successful send, so `expire` never touches it, `one_open` blocks every
        later question, and `listening()` mutes speech for the rest of the run.
        A timeout is a send failure like any other (§8.2).

        ``asyncio.timeout`` rather than ``wait_for`` so a transport that answers
        at once still does so in the same turn of the loop — the deadline is a
        timer on this task, not a second task around the send.
        """
        try:
            async with asyncio.timeout(self.timings.ask_expire_s):
                sent = await self.send(row)
            if not sent:
                raise RuntimeError("send returned false")
            current = self.db.get_question(row.id)
            if current is not None and current.status == "open":
                current.sent_t = self.now_fn()
                current.expires_t = current.sent_t + self.timings.ask_expire_s
                self.db.update_question(current)
        except Exception:
            log.exception("question send failed: %s", row.id)
            current = self.db.get_question(row.id)
            if current is not None and current.status == "open":
                current.status = "suppressed"
                current.suppressed_reason = "send_failed"
                self.db.update_question(current)
                self._report_send_failure(current)

    def _report_send_failure(self, row: PendingQuestion) -> None:
        decision_id = self._decision_for.get(row.id)
        if decision_id is None:
            return
        decision = next((d for d in self.db.list_decisions(limit=200)
                         if d.id == decision_id), None)
        if decision is None:
            return
        for action in decision.actions:
            if (action.get("type") == "ask"
                    and action.get("question_id") == row.id):
                action["outcome"] = "suppressed:send_failed"
                self.db.insert_decision(decision)
                return

    def on_answer(self, question_id: str, text: str, heard: bool, t: float) -> None:
        try:
            question = self.db.get_question(question_id)
            if question is None or not self.db.claim_answer(question_id, text, heard, t):
                log.info("duplicate or late answer ignored: %s", question_id)
                return
            question = self.db.get_question(question_id) or question
            if not heard or not text.strip():
                question.status = "expired"
                question.parsed = {"understood": False, "note": "nothing heard"}
                self.db.update_question(question)
                self._annotate(question, f"asked: {question.question} — nothing heard", t)
                return
            if self.reasoner is None or not self.reasoner.try_answer(question, text, t):
                self.finalize_failure(question, "reasoner busy", t, "t1_busy")
        except Exception:
            log.exception("answer handling failed: %s", question_id)

    def finalize_failure(
        self, question: PendingQuestion, note: str, t: float, drop_reason: str
    ) -> None:
        current = self.db.get_question(question.id) or question
        current.status = "answered"
        current.parsed = {"understood": False, "note": note}
        self.db.update_question(current)
        self.db.insert_decision(Decision(
            id=f"d_answer_{question.id[2:]}", t=t,
            trigger=f"answer:{question.id}", trigger_tick_id="",
            episode_id=question.episode_id, interpretation=note, actions=[],
            dropped=True, drop_reason=drop_reason,
            model=getattr(self.parser, "model", ""),
        ))

    def apply_parse(self, question: PendingQuestion, parse: AnswerParse, t: float) -> None:
        current = self.db.get_question(question.id) or question
        current.status = "answered"
        current.parsed = parse.model_dump()
        self.db.update_question(current)
        note = parse.note
        line = f"wearer: {note}"
        self._annotate(current, line, t)
        decision = Decision(
            id=f"d_answer_{question.id[2:]}", t=t,
            trigger=f"answer:{question.id}", trigger_tick_id="",
            episode_id=question.episode_id, interpretation=note,
            actions=[{"type": "annotate", "line": line}],
            model=getattr(self.parser, "model", ""),
        )
        if (parse.followup and parse.understood and current.answer_kind == "yes_no"
                and parse.confirmed is True and current.followup_of is None
                and self.timings.ask_followup_max > 0):
            episode = next((e for e in self.db.list_episodes()
                            if e.id == current.episode_id), None)
            sighting = episode is not None and episode.kind.endswith("_sighting")
            action = AskAction(text=parse.followup,
                               answer_kind="count" if sighting else "free",
                               fills="count" if sighting else "note")
            row, reason = self.ask(
                decision_id=decision.id, t=t, episode_id=current.episode_id,
                action=action, followup_of=current,
            )
            payload = action.model_dump()
            if row is not None:
                payload["question_id"] = row.id
            payload["outcome"] = ("sent" if reason is None
                                  else f"suppressed:{reason}")
            decision.actions.append(payload)
        self.db.insert_decision(decision)

    def _annotate(self, q: PendingQuestion, line: str, t: float) -> None:
        self.db.append_summary_line(TodaySummaryLine(
            t=t, line=line, decision_id=q.decision_id,
        ))

    def expire(self, now: float) -> int:
        try:
            due = [q for q in self.db.list_questions(limit=100000)
                   if q.status == "open" and q.expires_t is not None
                   and q.expires_t <= now]
            count = self.db.expire_questions(now)
            for q in due:
                current = self.db.get_question(q.id)
                if current is not None and current.status == "expired":
                    self._annotate(current, f"asked: {q.question} — no answer", now)
            return count
        except Exception:
            log.exception("question expiry failed")
            return 0

    def start(self) -> None:
        if self._expiry_task is not None and not self._expiry_task.done():
            return
        async def run() -> None:
            while True:
                try:
                    await asyncio.sleep(5)
                    self.expire(self.now_fn())
                except Exception:
                    log.exception("question expiry timer failed")
        self._expiry_task = asyncio.create_task(run(), name="question-expiry")

    async def stop(self) -> None:
        if self._expiry_task is None:
            return
        self._expiry_task.cancel()
        with contextlib.suppress(BaseException):
            await self._expiry_task
        self._expiry_task = None

    def listening(self) -> bool:
        q = self.db.open_question()
        if q is None:
            return False
        return q.expires_t is None or self.now_fn() < q.expires_t

    def stats(self) -> dict[str, Any]:
        rows = self.db.list_questions(limit=100000)
        counts = {status: sum(q.status == status for q in rows)
                  for status in ("answered", "expired", "suppressed")}
        return {"open": sum(q.status == "open" for q in rows),
                "asked": sum(q.status != "suppressed" for q in rows),
                **counts, "last_ask_t": self.limiter.last_ask_t}
