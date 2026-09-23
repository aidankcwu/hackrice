"""The decider (docs/PERCEPTION.md, "Decider and writers").

One Jev request asks every action question at once and returns calibrated
probabilities (:class:`Verdict`). :class:`JevDecider` is the real call through
``typesafe-sdk``; :class:`FakeDecider` is the scripted stand-in for tests.

Jev cannot see a frame, so its input is a compact JSON state built from text
the pipeline already holds: the last few ticks' tags and captions, the open
episodes, today's summary, the persona and the seven-day trends. No frames, no
embeddings, no raw sensor numbers, no phash.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeError,
)

from longevity.ai_fields import BOOL_FIELDS

from ..models import Episode, Escalation, Tick
from . import envelope
from .decider_settings import DeciderSettings

__all__ = [
    "ACTIONS",
    "Decider",
    "DeciderError",
    "FakeDecider",
    "JevDecider",
    "Verdict",
    "build_questions",
    "build_state",
    "make_decider",
    "state_size_ok",
    "verdict_from_response",
]

log = logging.getLogger(__name__)

CAPTION_MAX = 120
OBJECT_MAX = 40
SUMMARY_MAX = 160
PERSONA_MAX = 1200
TRENDS_MAX = 1200

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _cut(text: object, limit: int) -> str:
    return str(text or "")[:limit]


def _hot(tick: Tick) -> list[str]:
    """``watch.hot`` read leniently: ``Tick`` has no ``watch`` field yet, so the
    block arrives as an extra (``extra="allow"``) and may be absent or malformed."""

    watch = getattr(tick, "watch", None) or (tick.model_extra or {}).get("watch")
    hot = watch.get("hot") if isinstance(watch, dict) else getattr(watch, "hot", None)
    return [_cut(name, OBJECT_MAX) for name in hot] if isinstance(hot, list) else []


def _tick_entry(tick: Tick, now: float) -> dict[str, Any]:
    age = round(now - tick.t, 1)
    if tick.ai is None or not tick.ai_fresh(envelope.AI_MAX_AGE_MS):
        return {"age_s": age, "true": [], "no_ai": True}
    ai = tick.ai
    return {
        "age_s": age,
        "true": [name for name in BOOL_FIELDS if getattr(ai, name, None) is True],
        "scene": tick.enum("scene", envelope.AI_MAX_AGE_MS),
        "activity": tick.enum("activity", envelope.AI_MAX_AGE_MS),
        "caption": _cut(ai.caption, CAPTION_MAX),
        "objects": [_cut(o, OBJECT_MAX) for o in ai.objects or []],
        "hot": _hot(tick),
    }


def _episode_label(ep: Episode) -> str:
    """Episode labels live in the db, not the model; fall back to its dominant tags."""

    label = getattr(ep, "label", None)
    if label:
        return _cut(label, OBJECT_MAX)
    return _cut(", ".join(f"{k} {v}" for k, v in ep.dominant.items()), OBJECT_MAX)


def state_size_ok(state: dict, budget_tokens: int = 3000) -> bool:
    """Rough token estimate: four characters of JSON per token."""

    return len(json.dumps(state)) / 4 <= budget_tokens


def build_state(
    esc: Escalation,
    window: Sequence[Tick],
    summary_lines: Sequence[str],
    open_episodes: Sequence[Episode],
    persona: str,
    seven_day: str,
    now: float,
    *,
    max_ticks: int = 6,
    max_summary: int = 12,
) -> dict:
    """The decider's JSON state. Never raises on size: over budget it drops the
    oldest ``today`` lines, then the oldest ``recent`` ticks, down to one of each."""

    local = datetime.fromtimestamp(now).astimezone()
    newest_first = sorted(window, key=lambda tk: tk.t, reverse=True)[:max(max_ticks, 0)]
    state: dict[str, Any] = {
        "trigger": {"name": esc.trigger, "reason": esc.reason},
        "recent": [_tick_entry(tk, now) for tk in newest_first],
        "episodes": [
            {
                "kind": ep.kind,
                "minutes_open": round((now - ep.start_t) / 60, 1),
                "label": _episode_label(ep),
            }
            for ep in open_episodes
        ],
        "today": [_cut(line, SUMMARY_MAX) for line in summary_lines][-max_summary:]
        if max_summary > 0 else [],
        "persona": _cut(persona, PERSONA_MAX),
        "trends": _cut(seven_day, TRENDS_MAX),
        "clock": {"local": local.strftime("%H:%M"), "weekday": _WEEKDAYS[local.weekday()]},
    }
    while not state_size_ok(state):
        if len(state["today"]) > 1:
            state["today"].pop(0)
        elif len(state["recent"]) > 1:
            state["recent"].pop()
        else:
            break
    return state


# --- The Jev request ------------------------------------------------------

ACTIONS: tuple[str, ...] = (
    "annotate", "log_insight", "remember", "watch", "speak", "ask", "act", "look",
)

#: One yes/no per action: (instructions, true, false). Written for a health
#: companion in the wearer's ear, so "false" always includes "would interrupt".
_ACTION_QUESTIONS: dict[str, tuple[str, str, str]] = {
    "annotate": (
        "Should this moment get a line in today's running summary?",
        "Worth one line in today's running summary",
        "Nothing new happened; the summary already covers it",
    ),
    "log_insight": (
        "Should this be logged as an insight in today's health report?",
        "This is a measurable fact worth putting in today's report",
        "Nothing measurable, or already logged today",
    ),
    "remember": (
        "Does this reveal something to remember about the wearer long term?",
        "This reveals a lasting fact about the wearer's habits or preferences",
        "A one-off moment that says nothing lasting about the wearer",
    ),
    "watch": (
        "Should the companion check on this again later?",
        "This is worth re-checking after a while",
        "Settled now; nothing to come back to",
    ),
    "speak": (
        "Should the companion say something in the wearer's ear right now?",
        "A short spoken remark right now would help the wearer make a healthier next choice",
        "Nothing worth saying, or it would interrupt the wearer",
    ),
    "ask": (
        "Should the companion ask the wearer a short question right now?",
        "Only the wearer can answer what this is, and a quick question now would settle it",
        "The answer is already clear, or a question would interrupt the wearer",
    ),
    "act": (
        "Should the phone take a concrete action right now?",
        "The phone should do something concrete now, such as adding a walk to the "
        "calendar or shielding apps at wind-down",
        "No action on the phone would help right now",
    ),
    "look": (
        "Would a closer look at the camera frame change the decision?",
        "A closer look at the current frame would settle what this is",
        "The text already makes clear what this is",
    ),
}


def build_questions(topics: Sequence[str]) -> dict[str, Question]:
    """Every question for one Jev request: a noul per action, the topic choice
    and the urgency score. Answers come back under the same keys."""

    questions: dict[str, Question] = {
        name: Noul(instructions=instr, criteria={"true": yes, "false": no})
        for name, (instr, yes, no) in _ACTION_QUESTIONS.items()
    }
    questions["topic"] = Choice(
        instructions="Which of the wearer's health topics is this moment about?",
        criteria={t: None for t in topics},
    )
    questions["urgency"] = Score(
        instructions="How soon does the wearer need to hear about this?",
        criteria=["can wait", "soon", "now"],
    )
    return questions


@dataclass(frozen=True)
class Verdict:
    """Jev's answers: one probability per action plus topic and urgency."""

    probabilities: dict[str, float]
    topic: str
    topic_confidence: float
    urgency: float
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0

    def fires(self, thresholds: Mapping[str, float]) -> list[str]:
        """Actions at or over their threshold, in ACTIONS order. ``annotate``
        always fires; an action with no threshold never does."""

        return [
            a for a in ACTIONS
            if a == "annotate"
            or (a in thresholds and self.probabilities.get(a, 0.0) >= thresholds[a])
        ]

    def uncertain(
        self,
        low: float,
        high: float,
        actions: Sequence[str] = ("speak", "ask", "act"),
    ) -> str | None:
        """The first of ``actions`` whose probability lies in [low, high]."""

        for a in actions:
            if low <= self.probabilities.get(a, 0.0) <= high:
                return a
        return None


class DeciderError(RuntimeError):
    """Any decider failure; the caller falls back to the clerk."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Decider(Protocol):
    async def decide(self, state: dict) -> Verdict: ...

    async def aclose(self) -> None: ...


def verdict_from_response(resp: SystemOneResponse, latency_ms: float) -> Verdict:
    """Map a System One response to a Verdict. A missing or wrong-typed answer
    raises DeciderError: a partial verdict would silently read as "no"."""

    answers = resp.answers

    def _get(name: str, kind: type) -> Any:
        ans = answers.get(name)
        if not isinstance(ans, kind):
            got = "missing" if ans is None else type(ans).__name__
            raise DeciderError(f"Jev answer {name!r}: expected {kind.__name__}, got {got}")
        return ans

    probabilities = {a: float(_get(a, NoulAnswer).noul) for a in ACTIONS}
    topic = _get("topic", ChoiceAnswer)
    urgency = _get("urgency", ScoreAnswer)
    usage = {
        k: v for k, v in (
            ("input_tokens", resp.usage.input_tokens),
            ("output_tokens", resp.usage.output_tokens),
        ) if v is not None
    }
    return Verdict(
        probabilities=probabilities,
        topic=topic.choice,
        topic_confidence=float(topic.confidence),
        urgency=float(urgency.score),
        model=resp.model,
        usage=usage,
        latency_ms=latency_ms,
    )


class JevDecider:
    """The real decider: one ``system_one`` call per escalation."""

    def __init__(self, settings: DeciderSettings) -> None:
        self.settings = settings
        self.questions = build_questions(settings.topics())
        try:
            self.client = AsyncTypeSafeClient(
                api_key=settings.typesafe_api_key,
                model=settings.jev_model,
                timeout=settings.jev_timeout_s,
            )
        except TypeSafeError as exc:
            raise DeciderError(f"Jev client: {exc}") from exc

    async def decide(self, state: dict) -> Verdict:
        start = time.perf_counter()
        try:
            # The SDK timeout is per attempt; this bounds retries too.
            resp = await asyncio.wait_for(
                self.client.system_one(state=state, questions=self.questions),
                timeout=self.settings.jev_timeout_s,
            )
        except (TypeSafeError, asyncio.TimeoutError) as exc:
            raise DeciderError(
                f"Jev {type(exc).__name__}: {exc}", status=getattr(exc, "status", None)
            ) from exc
        return verdict_from_response(resp, (time.perf_counter() - start) * 1000)

    async def aclose(self) -> None:
        await self.client.aclose()


class FakeDecider:
    """Scripted stand-in: a probability per action (unscripted ones 0.0), or a
    callable from state to Verdict. Records every state in ``calls``."""

    def __init__(
        self,
        scripted: Mapping[str, float] | Callable[[dict], Verdict] | None = None,
        topic: str = "other",
        urgency: float = 0.0,
        latency_ms: float = 1.0,
    ) -> None:
        self.scripted = scripted
        self.topic = topic
        self.urgency = urgency
        self.latency_ms = latency_ms
        self.calls: list[dict] = []

    async def decide(self, state: dict) -> Verdict:
        self.calls.append(state)
        if callable(self.scripted):
            return self.scripted(state)
        scripted = self.scripted or {}
        return Verdict(
            probabilities={a: float(scripted.get(a, 0.0)) for a in ACTIONS},
            topic=self.topic,
            topic_confidence=1.0,
            urgency=self.urgency,
            model="fake",
            usage={},
            latency_ms=self.latency_ms,
        )

    async def aclose(self) -> None:
        return None


def make_decider(settings: DeciderSettings) -> Decider | None:
    """The decider DECIDER names, or None for the clerk path."""

    if settings.decider != "jev":
        return None
    if not settings.typesafe_api_key:
        log.warning("DECIDER=jev but TYPESAFE_API_KEY is unset; using the clerk")
        return None
    return JevDecider(settings)
