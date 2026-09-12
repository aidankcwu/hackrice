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
from .schema import (
    AnnotateAction,
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
    ) -> None:
        if not api_key:
            raise RuntimeError("OpenAIReasonerClient requires an API key")
        self.model = model
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete(
        self, input_messages: list[dict[str, Any]]
    ) -> tuple[T1Response, dict[str, Any]]:
        started = time.perf_counter()
        response = await self._client.responses.create(
            model=self.model,
            input=input_messages,
            text=T1_TEXT_FORMAT,
        )
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
_FOOD_WORDS = (
    "vegetables",
    "fruit",
    "grains",
    "fish",
    "poultry",
    "red_meat",
    "processed",
    "sweets",
    "mixed",
)

#: Local hour at or after which caffeine is worth mentioning (bedtime - 9 h).
CAFFEINE_HOUR = 14


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
    def _food_type(text: str) -> str | None:
        table = text.split("Tick table", 1)[-1]
        for word in _FOOD_WORDS:
            if re.search(rf"\b{word}\b", table):
                return word.replace("_", " ")
        return None

    # -- rules -----------------------------------------------------------

    def _decide(self, trigger: str, hour: int, text: str) -> T1Response:
        stamp = f"{hour:02d}:00"
        base = trigger.split(":", 1)[0]

        if base == "food_in_frame":
            food = self._food_type(text)
            interpretation = f"meal, {food}" if food else "meal"
            return T1Response(
                interpretation=interpretation,
                confidence=0.72,
                actions=[
                    AnnotateAction(line=f"{stamp} {interpretation}"),
                    LogInsightAction(
                        category="diet",
                        text=f"Meal logged ({food or 'type unclear'}); "
                        "tag against the Mediterranean pattern.",
                    ),
                ],
            )

        if base == "caffeine_seen":
            actions: list[Any] = [
                AnnotateAction(line=f"{stamp} caffeine in frame"),
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
                    AnnotateAction(line=f"{stamp} alcohol in frame"),
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
                    AnnotateAction(line=f"{stamp} sustained screen time"),
                    WatchAction(after_s=120, reason="still at screen?"),
                ],
            )

        if base == "people_sustained":
            return T1Response(
                interpretation="conversation with people present",
                confidence=0.66,
                actions=[
                    AnnotateAction(line=f"{stamp} people present, conversation"),
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
                    AnnotateAction(line=f"{stamp} outdoors"),
                    LogInsightAction(
                        category="nature",
                        text="Outdoor block; counts toward the weekly nature dose.",
                    ),
                ],
            )

        if base == "watch":
            return T1Response(
                interpretation="scheduled re-check",
                confidence=0.6,
                actions=[AnnotateAction(line=f"{stamp} re-check: {trigger}")],
            )

        return T1Response(
            interpretation=f"{base.replace('_', ' ')}, nothing worth saying",
            confidence=0.5,
            actions=[AnnotateAction(line=f"{stamp} {base.replace('_', ' ')}")],
        )


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
