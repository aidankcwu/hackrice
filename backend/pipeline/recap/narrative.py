"""The recap narrative: a structured model call, separate from T1.

T1 decides what to *do* in the moment. This decides what to *say* about a
finished window, and it is a different job: it reads the whole session at once
-- subscores, moments, today's memory lines -- and writes four things.

``headline`` and ``paragraphs`` are read on screen. ``spoken`` is read aloud
through the glasses, which is why it is a separate field rather than a
concatenation of the others: prose that scans on a dashboard ("screen 0.4 h,
diet 0.67") is unlistenable, and ElevenLabs will happily pronounce every
decimal point.

Two implementations behind one interface, chosen by the pipeline's reasoner
mode, so a key-less run still recaps (SPEC §11.6 "no-key integration test").
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..reasoner.prompts import DEFAULT_PERSONA

log = logging.getLogger(__name__)

__all__ = [
    "FakeNarrativeClient",
    "HEADLINE_MAX_WORDS",
    "NarrativeClient",
    "OpenAINarrativeClient",
    "PARAGRAPH_MAX_WORDS",
    "RECAP_JSON_SCHEMA",
    "RECAP_TEXT_FORMAT",
    "RecapContext",
    "RecapNarrative",
    "SPOKEN_MAX_WORDS",
    "SUGGESTION_MAX_WORDS",
    "build_system_prompt",
    "build_user_prompt",
    "clamp_narrative",
    "make_narrative_client",
]

HEADLINE_MAX_WORDS = 12
PARAGRAPH_MAX_WORDS = 60
SPOKEN_MAX_WORDS = 55
#: A suggestion is one line on a card; a model that writes a paragraph into it
#: breaks the layout, so code counts the words rather than trusting the schema.
SUGGESTION_MAX_WORDS = 30
MAX_PARAGRAPHS = 3
MAX_SUGGESTIONS = 3
#: The recap is one call at the end of a session, not a per-tick budget, so it
#: can afford to wait -- but not past the judge's patience.
RECAP_TIMEOUT_S = 20.0

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)
#: "0.67" -> "0.7" is still a decimal; spoken text gets whole numbers only.
_DECIMAL = re.compile(r"\b(\d+)\.(\d+)\b")


class RecapNarrative(BaseModel):
    """What one recap call returns."""

    model_config = ConfigDict(extra="ignore")

    headline: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    spoken: str = ""


@dataclass(frozen=True)
class RecapContext:
    """Everything the model is shown. Built by :mod:`pipeline.recap.builder`."""

    duration_s: float
    #: ``{metric, label, value, unit, score, source, note}`` per subscore.
    subscores: list[dict[str, Any]] = field(default_factory=list)
    #: ``{t, category, severity, caption, insight}`` per moment.
    moments: list[dict[str, Any]] = field(default_factory=list)
    #: Today's ``annotate`` lines that fall inside the window.
    summary_lines: list[str] = field(default_factory=list)
    overall: float = 0.0
    persona: str = DEFAULT_PERSONA

    @property
    def minutes(self) -> int:
        return max(1, int(round(self.duration_s / 60.0)))


@runtime_checkable
class NarrativeClient(Protocol):
    """One structured call per recap."""

    async def write(self, ctx: RecapContext) -> tuple[RecapNarrative, dict[str, Any]]:
        """Return the narrative plus meta (``model``, ``latency_ms``)."""
        ...


# -- JSON schema (Responses API, strict) ---------------------------------


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


RECAP_JSON_SCHEMA: dict[str, Any] = _obj({
    "headline": {
        "type": "string",
        "description": f"At most {HEADLINE_MAX_WORDS} words. No colon-subtitle.",
    },
    "paragraphs": {
        "type": "array",
        "minItems": 2,
        "maxItems": MAX_PARAGRAPHS,
        "items": {
            "type": "string",
            "description": f"At most {PARAGRAPH_MAX_WORDS} words each.",
        },
    },
    "suggestions": {
        "type": "array",
        "minItems": 1,
        "maxItems": MAX_SUGGESTIONS,
        "items": {
            "type": "string",
            "description": f"One sentence each, at most {SUGGESTION_MAX_WORDS} words.",
        },
    },
    "spoken": {
        "type": "string",
        "description": (
            f"At most {SPOKEN_MAX_WORDS} words, written to be read aloud. "
            "Plain sentences. No lists, no bullet characters, no decimals."
        ),
    },
})

#: Pass as ``text=RECAP_TEXT_FORMAT`` to ``responses.create``.
RECAP_TEXT_FORMAT: dict[str, Any] = {
    "format": {
        "type": "json_schema",
        "name": "session_recap",
        "strict": True,
        "schema": RECAP_JSON_SCHEMA,
    }
}


# -- prompts --------------------------------------------------------------


def build_system_prompt(ctx: RecapContext) -> str:
    """Voice rules from the persona, plus the one thing the model must not do."""

    return (
        "## Who you are writing for\n"
        f"{ctx.persona.strip()}\n\n"
        "## Your job\n"
        "You are writing the recap of a short wearable-camera session that has "
        "just ended. You are shown the session length, the scored metrics, the "
        "moments the system escalated on (each with its own photo, which you "
        "cannot see -- go by the caption), and the memory lines written during "
        "the window.\n\n"
        "Voice: short, dry, specific. No cheerleading, no moralising, no "
        "preamble, no motivational tone. Name what was actually observed and "
        "what it means; never invent detail the moments do not support. A dry, "
        "specific observation lands; a lecture does not.\n\n"
        f"THIS WAS A {ctx.minutes}-MINUTE SESSION. Do not pretend it was a day. "
        "Counts are what happened in those minutes. Where a rate has been "
        "extrapolated to a full day the note says so -- quote it as a "
        "projection, never as a measurement. Metrics marked 'seeded' came off "
        "the wearable's own history, not from this session; say so if you use "
        "them.\n\n"
        "'spoken' is read aloud through the glasses' speakers. Write it as "
        "speech: plain sentences, no lists, no numbers with decimal points, "
        "nothing that only makes sense on a screen.\n\n"
        "Respond with JSON matching the required schema and nothing else."
    )


def _fmt_value(row: dict[str, Any]) -> str:
    value = row.get("value")
    if value is None:
        return "no data"
    unit = row.get("unit") or ""
    return f"{float(value):.2f}".rstrip("0").rstrip(".") + (f" {unit}" if unit else "")


def build_user_prompt(ctx: RecapContext) -> str:
    """The volatile half: this session's numbers, moments and memory lines."""

    lines = [f"Session length: {ctx.minutes} min ({ctx.duration_s:.0f} s).",
             f"Overall score: {ctx.overall:.2f} of 1.00.", "", "Subscores:"]
    for row in ctx.subscores:
        note = f" — {row['note']}" if row.get("note") else ""
        lines.append(
            f"  {row.get('label') or row.get('metric')} ({row.get('source')}): "
            f"{_fmt_value(row)}, score {float(row.get('score', 0.0)):.2f}{note}"
        )
    lines += ["", "Moments:"]
    if not ctx.moments:
        lines.append("  (none -- nothing escalated in this window)")
    for moment in ctx.moments:
        stamp = time.strftime("%H:%M:%S", time.localtime(float(moment.get("t", 0.0))))
        insight = f" | insight: {moment['insight']}" if moment.get("insight") else ""
        lines.append(
            f"  {stamp} [{moment.get('category')}/{moment.get('severity')}] "
            f"{moment.get('caption')}{insight}"
        )
    lines += ["", "Memory lines written during the window:"]
    lines += [f"  {line}" for line in ctx.summary_lines] or ["  (none)"]
    lines += ["", "Write the recap."]
    return "\n".join(lines)


# -- shared post-processing ----------------------------------------------


def _clip_words(text: str, limit: int) -> str:
    words = (text or "").split()
    return " ".join(words[:limit]) if len(words) > limit else " ".join(words)


def _despecify(text: str) -> str:
    """Round decimals out of speech: '0.67' and '4.5 h' are unlistenable."""

    return _DECIMAL.sub(lambda m: str(round(float(m.group(0)))), text or "")


def clamp_narrative(narrative: RecapNarrative) -> RecapNarrative:
    """Enforce the limits code owns rather than trusting the model to count.

    The schema states them; models still overshoot, and an over-long ``spoken``
    is forty seconds of the glasses talking over a judge.
    """

    paragraphs = [_clip_words(p, PARAGRAPH_MAX_WORDS)
                  for p in narrative.paragraphs if p and p.strip()][:MAX_PARAGRAPHS]
    suggestions = [_clip_words(s, SUGGESTION_MAX_WORDS)
                   for s in narrative.suggestions if s and s.strip()][:MAX_SUGGESTIONS]
    return RecapNarrative(
        headline=_clip_words(narrative.headline, HEADLINE_MAX_WORDS),
        paragraphs=paragraphs,
        suggestions=suggestions,
        spoken=_clip_words(_despecify(narrative.spoken), SPOKEN_MAX_WORDS),
    )


# -- the real client ------------------------------------------------------


class OpenAINarrativeClient:
    """Responses API, strict JSON schema, one text-only call."""

    def __init__(self, api_key: str, model: str, timeout: float | None = None,
                 client: Any | None = None) -> None:
        if not api_key and client is None:
            raise RuntimeError("OpenAINarrativeClient requires an API key")
        self.model = model
        #: Read off the module at construction time so a test (or a caller that
        #: wants a tighter budget) can lower it without touching the signature.
        self.timeout = RECAP_TIMEOUT_S if timeout is None else float(timeout)
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            # ``max_retries=0``: the SDK's default of two silent retries turns a
            # 20 s budget into a 60 s one, which is the whole failure we are
            # bounding. One try, then the builder's fallback to the fake.
            self._client = AsyncOpenAI(
                api_key=api_key, max_retries=0, timeout=self.timeout)

    async def write(self, ctx: RecapContext) -> tuple[RecapNarrative, dict[str, Any]]:
        """One call, hard-bounded. A timeout raises; the builder falls back."""

        started = time.perf_counter()
        # The SDK's own timeout covers the HTTP exchange; this covers everything
        # -- connection setup, retries a future SDK version might reintroduce, a
        # stub client that simply never returns.
        response = await asyncio.wait_for(self._respond(ctx), self.timeout)
        raw = getattr(response, "output_text", None) or ""
        meta = {
            "model": getattr(response, "model", None) or self.model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
        return clamp_narrative(self._parse(raw)), meta

    async def _respond(self, ctx: RecapContext) -> Any:
        return await self._client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": build_system_prompt(ctx)},
                {"role": "user", "content": build_user_prompt(ctx)},
            ],
            text=RECAP_TEXT_FORMAT,
        )

    @staticmethod
    def _parse(raw: str) -> RecapNarrative:
        """Parse ``output_text``; retry once de-fenced, exactly as T1 does."""

        try:
            return RecapNarrative.model_validate(json.loads(raw))
        except Exception as first:
            log.warning("recap response did not parse (%s); retrying de-fenced", first)
            return RecapNarrative.model_validate(
                json.loads(_FENCE.sub("", raw).strip()))


# -- the fake -------------------------------------------------------------

_SEVERITY_WORD = {"good": "worth repeating", "flag": "worth a look",
                  "neutral": "noted"}


class FakeNarrativeClient:
    """Deterministic prose from the moments and scores. Zero latency, no key.

    Not a mock: it reads the same context the real model reads and writes
    something a judge can hear, so a key-less demo still ends with the glasses
    saying a true sentence about the last two minutes.
    """

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.last_context: RecapContext | None = None

    async def write(self, ctx: RecapContext) -> tuple[RecapNarrative, dict[str, Any]]:
        self.calls += 1
        self.last_context = ctx
        started = time.perf_counter()
        narrative = clamp_narrative(self._compose(ctx))
        meta = {"model": self.model,
                "latency_ms": int((time.perf_counter() - started) * 1000)}
        return narrative, meta

    # -- composition -----------------------------------------------------

    @staticmethod
    def _counts(ctx: RecapContext) -> dict[str, int]:
        counts = {"good": 0, "neutral": 0, "flag": 0}
        for moment in ctx.moments:
            counts[str(moment.get("severity", "neutral"))] = counts.get(
                str(moment.get("severity", "neutral")), 0) + 1
        return counts

    @staticmethod
    def _live(ctx: RecapContext) -> list[dict[str, Any]]:
        return [row for row in ctx.subscores if row.get("source") == "live"]

    @staticmethod
    def _weakest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        scored = [r for r in rows if r.get("value") is not None]
        return min(scored, key=lambda r: float(r.get("score", 0.0))) if scored else None

    def _compose(self, ctx: RecapContext) -> RecapNarrative:
        counts = self._counts(ctx)
        live = self._live(ctx)
        weakest = self._weakest(live)
        categories = []
        for moment in ctx.moments:
            category = str(moment.get("category", "other"))
            if category not in categories:
                categories.append(category)

        headline = (
            f"{ctx.minutes} minutes: {len(ctx.moments)} moments, "
            f"{counts['flag']} flagged"
        )

        if ctx.moments:
            first = ctx.moments[0]
            seen = ", ".join(categories[:4])
            para_one = (
                f"{len(ctx.moments)} escalations in {ctx.minutes} minutes, "
                f"across {seen}. It opened on {first.get('caption')} "
                f"({_SEVERITY_WORD.get(str(first.get('severity')), 'noted')}). "
                f"{counts['good']} good, {counts['flag']} flagged, "
                f"{counts['neutral']} neutral."
            )
        else:
            para_one = (
                f"Nothing escalated in {ctx.minutes} minutes. The tick stream "
                "ran and the gate stayed quiet, which is the system working, "
                "not the system broken."
            )

        if weakest is not None:
            note = weakest.get("note") or "no note"
            para_two = (
                f"Overall {ctx.overall:.2f} of 1.00 across {len(ctx.subscores)} "
                f"metrics. The weakest live metric is "
                f"{weakest.get('label') or weakest.get('metric')} at "
                f"{float(weakest.get('score', 0.0)):.2f}: {note}. The rest of "
                "the panel is seeded history, not this session."
            )
        else:
            para_two = (
                f"Overall {ctx.overall:.2f} of 1.00, entirely from seeded "
                "history: this window produced no live metric with a value."
            )

        suggestions: list[str] = []
        flagged = [m for m in ctx.moments if m.get("severity") == "flag"]
        if flagged:
            suggestions.append(
                f"Look at the {flagged[0].get('category')} moment first: "
                f"{flagged[0].get('caption')}."
            )
        if weakest is not None:
            suggestions.append(
                f"{weakest.get('label') or weakest.get('metric')} is the one to "
                "move; the target is "
                f"{weakest.get('target') or 'on the metric card'}."
            )
        if not suggestions:
            suggestions.append("Nothing to change from this window; run it longer.")

        good_or_flag = (
            f"{counts['flag']} worth a look" if counts["flag"]
            else "nothing flagged"
        )
        spoken = (
            f"That was about {ctx.minutes} minutes. I caught "
            f"{len(ctx.moments)} moments, {good_or_flag}. "
            + (f"The first was {ctx.moments[0].get('caption')}. "
               if ctx.moments else "")
            + (f"Weakest thing here is {weakest.get('label') or weakest.get('metric')}. "
               if weakest is not None else "")
            + "That is the whole session."
        )

        return RecapNarrative(
            headline=headline,
            paragraphs=[para_one, para_two],
            suggestions=suggestions[:MAX_SUGGESTIONS],
            spoken=spoken,
        )


# -- factory --------------------------------------------------------------


def make_narrative_client(
    settings: Settings, mode: Literal["openai", "fake"]
) -> NarrativeClient:
    """Build a narrative client. ``mode`` mirrors the pipeline's reasoner mode.

    A key-less ``openai`` run falls back to the fake rather than refusing to
    recap. This is the opposite of :func:`pipeline.reasoner.client.make_client`,
    and deliberately so: a silent rule-table *decision* is a lie about what the
    system did, while a rule-table *summary* of real decisions is still a true
    summary -- and the alternative is a demo that ends with an error page.
    """

    if mode == "fake":
        return FakeNarrativeClient()
    if mode != "openai":
        raise ValueError(f"unknown narrative client mode: {mode!r}")
    if not settings.openai_api_key:
        log.warning("OPENAI_API_KEY is not set; recap narrative falls back to the fake")
        return FakeNarrativeClient()
    return OpenAINarrativeClient(settings.openai_api_key, settings.t1_model)
