"""Model clients for T1.

SPEC §11.6 puts the reasoner on the OpenAI Responses API with structured JSON
output and interleaved multi-image input. :class:`OpenAIReasonerClient` is that;
:class:`FakeReasonerClient` is a deterministic stand-in so the whole pipeline --
gate, reasoner, actions, dashboard -- runs with no API key and no latency.

The fake is not a mock in the test-double sense: it reads the same envelope the
real model reads and returns a plausible response, so an end-to-end demo run
without a key still produces a decision feed.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from ..config import Settings
from ..models import FOOD_TYPES, Episode, PendingQuestion
from .prompts import ANSWER_OBJECTIVE
from .schema import (
    ANSWER_TEXT_FORMAT,
    AnnotateAction,
    AnswerParse,
    AskAction,
    LogInsightAction,
    SpeakAction,
    T1_TEXT_FORMAT,
    T1Response,
    WatchAction,
)

log = logging.getLogger(__name__)

__all__ = [
    "ReasonerClient",
    "OpenAIReasonerClient",
    "FakeReasonerClient",
    "make_client",
    "AnswerParser",
    "OpenAIAnswerParser",
    "FakeAnswerParser",
    "make_answer_parser",
]

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


@runtime_checkable
class ReasonerClient(Protocol):
    """One structured call per escalation."""

    async def complete(
        self, input_messages: list[dict[str, Any]]
    ) -> tuple[T1Response, dict[str, Any]]:
        """Return the parsed response plus meta (``model``, ``latency_ms``, ``usage``)."""
        ...


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


class OpenAIReasonerClient:
    """The real T1 call: Responses API, strict JSON schema, inline images."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float | None = None,
        client: Any | None = None,
        reasoning_effort: str | None = "minimal",
    ) -> None:
        if not api_key:
            raise RuntimeError("OpenAIReasonerClient requires an API key")
        self.model = model
        self.reasoning_effort = reasoning_effort
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete(
        self, input_messages: list[dict[str, Any]]
    ) -> tuple[T1Response, dict[str, Any]]:
        started = time.perf_counter()
        kwargs: dict[str, Any] = dict(
            model=self.model, input=input_messages, text=T1_TEXT_FORMAT
        )
        # T1 is a perception + decision call, not a puzzle: low reasoning
        # effort cuts latency. Retry without it for models that reject it.
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = await self._client.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if "reasoning" in kwargs and "reasoning" in str(exc).lower():
                kwargs.pop("reasoning")
                response = await self._client.responses.create(**kwargs)
            else:
                raise
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = getattr(response, "output_text", None) or ""

        parsed = self._parse(raw)

        meta: dict[str, Any] = {
            "model": getattr(response, "model", None) or self.model,
            "latency_ms": latency_ms,
            "usage": self._usage(response),
            "raw_len": len(raw),
        }
        return parsed, meta

    # -- parsing ---------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> T1Response:
        """Parse ``output_text``; retry once after stripping code fences.

        Structured output should never need the retry. It costs nothing and a
        demo is not the place to discover that a model wrapped its JSON.
        """

        try:
            return T1Response.model_validate(json.loads(raw))
        except Exception as first:
            log.warning("T1 response did not parse (%s); retrying de-fenced", first)
            return T1Response.model_validate(json.loads(_strip_fences(raw)))

    @staticmethod
    def _usage(response: Any) -> dict[str, Any] | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        if hasattr(usage, "model_dump"):
            try:
                return usage.model_dump()
            except Exception:  # pragma: no cover - defensive
                return None
        return dict(usage) if isinstance(usage, dict) else None


# -- the fake -------------------------------------------------------------

_TRIGGER_RE = re.compile(r"^Trigger:\s*(\S+)\s+at\s+(\d{2}):(\d{2}):(\d{2})")
#: Every `food_type` the VLM can report, in menu order, minus the null value.
#: Derived rather than retyped: the fake has to recognise `rice_bowl` the day
#: the menu grows one, or a key-less demo silently stops naming the meal.
_FOOD_WORDS = tuple(value for value in FOOD_TYPES if value != "none")

#: Local hour at or after which caffeine is worth mentioning (bedtime - 9 h).
CAFFEINE_HOUR = 14

#: The gate's wearable line, e.g.
#: ``Heart rate (wearable, bpm) over the last 20s, resting 58: t-20s 96, ...``.
_HR_LINE_RE = re.compile(r"Heart rate \(wearable[^:]*resting\s+(\d+(?:\.\d+)?)\s*:")
_HR_POINT_RE = re.compile(r"t-\d+s\s+(\d+(?:\.\d+)?)")

#: Columns in the envelope's tick table (envelope._COLUMNS).
_TABLE_COLS = 11
_ACTIVITY_COL = 2
_SCENE_COL = 1
_PEOPLE_COL = 5


class FakeReasonerClient:
    """Deterministic rules over the envelope text. Zero latency, no key.

    Reads the trigger line and the tick table exactly where the real model
    would, so the envelope stays under test even on key-less runs.
    """

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.last_envelope: list[dict[str, Any]] | None = None

    async def complete(
        self, input_messages: list[dict[str, Any]]
    ) -> tuple[T1Response, dict[str, Any]]:
        self.calls += 1
        self.last_envelope = input_messages
        started = time.perf_counter()
        text = self._user_text(input_messages)
        trigger, hour = self._trigger(text)
        resp = self._decide(trigger, hour, text)
        meta = {
            "model": self.model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "usage": None,
        }
        return resp, meta

    # -- envelope reading ------------------------------------------------

    @staticmethod
    def _user_text(input_messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for message in input_messages:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
                continue
            for item in content or []:
                if item.get("type") == "input_text":
                    parts.append(str(item.get("text", "")))
        return "\n".join(parts)

    @staticmethod
    def _trigger(text: str) -> tuple[str, int]:
        for line in text.splitlines():
            m = _TRIGGER_RE.match(line)
            if m:
                return m.group(1), int(m.group(2))
        return "unknown", datetime.now().hour

    @staticmethod
    def _clock(text: str) -> str:
        """``HH:MM`` from the trigger line, for an insight that cites a time."""

        for line in text.splitlines():
            m = _TRIGGER_RE.match(line)
            if m:
                return f"{m.group(2)}:{m.group(3)}"
        return datetime.now().strftime("%H:%M")

    @staticmethod
    def _table_rows(text: str) -> list[list[str]]:
        """The tick-table body, one token list per row.

        Frame labels also start with ``t-`` but carry an em dash and commas;
        table rows are exactly ten whitespace-separated cells.
        """

        rows: list[list[str]] = []
        for line in text.split("Tick table", 1)[-1].splitlines():
            if "," in line:
                continue
            cells = line.split()
            if len(cells) == _TABLE_COLS and cells[0].startswith("t-"):
                rows.append(cells)
        return rows

    @classmethod
    def _context(cls, text: str) -> tuple[str | None, str, bool]:
        """Latest known scene, latest known activity, and whether people showed."""

        rows = cls._table_rows(text)
        scene = next(
            (r[_SCENE_COL] for r in reversed(rows) if r[_SCENE_COL] != "?"), None
        )
        activity = next(
            (r[_ACTIVITY_COL] for r in reversed(rows) if r[_ACTIVITY_COL] != "?"),
            "seated",
        )
        people = any(r[_PEOPLE_COL] == "y" for r in rows)
        return scene, activity, people

    @staticmethod
    def _hr_numbers(text: str) -> tuple[float | None, float | None]:
        """Peak bpm and resting bpm off the gate's extra line, if it is there."""

        for line in text.splitlines():
            m = _HR_LINE_RE.search(line)
            if not m:
                continue
            points = [float(v) for v in _HR_POINT_RE.findall(line)]
            if points:
                return max(points), float(m.group(1))
        return None, None

    @staticmethod
    def _food_type(text: str) -> str | None:
        table = text.split("Tick table", 1)[-1]
        for word in _FOOD_WORDS:
            if re.search(rf"\b{word}\b", table):
                return word.replace("_", " ")
        return None

    # -- rules -----------------------------------------------------------

    def _decide(self, trigger: str, hour: int, text: str) -> T1Response:
        # Annotate lines carry no time prefix; the store stamps `t` and the
        # dashboard renders it (a fake HH:00 stamp double-prefixed on screen).
        base = trigger.split(":", 1)[0]

        if any(line.startswith("Keyword trigger ") for line in text.splitlines()):
            spoken_name = base.replace("_", " ")
            return T1Response(
                interpretation=f"keyword trigger: {spoken_name}",
                confidence=0.75,
                actions=[
                    AnnotateAction(line=f"{spoken_name} in frame"),
                    SpeakAction(text=f"{spoken_name} again?", urgency="low"),
                ],
            )

        if base == "food_in_frame":
            food = self._food_type(text)
            interpretation = f"meal, {food}" if food else "meal"
            return T1Response(
                interpretation=interpretation,
                confidence=0.72,
                actions=[
                    AnnotateAction(line=f"{interpretation}"),
                    LogInsightAction(
                        category="diet",
                        text=f"Meal logged ({food or 'type unclear'}); "
                        "tag against the Mediterranean pattern.",
                    ),
                ],
            )

        if base == "answer":
            # A wake-up that is itself the wearer's reply. The parse already
            # happened (`Reasoner.try_answer`); all that is left is the memory
            # line, and asking again here would loop (ASK_DESIGN §4).
            return T1Response(
                interpretation="wearer answered",
                confidence=0.6,
                actions=[AnnotateAction(line="wearer answered")],
            )

        if base == "caffeine_seen":
            actions: list[Any] = [
                AnnotateAction(line=f"caffeine in frame"),
            ]
            if hour >= CAFFEINE_HOUR:
                actions.append(
                    WatchAction(after_s=60, reason="check for more caffeine")
                )
                actions.append(
                    SpeakAction(
                        text="Coffee this late may cost you sleep tonight.",
                        urgency="low",
                    )
                )
            else:
                # Before the cutoff there is nothing to say, so the mouth is
                # free to ask instead. After it, the fake has a statement to
                # make and `normalize` would drop it for an ask (§8.6).
                actions.append(
                    AskAction(
                        text="Is that coffee yours?",
                        answer_kind="yes_no",
                        fills="confirmed",
                        reason="a cup in frame is not a cup being drunk",
                    )
                )
            return T1Response(
                interpretation="caffeine in frame",
                confidence=0.7,
                actions=actions,
            )

        if base == "alcohol_seen":
            return T1Response(
                interpretation="alcohol in frame",
                confidence=0.7,
                actions=[
                    AnnotateAction(line=f"alcohol in frame"),
                    AskAction(
                        text="That yours?",
                        answer_kind="yes_no",
                        fills="confirmed",
                        reason="the frames cannot say whose drink it is",
                    ),
                    LogInsightAction(
                        category="alcohol",
                        text="Alcohol sighting; expect a nightly HRV drop.",
                    ),
                ],
            )

        if base == "screen_sustained":
            return T1Response(
                interpretation="sustained screen time",
                confidence=0.68,
                actions=[
                    AnnotateAction(line=f"sustained screen time"),
                    WatchAction(after_s=120, reason="still at screen?"),
                ],
            )

        if base == "people_sustained":
            return T1Response(
                interpretation="conversation with people present",
                confidence=0.66,
                actions=[
                    AnnotateAction(line=f"people present, conversation"),
                    LogInsightAction(
                        category="social",
                        text="Social episode; counts toward daily integration.",
                    ),
                ],
            )

        if base == "outdoor_sustained":
            return T1Response(
                interpretation="outdoors, greenery in frame",
                confidence=0.66,
                actions=[
                    AnnotateAction(line=f"outdoors"),
                    LogInsightAction(
                        category="nature",
                        text="Outdoor block; counts toward the weekly nature dose.",
                    ),
                ],
            )

        if base == "biometric_anomaly":
            # The wearable gives the number, the frames give the cause
            # (SPEC §14.3) -- so read both and cite them together.
            hr, rest = self._hr_numbers(text)
            scene, activity, people = self._context(text)
            seen = ", ".join(
                bit for bit in (scene, "people present" if people else None) if bit
            ) or "no clear scene"
            numbers = (
                f"HR {hr:.0f} (resting {rest:.0f})"
                if hr is not None and rest is not None
                else "HR elevated"
            )
            return T1Response(
                interpretation=f"elevated heart rate while {activity}; "
                f"frames show {seen}",
                confidence=0.64,
                actions=[
                    AnnotateAction(line=f"{numbers} while {activity}"),
                    LogInsightAction(
                        category="stress",
                        text=f"{self._clock(text)} {numbers}, {activity}, "
                        f"frames show {seen}",
                    ),
                ],
            )

        if base == "watch":
            return T1Response(
                interpretation="scheduled re-check",
                confidence=0.6,
                actions=[AnnotateAction(line=f"re-check: {trigger}")],
            )

        return T1Response(
            interpretation=f"{base.replace('_', ' ')}, nothing worth saying",
            confidence=0.5,
            actions=[AnnotateAction(line=f"{base.replace('_', ' ')}")],
        )


# -- the answer parser (ASK_DESIGN §8.11) ---------------------------------


@runtime_checkable
class AnswerParser(Protocol):
    """One structured call per spoken answer."""

    async def parse(
        self,
        question: PendingQuestion,
        transcript: str,
        episode: Episode | None,
    ) -> AnswerParse:
        """Turn what the wearer said into the fields of §8.8."""
        ...


def _answer_messages(
    question: PendingQuestion, transcript: str, episode: Episode | None
) -> list[dict[str, Any]]:
    """The whole prompt: what was asked, what was seen, what was heard."""

    lines = [
        f"Question asked: {question.question}",
        f"Answer kind: {question.answer_kind}. Field it is for: {question.fills}.",
    ]
    if episode is not None:
        seen = ", ".join(f"{k}={v}" for k, v in sorted(episode.dominant.items()))
        lines.append(
            f"What the camera had established: {episode.kind}"
            f"{', ' + seen if seen else ''}."
        )
    heard = transcript.strip()
    lines.append(f"Transcript: {heard if heard else '(nothing heard)'}")
    return [
        {"role": "system", "content": ANSWER_OBJECTIVE},
        {"role": "user", "content": "\n".join(lines)},
    ]


class OpenAIAnswerParser:
    """The real parse: Responses API, strict JSON schema, text only.

    No frames. The question already carries what the camera saw; the only new
    evidence is the sentence, and a second image call would spend the latency
    budget the wearer is standing there waiting through.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float | None = 10.0,
        client: Any | None = None,
        reasoning_effort: str | None = "minimal",
    ) -> None:
        if not api_key:
            raise RuntimeError("OpenAIAnswerParser requires an API key")
        self.model = model
        self.reasoning_effort = reasoning_effort
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def parse(
        self,
        question: PendingQuestion,
        transcript: str,
        episode: Episode | None = None,
    ) -> AnswerParse:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            input=_answer_messages(question, transcript, episode),
            text=ANSWER_TEXT_FORMAT,
        )
        # Same trade as T1: this is comprehension, not a puzzle, and the
        # wearer is waiting. Retry without it for models that reject it.
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = await self._client.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if "reasoning" in kwargs and "reasoning" in str(exc).lower():
                kwargs.pop("reasoning")
                response = await self._client.responses.create(**kwargs)
            else:
                raise
        return self._parse(getattr(response, "output_text", None) or "")

    @staticmethod
    def _parse(raw: str) -> AnswerParse:
        try:
            return AnswerParse.model_validate(json.loads(raw))
        except Exception as first:
            log.warning("answer parse failed (%s); retrying de-fenced", first)
            return AnswerParse.model_validate(json.loads(_strip_fences(raw)))


# -- the fake parser ------------------------------------------------------

#: Every way the wearer says "no" -- refusal, disowning, or owning the item
#: while declining to consume it. Checked *first* and on its own, because a
#: denial almost always arrives wrapped in affirmative words: "mine but I am
#: not drinking it" contains "mine", "no it is not" contains "it is", and
#: reading either as a yes writes a drink into the day that nobody had.
#: §8.8 is explicit that ownership without consumption is `confirmed=false`.
_NEGATION_RE = re.compile(
    r"\b("
    r"no|nope|nah|negative|not really|"
    r"not mine|isn'?t mine|not my|someone else|somebody else|"
    r"not drinking|not having|not eating|not touching|"
    r"didn'?t|don'?t|doesn'?t|isn'?t|wasn'?t|won'?t|"
    r"it is not|it'?s not|that is not|that'?s not|"
    r"my (?:friend|roommate|colleague|brother|sister)'?s?"
    r")\b",
    re.IGNORECASE,
)
#: Only consulted when nothing above matched.
_YES_RE = re.compile(
    r"\b(yes|yeah|yeh|yep|yup|sure|mine|correct|it is|i am|i did)\b",
    re.IGNORECASE,
)
#: A number about some *other* day is not a count of servings today (§8.8),
#: and the fake has no calendar to place it on -- so it declines to count.
_TEMPORAL_RE = re.compile(
    r"\b(yesterday|last night|last week|last month|last year|"
    r"tomorrow|earlier this week|the other day)\b",
    re.IGNORECASE,
)
#: A bare non-negative number: not part of a word, not the tail of "-3",
#: not "3pm". A sign or a suffix means the digits were doing another job.
_DIGIT_RE = re.compile(r"(?<![\w.\-])(\d+(?:\.\d+)?)(?![\w.])")
_NUMBER_WORDS = {
    "zero": 0.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0,
    "ten": 10.0,
}
_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE
)
#: ``rice_bowl`` has to match "rice bowl" as spoken, not as enumerated.
_FOOD_RES = tuple(
    (value, re.compile(r"\b" + value.replace("_", "[ _-]") + r"s?\b", re.IGNORECASE))
    for value in FOOD_TYPES
    if value != "none"
)

#: What the fake asks when a yes leaves the quantity open (§8.5).
FAKE_FOLLOWUP = "How many today?"


class FakeAnswerParser:
    """Regex over the transcript. Zero latency, no key, same interface.

    Good enough that a key-less demo run answers "yeah two" correctly, and
    honest about the rest: anything it cannot place becomes a note, never a
    guessed field.
    """

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def parse(
        self,
        question: PendingQuestion,
        transcript: str,
        episode: Episode | None = None,
    ) -> AnswerParse:
        self.calls += 1
        text = (transcript or "").strip()
        if not text:
            return AnswerParse(understood=False, note="")

        # Speech-to-text hands back typographic apostrophes; "didn\u2019t" has
        # to read as "didn't" or every contraction below silently misses.
        # Matching happens on the normalised copy, the note keeps the original.
        probe = text.replace("\u2019", "'")

        # Negation wins outright, and only a transcript with no negation in
        # it at all is read for a yes. A sentence holding both is a no.
        confirmed: bool | None = None
        if _NEGATION_RE.search(probe):
            confirmed = False
        elif _YES_RE.search(probe):
            confirmed = True

        count = self._count(probe)
        food_type = self._food_type(probe)

        followup = None
        if question.answer_kind == "yes_no" and confirmed and count is None:
            followup = FAKE_FOLLOWUP

        return AnswerParse(
            understood=True,
            confirmed=confirmed,
            count=count,
            food_type=food_type,
            note=text,
            followup=followup,
        )

    @staticmethod
    def _count(text: str) -> float | None:
        """A count of servings *today*, or nothing.

        Conservative on purpose: "two yesterday" is a number, but not a
        number about today, and the fake would rather record no count than
        the wrong day's.
        """

        if _TEMPORAL_RE.search(text):
            return None
        digits = _DIGIT_RE.search(text)
        if digits:
            return float(digits.group(1))
        word = _NUMBER_WORD_RE.search(text)
        if word:
            return _NUMBER_WORDS[word.group(1).lower()]
        return None

    @staticmethod
    def _food_type(text: str) -> str | None:
        for value, pattern in _FOOD_RES:
            if pattern.search(text):
                return value
        return None


# -- factory --------------------------------------------------------------


def make_client(
    settings: Settings, mode: Literal["openai", "fake"]
) -> ReasonerClient:
    """Build a client. ``mode`` is explicit -- there is no silent fallback.

    A key-less ``openai`` run raises rather than quietly degrading to the fake:
    discovering mid-demo that every decision came from a rule table is worse
    than failing at startup.
    """

    if mode == "fake":
        return FakeReasonerClient()
    if mode != "openai":
        raise ValueError(f"unknown reasoner client mode: {mode!r}")
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set; use mode='fake' for a key-less run"
        )
    return OpenAIReasonerClient(settings.openai_api_key, settings.t1_model)


def make_answer_parser(
    settings: Settings, mode: Literal["openai", "fake"]
) -> AnswerParser:
    """Build an answer parser. ``mode`` is explicit, exactly as for T1."""

    if mode == "fake":
        return FakeAnswerParser()
    if mode != "openai":
        raise ValueError(f"unknown answer parser mode: {mode!r}")
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set; use mode='fake' for a key-less run"
        )
    return OpenAIAnswerParser(settings.openai_api_key, settings.t1_model)
