"""The voice agent's structured reply (docs/CONVERSATION_DESIGN.md §4).

One JSON object per turn: the line to say, whether it is a question or a
statement, whatever the exchange settled, whether the transcript read as an
answer at all, and whether the conversation is over.

Written for the Responses API in ``strict`` mode, exactly like
:mod:`pipeline.reasoner.schema`: every object closed, every property required,
optionality expressed as a nullable union.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..models import FOOD_TYPES
from ..reasoner.schema import COUNT_MAX, NOTE_MAX_CHARS, _obj

__all__ = [
    "VoiceSettled",
    "VoiceReply",
    "VOICE_JSON_SCHEMA",
    "VOICE_TEXT_FORMAT",
    "VOICE_OPEN_JSON_SCHEMA",
    "VOICE_OPEN_TEXT_FORMAT",
    "UTTERANCE_MAX_CHARS",
]

#: One line, spoken aloud. Past this it is a paragraph, and the wearer is
#: standing in a room waiting for it to finish.
UTTERANCE_MAX_CHARS = 200


class VoiceSettled(BaseModel):
    """What the exchange established, in the fields the clerk can score.

    The same four facts :class:`~pipeline.reasoner.schema.AnswerParse` carries,
    validated the same way -- an out-of-range count or an invented food becomes
    ``None`` rather than a number nobody said.
    """

    model_config = ConfigDict(extra="ignore")

    confirmed: bool | None = None
    count: float | None = None
    food_type: str | None = None
    note: str | None = None

    @field_validator("count", mode="before")
    @classmethod
    def _clean_count(cls, value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number) or number < 0.0 or number > COUNT_MAX:
            return None
        return number

    @field_validator("food_type", mode="before")
    @classmethod
    def _clean_food_type(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip().lower().replace(" ", "_").replace("-", "_")
        if candidate in ("", "none", "null", "unknown"):
            return None
        return candidate if candidate in FOOD_TYPES else None

    @field_validator("note", mode="before")
    @classmethod
    def _clean_note(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:NOTE_MAX_CHARS].rstrip() or None

    def any_fact(self) -> bool:
        """True when this settled anything at all worth writing down."""

        return (
            self.confirmed is not None
            or self.count is not None
            or self.food_type is not None
            or bool(self.note)
        )


class VoiceReply(BaseModel):
    """One turn from the voice agent."""

    model_config = ConfigDict(extra="ignore")

    #: "" means stay silent -- a legitimate way to end a conversation.
    utterance: str = ""
    kind: Literal["question", "statement"] = "statement"
    settled: VoiceSettled = VoiceSettled()
    #: Reply turns only: did the transcript read as an answer?
    heard: bool = True
    #: True closes the conversation after this utterance. A statement always
    #: closes; a question always opens the mic, whatever this says.
    done: bool = True

    @field_validator("utterance", mode="before")
    @classmethod
    def _clean_utterance(cls, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        # The same bar the clerk's speech is held to: silence means no words,
        # never the word "nothing" or a JSON blob read out loud.
        if text.lower() in {"nothing", "none", "null", "silent", "(silence)"}:
            return ""
        if text[:1] in "{[" or '"type"' in text:
            return ""
        return text[:UTTERANCE_MAX_CHARS].rstrip()


#: The two fields every turn has. Shared (not copied) so the opening schema
#: and the reply schema can never describe ``utterance`` or ``kind``
#: differently. ``utterance`` stays first: the line is what the wearer is
#: waiting for, and moving ``kind`` ahead of it changed nothing measurable.
_UTTERANCE: dict[str, Any] = {
    "type": "string",
    "description": "The one line to say aloud, in the persona's voice. "
    'Empty string ("") to stay silent.',
}
_KIND: dict[str, Any] = {
    "type": "string",
    "enum": ["question", "statement"],
    "description": "A question opens the microphone; a statement ends "
    "the conversation.",
}

VOICE_JSON_SCHEMA: dict[str, Any] = _obj(
    {
        "utterance": _UTTERANCE,
        "kind": _KIND,
        "settled": _obj(
            {
                "confirmed": {
                    "type": ["boolean", "null"],
                    "description": "True when the wearer says the item is "
                    "theirs AND they are having it; false when it is not "
                    "theirs or they are not having it; null when unsaid.",
                },
                "count": {
                    "type": ["number", "null"],
                    "description": "Servings of this item today, 0 to 20, or null.",
                },
                "food_type": {
                    "type": ["string", "null"],
                    "description": "One of the fixed food_type values, or null. "
                    "Never invent one: " + ", ".join(FOOD_TYPES) + ".",
                },
                "note": {
                    "type": ["string", "null"],
                    "description": "One short line of what the wearer said in "
                    "effect, 80 characters or less, or null.",
                },
            }
        ),
        "heard": {
            "type": "boolean",
            "description": "Reply turns only: true when the transcript reads as "
            "an answer to what you asked. False for noise, UI words, or a "
            "fragment you cannot place.",
        },
        "done": {
            "type": "boolean",
            "description": "True closes the conversation after this utterance.",
        },
    }
)

#: Pass as ``text=VOICE_TEXT_FORMAT`` to ``responses.create``.
VOICE_TEXT_FORMAT: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "name": "voice_turn",
        "strict": True,
        "schema": VOICE_JSON_SCHEMA,
    }
}

#: The opening turn's schema: just the line and its shape.
#:
#: Under ``strict`` every property is required, so the full schema made the
#: model write ``settled`` (four nulls), ``heard`` and ``done`` on every
#: opening -- ~30-45 output tokens, ~0.3 s at ~8.7 ms/token, all of it thrown
#: away: nothing has been said yet, so an opening settles nothing (the prompt
#: says so, and ``_write_back`` drops unheard facts anyway); ``heard`` is
#: reply-only by definition; and ``done`` is not read -- a statement always
#: closes and a question always opens the microphone, by code. The missing
#: fields take :class:`VoiceReply`'s defaults (nothing settled, heard, done).
VOICE_OPEN_JSON_SCHEMA: dict[str, Any] = _obj(
    {
        "utterance": _UTTERANCE,
        "kind": _KIND,
    }
)

#: ``text=`` for an opening turn; reply turns keep :data:`VOICE_TEXT_FORMAT`.
VOICE_OPEN_TEXT_FORMAT: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "name": "voice_open",
        "strict": True,
        "schema": VOICE_OPEN_JSON_SCHEMA,
    }
}
