"""The T1 structured response: JSON schema in, pydantic model out.

SPEC §4.1 -- one call returns interpretation *and* decision. SPEC §4.4 fixes the
action set; SPEC §4.5 makes ``annotate`` near-mandatory, which :func:`normalize`
enforces in code rather than trusting the model to remember.

The schema is written for the Responses API in ``strict`` mode, which demands
``additionalProperties: false`` on every object and every property listed in
``required``. Optional fields are therefore expressed as nullable unions
(``["integer", "null"]``), never by omission from ``required``.
"""

from __future__ import annotations

import time
from datetime import datetime
import math
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import FOOD_TYPES

__all__ = [
    "SpeakAction",
    "LogInsightAction",
    "AnnotateAction",
    "WatchAction",
    "AskAction",
    "RememberAction",
    "NothingAction",
    "Action",
    "T1Response",
    "AnswerParse",
    "T1_JSON_SCHEMA",
    "T1_TEXT_FORMAT",
    "ANSWER_JSON_SCHEMA",
    "ANSWER_TEXT_FORMAT",
    "SPEAK_DROPPED_FOR_ASK",
    "normalize",
]

Urgency = Literal["low", "normal", "high"]

ANNOTATE_MAX_CHARS = 80

#: A ``remember`` line is one durable fact, not a paragraph. Mirrors
#: ``Database.PROFILE_LINE_MAX_CHARS``; :func:`normalize` is where it is
#: enforced, because the schema cannot express a length.
REMEMBER_MAX_CHARS = 160

#: ``AnswerParse.note`` is one line of what the wearer said in effect.
NOTE_MAX_CHARS = 80

#: ``AnswerParse.followup`` is one more question, spoken aloud.
FOLLOWUP_MAX_CHARS = 120

#: ``AnswerParse.count`` is servings *today* (docs/ASK_DESIGN.md §8.8).
COUNT_MAX = 20.0

#: Reason string for the speak that :func:`normalize` drops when the same
#: response also asks (docs/ASK_DESIGN.md §8.6). The drop happens here, in
#: code the model cannot argue with; the *logging* of it belongs to the action
#: handler, which is the only layer that knows the decision id.
SPEAK_DROPPED_FOR_ASK = "speak_dropped_for_ask"


class _ActionBase(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SpeakAction(_ActionBase):
    """A hand-off to the voice agent, leaning statement.

    Was an utterance proposal (SPEC §4.6); since
    docs/CONVERSATION_DESIGN.md §1 the ``text`` is a topic and a reason in
    plain words and the voice agent writes the sentence. The field keeps its
    name because the action set is a closed schema the model is trained on
    within a single prompt, and renaming it would cost more than it explains.
    """

    type: Literal["speak"] = "speak"
    text: str
    urgency: Urgency = "low"


class LogInsightAction(_ActionBase):
    """A persistent record feeding daily and weekly reports."""

    type: Literal["log_insight"] = "log_insight"
    category: str = "general"
    text: str


class AnnotateAction(_ActionBase):
    """One line appended to today's summary -- part 4 of the next envelope."""

    type: Literal["annotate"] = "annotate"
    line: str


class WatchAction(_ActionBase):
    """A pending-checks row the trigger gate polls (SPEC §4.4)."""

    type: Literal["watch"] = "watch"
    after_s: int | None = None
    condition: str | None = None
    reason: str = ""


class AskAction(_ActionBase):
    """A hand-off to the voice agent, leaning question (ASK_DESIGN §5).

    The clerk proposes the topic; the voice agent writes the question and
    :class:`~pipeline.actions.questions.QuestionManager` delivers it. ``fills``
    names the primary field the answer is expected to set, which is still how
    the answer reaches ``reported``.
    """

    type: Literal["ask"] = "ask"
    text: str
    answer_kind: Literal["yes_no", "count", "free"] = "yes_no"
    fills: Literal["confirmed", "count", "food_type", "note"] = "confirmed"
    reason: str = ""


class RememberAction(_ActionBase):
    """One durable fact about the wearer, added to the persona (Part A).

    Distinct from ``annotate``, which is about *today* and is read back for one
    day, and from ``log_insight``, which is a health observation for a report.
    A ``remember`` line is about *who the wearer is* -- a preference, a habit, a
    person, a place, a routine -- and goes into every future system prompt.
    """

    type: Literal["remember"] = "remember"
    line: str


class NothingAction(_ActionBase):
    type: Literal["nothing"] = "nothing"


Action = Annotated[
    Union[
        SpeakAction,
        LogInsightAction,
        AnnotateAction,
        WatchAction,
        AskAction,
        RememberAction,
        NothingAction,
    ],
    Field(discriminator="type"),
]


class T1Response(BaseModel):
    """What one escalation's model call returns."""

    model_config = ConfigDict(extra="ignore")

    interpretation: str = ""
    confidence: float = 0.0
    actions: list[Action] = Field(default_factory=list)

    def of_type(self, kind: str) -> list[Action]:
        return [a for a in self.actions if a.type == kind]


# -- JSON schema (Responses API, strict) ---------------------------------


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    """A strict-mode object: closed, with every property required."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_SPEAK = _obj(
    {
        "type": {"type": "string", "enum": ["speak"]},
        "text": {
            "type": "string",
            "description": "The TOPIC and the REASON in plain words, for the "
            "voice agent that owns the mouth -- not the sentence to say. "
            '"picked up a wine glass, ownership unknown".',
        },
        "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
    }
)

_LOG_INSIGHT = _obj(
    {
        "type": {"type": "string", "enum": ["log_insight"]},
        "category": {
            "type": "string",
            "description": "diet, sleep, screen, social, nature, movement, "
            "alcohol, caffeine, or another short lowercase tag.",
        },
        "text": {"type": "string"},
    }
)

_ANNOTATE = _obj(
    {
        "type": {"type": "string", "enum": ["annotate"]},
        "line": {
            "type": "string",
            "description": "One short memory line for today's summary.",
        },
    }
)

_WATCH = _obj(
    {
        "type": {"type": "string", "enum": ["watch"]},
        "after_s": {
            "type": ["integer", "null"],
            "description": "Re-check this many seconds from now, or null to "
            "wait on the condition alone.",
        },
        "condition": {
            "type": ["string", "null"],
            "description": "Plain-language condition to re-check on, or null.",
        },
        "reason": {"type": "string"},
    }
)

_ASK = _obj(
    {
        "type": {"type": "string", "enum": ["ask"]},
        "text": {
            "type": "string",
            "description": "The TOPIC and the REASON in plain words, for the "
            "voice agent -- not the question itself. It writes the wording.",
        },
        "answer_kind": {
            "type": "string",
            "enum": ["yes_no", "count", "free"],
            "description": "The shape of the answer you expect back.",
        },
        "fills": {
            "type": "string",
            "enum": ["confirmed", "count", "food_type", "note"],
            "description": "The field the answer is primarily meant to settle.",
        },
        "reason": {
            "type": "string",
            "description": "Why the frames cannot settle this on their own.",
        },
    }
)

_REMEMBER = _obj(
    {
        "type": {"type": "string", "enum": ["remember"]},
        "line": {
            "type": "string",
            "description": "One short durable fact about the wearer -- a "
            "preference, a habit, a person, a place, a routine -- learned from "
            "an answer or a repeated pattern. Not today's events.",
        },
    }
)

_NOTHING = _obj({"type": {"type": "string", "enum": ["nothing"]}})

T1_JSON_SCHEMA: dict[str, Any] = _obj(
    {
        "interpretation": {
            "type": "string",
            "description": "What is happening, in one specific clause.",
        },
        "confidence": {
            "type": "number",
            "description": "0..1 confidence in the interpretation.",
        },
        "actions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "anyOf": [
                    _SPEAK,
                    _LOG_INSIGHT,
                    _ANNOTATE,
                    _WATCH,
                    _ASK,
                    _REMEMBER,
                    _NOTHING,
                ],
            },
        },
    }
)

#: Pass as ``text=T1_TEXT_FORMAT`` to ``responses.create``.
T1_TEXT_FORMAT: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "name": "t1_decision",
        "strict": True,
        "schema": T1_JSON_SCHEMA,
    }
}


# -- the answer parse (ASK_DESIGN §5, §8.8) -------------------------------


class AnswerParse(BaseModel):
    """What the wearer's spoken answer amounts to, in fields code can store.

    The validation here is the contract of §8.8, not a formality: the parser
    is a language model and this is the only place that decides a "count" of
    ``-3`` or a ``food_type`` of ``"latte"`` is not a fact about the day. An
    out-of-range or unknown value becomes ``None`` -- silently dropping a
    field is right, because the alternative is writing a number nobody said.
    """

    model_config = ConfigDict(extra="ignore")

    understood: bool = False
    confirmed: bool | None = None
    count: float | None = None
    food_type: str | None = None
    note: str = ""
    followup: str | None = None

    @field_validator("count", mode="before")
    @classmethod
    def _clean_count(cls, value: Any) -> float | None:
        """Finite and 0..20 servings today, or nothing at all."""

        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            # ``OverflowError`` is not hypothetical: a model that writes
            # ``10**400`` into the JSON parses to a Python int no float can
            # hold, and an unhandled raise here would take down the answer.
            return None
        if not math.isfinite(number) or number < 0.0 or number > COUNT_MAX:
            return None
        return number

    @field_validator("food_type", mode="before")
    @classmethod
    def _clean_food_type(cls, value: Any) -> str | None:
        """One of the §9 menu values, or nothing -- never an invented food."""

        if not isinstance(value, str):
            return None
        candidate = value.strip().lower().replace(" ", "_").replace("-", "_")
        if candidate in ("", "none", "null", "unknown"):
            return None
        return candidate if candidate in FOOD_TYPES else None

    @field_validator("note", mode="before")
    @classmethod
    def _clean_note(cls, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return text[:NOTE_MAX_CHARS].rstrip()

    @field_validator("followup", mode="before")
    @classmethod
    def _clean_followup(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:FOLLOWUP_MAX_CHARS].rstrip()

    @model_validator(mode="after")
    def _not_understood_is_note_only(self) -> "AnswerParse":
        """``understood=false`` carries a note and nothing else (§8.8).

        A parse that did not understand the answer has no standing to set a
        field: whatever ``confirmed`` or ``count`` it guessed came from a
        sentence it just admitted it could not read. The note survives
        because "what the wearer said in effect" is still worth keeping.
        """

        if not self.understood:
            self.confirmed = None
            self.count = None
            self.food_type = None
            self.followup = None
        return self


ANSWER_JSON_SCHEMA: dict[str, Any] = _obj(
    {
        "understood": {
            "type": "boolean",
            "description": "True when the transcript answers the question at all.",
        },
        "confirmed": {
            "type": ["boolean", "null"],
            "description": "True when the wearer says the item is theirs AND "
            "they are having it; false when it is not theirs or they are not "
            "having it; null when they did not say.",
        },
        "count": {
            "type": ["number", "null"],
            "description": "Servings of this item today, 0 to 20, or null.",
        },
        "food_type": {
            "type": ["string", "null"],
            "description": "One of the fixed food_type values, or null. "
            "Never invent a value: " + ", ".join(FOOD_TYPES) + ".",
        },
        "note": {
            "type": "string",
            "description": "What the wearer said in effect, 80 characters or less.",
        },
        "followup": {
            "type": ["string", "null"],
            "description": "One more spoken question, or null. Only when a yes "
            "still leaves the quantity unknown.",
        },
    }
)

#: Pass as ``text=ANSWER_TEXT_FORMAT`` to ``responses.create``.
ANSWER_TEXT_FORMAT: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "name": "answer_parse",
        "strict": True,
        "schema": ANSWER_JSON_SCHEMA,
    }
}


# -- normalisation --------------------------------------------------------


def _hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().strftime("%H:%M")


def normalize(resp: T1Response, t: float | None = None) -> T1Response:
    """Enforce the invariants code owns rather than the model.

    * ``annotate`` is always present (SPEC §4.5 "always write"). A missing one
      is synthesised from the interpretation.
    * a ``remember`` line is trimmed to ``REMEMBER_MAX_CHARS`` and an empty
      one is dropped.
    * ``nothing`` is dropped when any other action was chosen -- "no action"
      alongside an action is a contradiction, and the model does emit it.
    * ``confidence`` is clamped to 0..1.
    * an ``ask`` and a ``speak`` in the same response is a contradiction --
      the question wins and the statement is dropped (ASK_DESIGN §8.6), so
      nothing talks over the answer window. The handler logs the drop as
      ``SPEAK_DROPPED_FOR_ASK``; this function only removes it.
    """

    stamp = time.time() if t is None else t
    actions = list(resp.actions)

    # A speak whose text is empty, a bare "nothing", or a JSON-looking blob is
    # the model trying to stay silent the wrong way (seen live: ElevenLabs
    # read '{"type":"nothing"}' aloud). Silence means no speak action at all.
    # An `ask` is spoken by the same mouth, so it is held to the same bar.
    def _sayable(a: Any) -> bool:
        kind = getattr(a, "type", None)
        if kind not in ("speak", "ask"):
            return True
        text = (getattr(a, "text", "") or "").strip()
        if len(text) < 2 or text.lower() in {"nothing", "none", "null", "silent"}:
            return False
        if text[0] in "{[" or '"type"' in text:
            return False
        return True

    actions = [a for a in actions if _sayable(a)]

    # A `remember` line is capped here rather than in the schema, which cannot
    # express a length, and an empty one is dropped outright: a blank fact
    # would still take a slot in every future system prompt.
    capped: list[Any] = []
    for action in actions:
        if getattr(action, "type", None) != "remember":
            capped.append(action)
            continue
        line = (getattr(action, "line", "") or "").strip()
        if not line:
            continue
        capped.append(RememberAction(line=line[:REMEMBER_MAX_CHARS].rstrip()))
    actions = capped

    if any(a.type == "ask" for a in actions):
        actions = [a for a in actions if a.type != "speak"]

    if any(a.type != "nothing" for a in actions):
        actions = [a for a in actions if a.type != "nothing"]

    if not any(a.type == "annotate" for a in actions):
        text = (resp.interpretation or "nothing worth saying").strip()
        if len(text) > ANNOTATE_MAX_CHARS:
            text = text[:ANNOTATE_MAX_CHARS].rstrip()
        actions.append(AnnotateAction(line=text))

    confidence = min(1.0, max(0.0, float(resp.confidence)))
    return T1Response(
        interpretation=resp.interpretation,
        confidence=confidence,
        actions=actions,
    )
