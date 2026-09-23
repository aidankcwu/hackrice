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

import logging
import time
from datetime import datetime
import math
from typing import Annotated, Any, Literal, Union

from longevity.ai_fields import BOOL_FIELDS, TRISTATE_BOOL_FIELDS
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..actions.sound import SOUND_NAMES, drop_sound_if_speaking
from ..models import FOOD_TYPES

log = logging.getLogger(__name__)

__all__ = [
    "SpeakAction",
    "LogInsightAction",
    "AnnotateAction",
    "WatchAction",
    "AskAction",
    "RememberAction",
    "LookAction",
    "ActAction",
    "NothingAction",
    "Action",
    "T1Response",
    "AnswerParse",
    "T1_JSON_SCHEMA",
    "T1_TEXT_FORMAT",
    "ANSWER_JSON_SCHEMA",
    "ANSWER_TEXT_FORMAT",
    "SPEAK_DROPPED_FOR_ASK",
    "LOOK_MIN_CHARS",
    "LOOK_MAX_CHARS",
    "WATCH_CONCEPTS",
    "WATCH_WITHIN_DEFAULT_S",
    "WATCH_WITHIN_MIN_S",
    "WATCH_WITHIN_MAX_S",
    "WATCH_CONCEPT_UNKNOWN",
    "normalize",
]

Urgency = Literal["low", "normal", "high"]
Deliver = Literal["now", "quiet"]

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

#: A ``look`` question is one short question of the current frame
#: (docs/PERCEPTION.md "Labeler" 4). Mirrors ``writers.LOOK_MAX_CHARS``.
LOOK_MIN_CHARS = 2
LOOK_MAX_CHARS = 120

#: What a ``watch`` may arm the watcher on (docs/PERCEPTION.md "Gate and
#: actions"): the §9 booleans, which are exactly the concepts the watcher
#: scores. Read from ``ai_fields`` so the field set still lives in one place.
WATCH_CONCEPTS: frozenset[str] = frozenset(BOOL_FIELDS) | frozenset(TRISTATE_BOOL_FIELDS)
#: ``within_s`` bounds for an armed watch, and the default when a concept is
#: given without one. Ten seconds is the shortest arming that outlives the
#: labeler round trip; two hours is the longest the day's context stays valid.
WATCH_WITHIN_MIN_S = 10
WATCH_WITHIN_MAX_S = 7200
WATCH_WITHIN_DEFAULT_S = 600
#: Log reason for a ``watch`` whose concept is not a §9 boolean: the action is
#: dropped by :func:`normalize`, since the watcher could never score it.
WATCH_CONCEPT_UNKNOWN = "watch_concept_unknown"


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
    #: ``now`` hands off at once; ``quiet`` waits for a quiet tick, up to
    #: ``quiet_max_s`` (docs/PERCEPTION.md "Gate and actions").
    deliver: Deliver = "now"
    expire_s: int | None = Field(default=None, ge=1, le=600)


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
    """A pending-checks row the trigger gate polls (SPEC §4.4) -- or, with a
    ``concept``, an armed condition on the watcher (docs/PERCEPTION.md "Gate
    and actions"): "wake me if ``screen_present`` goes hot within
    ``within_s``". On a match the gate escalates as ``watch_armed`` carrying
    the original decision id. ``concept`` must be a §9 boolean
    (:data:`WATCH_CONCEPTS`); :func:`normalize` drops any other and fills
    ``within_s`` in 10..7200 s, 600 by default.
    """

    type: Literal["watch"] = "watch"
    after_s: int | None = None
    condition: str | None = None
    reason: str = ""
    concept: str | None = None
    within_s: int | None = None


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
    deliver: Deliver = "now"
    expire_s: int | None = Field(default=None, ge=1, le=600)


class RememberAction(_ActionBase):
    """One durable fact about the wearer, added to the persona (Part A).

    Distinct from ``annotate``, which is about *today* and is read back for one
    day, and from ``log_insight``, which is a health observation for a report.
    A ``remember`` line is about *who the wearer is* -- a preference, a habit, a
    person, a place, a routine -- and goes into every future system prompt.
    """

    type: Literal["remember"] = "remember"
    line: str


class LookAction(_ActionBase):
    """One targeted labeler call on the current frame (docs/PERCEPTION.md).

    The question goes through the labeler's mailbox; the answer re-wakes the
    decider with the question and answer appended, or goes to the voice agent
    when a conversation is open. One look per decision, never chained --
    :func:`normalize` keeps the first and defers the decision's ``speak`` and
    ``ask`` until the answer is back.
    """

    type: Literal["look"] = "look"
    question: str = Field(min_length=LOOK_MIN_CHARS, max_length=LOOK_MAX_CHARS)
    reason: str = ""


class ActAction(_ActionBase):
    """One thing the phone does. The clerk and decider may only originate a
    sound cue (``kind: sound``, ``args: {name: chime | tick | soft}``); the
    autopilot's calendar and shield acts never go through this schema."""

    type: Literal["act"] = "act"
    kind: Literal["sound"] = "sound"
    args: dict[str, Any] = Field(default_factory=dict)


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
        LookAction,
        ActAction,
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
    #: Set by :func:`normalize` when a ``look`` displaced this response's
    #: ``speak``/``ask``: the re-run after the answer decides them afresh.
    deferred_for_look: bool = False

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
        "concept": {
            "type": ["string", "null"],
            "description": "Arm the watcher on one of these and wake when it "
            "comes back: " + ", ".join(BOOL_FIELDS + TRISTATE_BOOL_FIELDS)
            + ". Null for a plain timed re-check.",
        },
        "within_s": {
            "type": ["integer", "null"],
            "description": "How long the armed concept is watched for, "
            "10 to 7200 seconds; null means 600. Ignored without a concept.",
        },
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

_LOOK = _obj(
    {
        "type": {"type": "string", "enum": ["look"]},
        "question": {
            "type": "string",
            "description": "One short question about the current camera frame "
            "that would settle the decision, 120 characters or less. Its "
            "answer wakes you again; any speak or ask waits for it.",
        },
        "reason": {"type": "string"},
    }
)

_ACT = _obj(
    {
        "type": {"type": "string", "enum": ["act"]},
        "kind": {"type": "string", "enum": ["sound"]},
        "args": _obj(
            {
                "name": {
                    "type": "string",
                    "enum": list(SOUND_NAMES),
                    "description": "A short non-verbal cue in the wearer's ear, "
                    "cheaper than a sentence. Dropped next to a speak.",
                },
            }
        ),
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
                    _LOOK,
                    _ACT,
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


def _armed_watch(action: Any) -> Any:
    """A ``watch`` with a concept: None (dropped) when the concept is not a §9
    boolean, else the same watch with ``within_s`` settled into its bounds."""

    if getattr(action, "type", None) != "watch":
        return action
    concept = getattr(action, "concept", None)
    if concept is None:
        return action
    concept = str(concept).strip()
    if concept not in WATCH_CONCEPTS:
        log.info("%s: watch on %r dropped (not a §9 boolean)", WATCH_CONCEPT_UNKNOWN, concept)
        return None
    within = getattr(action, "within_s", None)
    if within is None:
        within = WATCH_WITHIN_DEFAULT_S
    within = int(min(WATCH_WITHIN_MAX_S, max(WATCH_WITHIN_MIN_S, int(within))))
    return WatchAction(after_s=action.after_s, condition=action.condition,
                       reason=action.reason, concept=concept, within_s=within)


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
    * at most one ``look`` (the first); with one, ``speak`` and ``ask`` are
      removed and ``deferred_for_look`` is set -- the re-run after the answer
      decides them afresh (docs/PERCEPTION.md "Normalisation additions").
    * a sound ``act`` next to a ``speak`` is dropped: the speak wins.
    * a ``watch`` with a concept the watcher cannot score (not a §9 boolean)
      is dropped, logged as ``WATCH_CONCEPT_UNKNOWN``; one with a known
      concept gets ``within_s`` clamped to 10..7200 s, 600 when unset.
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

    actions = [_armed_watch(a) for a in actions]
    actions = [a for a in actions if a is not None]

    deferred = False
    looks = [a for a in actions if a.type == "look"]
    if looks:
        first = looks[0]
        kept: list[Any] = []
        for a in actions:
            if a.type == "look":
                if a is first:
                    kept.append(a)
                continue
            if a.type in ("speak", "ask"):
                deferred = True
                continue
            kept.append(a)
        actions = kept

    actions = drop_sound_if_speaking(actions)

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
        deferred_for_look=deferred,
    )
