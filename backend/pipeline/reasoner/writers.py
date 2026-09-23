"""Writers (docs/PERCEPTION.md, "Decider and writers").

The decider says *whether* an action fires; a writer says *what* it carries.
One small GPT prompt per action, run only on a yes, each returning one or two
fields under a strict JSON schema. Every writer is bounded by
``writer_timeout_s`` and never raises: a failure is logged, recorded in
``calls`` and turned into a default (``None``, or a plain summary line).

Writers reuse the clerk's transport rather than opening their own: the
``OpenAIReasonerClient``'s pooled SDK client and its reasoning-effort ladder.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from .client import OpenAIReasonerClient
from .decider import Verdict
from .decider_settings import DeciderSettings
from .effort import create_with_effort
from .schema import ANNOTATE_MAX_CHARS, FOLLOWUP_MAX_CHARS, REMEMBER_MAX_CHARS, _obj

__all__ = [
    "ANSWER_KINDS",
    "FILLS",
    "FakeWriters",
    "Writers",
    "make_writers",
]

log = logging.getLogger(__name__)

Outcome = Literal["ok", "failed", "empty"]

ANSWER_KINDS = ("yes_no", "count", "free")
FILLS = ("confirmed", "count", "food_type", "note")

INSIGHT_MAX_CHARS = 240
CATEGORY_MAX_CHARS = 24
HANDOFF_MAX_CHARS = 160
QUESTION_MAX_CHARS = FOLLOWUP_MAX_CHARS
CONDITION_MAX_CHARS = 120
LOOK_MAX_CHARS = 120
#: A watch further out than this is a new day's business, not a re-check.
WATCH_MAX_S = 6 * 3600

# -- prompts ---------------------------------------------------------------
# Shared preamble, then one purpose per writer. Kept as constants to tune.

_PREAMBLE = (
    "You work inside a health companion that runs on camera glasses and speaks "
    "in the wearer's ear. A separate voice agent writes every spoken sentence; "
    "you never write what is said aloud. The user message is JSON: `state` "
    "(recent camera tags and captions, open episodes, today's summary, the "
    "wearer's persona, seven-day trends, local clock), `topic` (the health "
    "topic this moment is about) and `probability` (how sure the decider is "
    "that this action should fire). Use only facts in the state. Plain words, "
    "no emoji, no markdown. "
)

SUMMARY_PROMPT = _PREAMBLE + (
    "Write ONE line for today's running summary: what just happened, concrete "
    f"and specific, at most {ANNOTATE_MAX_CHARS} characters, no time prefix. "
    'Example: "second coffee at the desk, laptop open".'
)

INSIGHT_PROMPT = _PREAMBLE + (
    "Write one insight for today's health report: a measurable fact tied to "
    "the topic, citing what was seen and, where the trends allow, how it "
    "compares. `category` is one short lowercase tag (diet, sleep, screen, "
    "social, nature, movement, alcohol, caffeine, stress, or similar). "
    f"`text` is one sentence, at most {INSIGHT_MAX_CHARS} characters. "
    "Return empty strings if nothing measurable is in the state."
)

PERSONA_PROMPT = _PREAMBLE + (
    "Write ONE durable fact about the wearer worth remembering for weeks: a "
    "habit, preference, routine, person or place, supported by the state (a "
    "repeated pattern or the wearer's own answer). Not today's events. At "
    f"most {REMEMBER_MAX_CHARS} characters. Return an empty string if the "
    "state shows nothing lasting or the persona already says it."
)

HANDOFF_PROMPT = _PREAMBLE + (
    "Write the hand-off the voice agent receives: the TOPIC and the REASON "
    "in one plain line, never the sentence to say. `action` says whether the "
    "voice agent will lean to a statement (speak) or a question (ask). "
    f"At most {HANDOFF_MAX_CHARS} characters. "
    'Example: "third coffee after 3pm, bedtime 11pm, caffeine half-life 5h".'
)

QUESTION_PROMPT = _PREAMBLE + (
    "The companion will ask the wearer a short question. Write what the "
    "question must find out, in plain words (the voice agent writes the "
    f"wording), at most {QUESTION_MAX_CHARS} characters, plus the expected "
    "`answer_kind` (yes_no, count, free) and the one field the answer fills "
    "(confirmed: whether a seen item is the wearer's or being consumed; "
    "count: servings today; food_type: what the food is; note: anything else). "
    'Example: text "whether the wine glass in hand is the wearer\'s", '
    "answer_kind yes_no, fills confirmed."
)

WATCH_PROMPT = _PREAMBLE + (
    "The companion will re-check this moment later. Give `after_s`, seconds "
    "from now to look again (for example 60 for a drink, 1200 for screen "
    "time), and/or `condition`, a plain-language condition the camera can "
    f"confirm, at most {CONDITION_MAX_CHARS} characters. Use null for "
    "whichever you do not need, but never both."
)

LOOK_PROMPT = _PREAMBLE + (
    "The companion will send the current camera frame to a vision labeler. "
    "Write ONE question the labeler can answer from that single frame and "
    "that would settle this moment, at most "
    f"{LOOK_MAX_CHARS} characters. "
    'Example: "Is the cup in the wearer\'s hand coffee, tea or water?"'
)

# -- schemas ---------------------------------------------------------------


def _format(name: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "strict": True,
            "schema": _obj(properties),
        }
    }


_STR = {"type": "string"}

SUMMARY_FORMAT = _format("summary_line", {"line": _STR})
INSIGHT_FORMAT = _format("insight", {"category": _STR, "text": _STR})
PERSONA_FORMAT = _format("persona_fact", {"fact": _STR})
HANDOFF_FORMAT = _format("handoff_topic", {"topic": _STR})
QUESTION_FORMAT = _format(
    "question",
    {
        "text": _STR,
        "answer_kind": {"type": "string", "enum": list(ANSWER_KINDS)},
        "fills": {"type": "string", "enum": list(FILLS)},
    },
)
WATCH_FORMAT = _format(
    "watch_condition",
    {
        "after_s": {"type": ["integer", "null"]},
        "condition": {"type": ["string", "null"]},
    },
)
LOOK_FORMAT = _format("look_question", {"question": _STR})


def _line(value: Any, limit: int) -> str:
    """One trimmed line, cut to ``limit``. Non-strings read as empty."""

    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].rstrip()


def _fallback_summary(state: dict) -> str:
    trigger = state.get("trigger") or {}
    return f"{trigger.get('name')}: {trigger.get('reason')}"[:ANNOTATE_MAX_CHARS]


class Writers:
    """The real writers over the clerk's OpenAI transport."""

    def __init__(self, transport: Any, settings: DeciderSettings) -> None:
        # An OpenAIReasonerClient lends its SDK client and effort; anything
        # else is taken to be an SDK client itself (``.responses.create``).
        self._sdk = getattr(transport, "_client", transport)
        self._effort = getattr(transport, "reasoning_effort", None)
        self.settings = settings
        self.calls: list[tuple[str, Outcome]] = []

    async def _call(
        self, name: str, prompt: str, text_format: dict[str, Any],
        state: dict, verdict: Verdict, action: str,
    ) -> dict[str, Any] | None:
        """One bounded request; the parsed JSON object, or None on failure
        (already logged and recorded as ``failed``)."""

        payload = {
            "action": action,
            "topic": verdict.topic,
            "probability": round(verdict.probabilities.get(action, 0.0), 3),
            "state": state,
        }
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        model = self.settings.writer_model
        try:
            response, _ = await asyncio.wait_for(
                create_with_effort(
                    self._sdk, model, self._effort,
                    dict(model=model, input=messages, text=text_format),
                    label=f"writer {name}",
                ),
                timeout=self.settings.writer_timeout_s,
            )
            data = json.loads(getattr(response, "output_text", None) or "")
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")
            return data
        except Exception as exc:  # noqa: BLE001 - a writer never fails the escalation
            log.warning("writer %s failed: %s: %s", name, type(exc).__name__, exc)
            self.calls.append((name, "failed"))
            return None

    def _record(self, name: str, value: Any) -> Any:
        self.calls.append((name, "ok" if value else "empty"))
        return value or None

    async def summary_line(self, state: dict, verdict: Verdict) -> str:
        data = await self._call("summary_line", SUMMARY_PROMPT, SUMMARY_FORMAT,
                                state, verdict, "annotate")
        if data is None:
            return _fallback_summary(state)
        line = self._record("summary_line", _line(data.get("line"), ANNOTATE_MAX_CHARS))
        return line or _fallback_summary(state)

    async def insight(self, state: dict, verdict: Verdict) -> tuple[str, str] | None:
        data = await self._call("insight", INSIGHT_PROMPT, INSIGHT_FORMAT,
                                state, verdict, "log_insight")
        if data is None:
            return None
        text = _line(data.get("text"), INSIGHT_MAX_CHARS)
        category = _line(data.get("category"), CATEGORY_MAX_CHARS).lower() or "general"
        return self._record("insight", (category, text) if text else None)

    async def persona_fact(self, state: dict, verdict: Verdict) -> str | None:
        data = await self._call("persona_fact", PERSONA_PROMPT, PERSONA_FORMAT,
                                state, verdict, "remember")
        if data is None:
            return None
        return self._record("persona_fact", _line(data.get("fact"), REMEMBER_MAX_CHARS))

    async def handoff_topic(
        self, state: dict, verdict: Verdict, action: str = "speak"
    ) -> str | None:
        """The ``speak``/``ask`` text: topic plus reason, one line."""

        data = await self._call("handoff_topic", HANDOFF_PROMPT, HANDOFF_FORMAT,
                                state, verdict, action)
        if data is None:
            return None
        return self._record("handoff_topic", _line(data.get("topic"), HANDOFF_MAX_CHARS))

    async def question(
        self, state: dict, verdict: Verdict
    ) -> tuple[str, str, str] | None:
        data = await self._call("question", QUESTION_PROMPT, QUESTION_FORMAT,
                                state, verdict, "ask")
        if data is None:
            return None
        kind, fills = data.get("answer_kind"), data.get("fills")
        if kind not in ANSWER_KINDS or fills not in FILLS:
            log.warning("writer question: bad answer_kind=%r or fills=%r", kind, fills)
            self.calls.append(("question", "failed"))
            return None
        text = _line(data.get("text"), QUESTION_MAX_CHARS)
        return self._record("question", (text, kind, fills) if text else None)

    async def watch_condition(
        self, state: dict, verdict: Verdict
    ) -> tuple[int | None, str | None] | None:
        data = await self._call("watch_condition", WATCH_PROMPT, WATCH_FORMAT,
                                state, verdict, "watch")
        if data is None:
            return None
        after = data.get("after_s")
        after_s = (
            min(after, WATCH_MAX_S)
            if isinstance(after, int) and not isinstance(after, bool) and after > 0
            else None
        )
        condition = _line(data.get("condition"), CONDITION_MAX_CHARS) or None
        result = None if after_s is None and condition is None else (after_s, condition)
        return self._record("watch_condition", result)

    async def look_question(self, state: dict, verdict: Verdict) -> str | None:
        data = await self._call("look_question", LOOK_PROMPT, LOOK_FORMAT,
                                state, verdict, "look")
        if data is None:
            return None
        return self._record("look_question", _line(data.get("question"), LOOK_MAX_CHARS))


class FakeWriters:
    """Canned writers: same methods, no network. Pass ``name=value`` to
    override a method's return (``None`` included); every call is recorded."""

    DEFAULTS: dict[str, Any] = {
        "summary_line": "moment noted",
        "insight": ("general", "moment noted for today's report"),
        "persona_fact": "notes a lasting habit",
        "handoff_topic": "moment worth a word, seen in frame",
        "question": ("whether this is the wearer's", "yes_no", "confirmed"),
        "watch_condition": (60, None),
        "look_question": "What is the wearer holding?",
    }

    def __init__(self, **canned: Any) -> None:
        unknown = set(canned) - set(self.DEFAULTS)
        if unknown:
            raise TypeError(f"unknown writer(s): {sorted(unknown)}")
        self.canned = {**self.DEFAULTS, **canned}
        self.calls: list[tuple[str, Outcome]] = []

    def _give(self, name: str) -> Any:
        value = self.canned[name]
        self.calls.append((name, "ok" if value else "empty"))
        return value

    async def summary_line(self, state: dict, verdict: Verdict) -> str:
        return self._give("summary_line") or _fallback_summary(state)

    async def insight(self, state: dict, verdict: Verdict) -> tuple[str, str] | None:
        return self._give("insight")

    async def persona_fact(self, state: dict, verdict: Verdict) -> str | None:
        return self._give("persona_fact")

    async def handoff_topic(
        self, state: dict, verdict: Verdict, action: str = "speak"
    ) -> str | None:
        return self._give("handoff_topic")

    async def question(self, state: dict, verdict: Verdict) -> tuple[str, str, str] | None:
        return self._give("question")

    async def watch_condition(
        self, state: dict, verdict: Verdict
    ) -> tuple[int | None, str | None] | None:
        return self._give("watch_condition")

    async def look_question(self, state: dict, verdict: Verdict) -> str | None:
        return self._give("look_question")


def make_writers(settings: DeciderSettings, reasoner_client: Any) -> Writers | None:
    """Writers over the clerk's transport, or None on the fake reasoner path."""

    if isinstance(reasoner_client, OpenAIReasonerClient):
        return Writers(reasoner_client, settings)
    return None
