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
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SpeakAction",
    "LogInsightAction",
    "AnnotateAction",
    "WatchAction",
    "NothingAction",
    "Action",
    "T1Response",
    "T1_JSON_SCHEMA",
    "T1_TEXT_FORMAT",
    "normalize",
]

Urgency = Literal["low", "normal", "high"]

ANNOTATE_MAX_CHARS = 80


class _ActionBase(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SpeakAction(_ActionBase):
    """An utterance proposal. Code disposes (SPEC §4.6)."""

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


class NothingAction(_ActionBase):
    type: Literal["nothing"] = "nothing"


Action = Annotated[
    Union[SpeakAction, LogInsightAction, AnnotateAction, WatchAction, NothingAction],
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
        "text": {"type": "string", "description": "What to say. One sentence."},
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
                "anyOf": [_SPEAK, _LOG_INSIGHT, _ANNOTATE, _WATCH, _NOTHING],
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


# -- normalisation --------------------------------------------------------


def _hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().strftime("%H:%M")


def normalize(resp: T1Response, t: float | None = None) -> T1Response:
    """Enforce the invariants code owns rather than the model.

    * ``annotate`` is always present (SPEC §4.5 "always write"). A missing one
      is synthesised from the interpretation.
    * ``nothing`` is dropped when any other action was chosen -- "no action"
      alongside an action is a contradiction, and the model does emit it.
    * ``confidence`` is clamped to 0..1.
    """

    stamp = time.time() if t is None else t
    actions = list(resp.actions)

    # A speak whose text is empty, a bare "nothing", or a JSON-looking blob is
    # the model trying to stay silent the wrong way (seen live: ElevenLabs
    # read '{"type":"nothing"}' aloud). Silence means no speak action at all.
    def _speakable(a: Any) -> bool:
        if getattr(a, "type", None) != "speak":
            return True
        text = (getattr(a, "text", "") or "").strip()
        if len(text) < 2 or text.lower() in {"nothing", "none", "null", "silent"}:
            return False
        if text[0] in "{[" or '"type"' in text:
            return False
        return True

    actions = [a for a in actions if _speakable(a)]

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
