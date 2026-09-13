"""Model clients for the voice agent (docs/CONVERSATION_DESIGN.md §4).

:class:`OpenAIVoiceClient` is the real one -- the same Responses API pattern the
clerk uses, strict JSON schema, minimal reasoning effort, one call per turn.
:class:`FakeVoiceClient` is a deterministic stand-in so a key-less run still
opens a conversation, speaks, listens, and closes.

The *thread* is the whole state: the Responses API ``input`` list, kept in
memory for the life of one conversation and discarded at close. Both clients
read it the same way, so the fake exercises the real message shapes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Literal, Protocol, runtime_checkable

from ..config import Settings
from ..reasoner.client import _strip_fences
from .schema import VOICE_TEXT_FORMAT, VoiceReply, VoiceSettled

log = logging.getLogger(__name__)

__all__ = [
    "VoiceClient",
    "OpenAIVoiceClient",
    "FakeVoiceClient",
    "make_voice_client",
    "MODE_LINE",
    "TRANSCRIPT_LINE",
    "HEARD_LINE",
]

#: The three lines the fake reads out of the user turns. The real model reads
#: them too -- they are ordinary prose -- but only the fake depends on the
#: exact spelling, which is why they are named here rather than inlined.
MODE_LINE = "Hand-off mode:"
TRANSCRIPT_LINE = "Transcript:"
HEARD_LINE = "Heard by the phone:"


@runtime_checkable
class VoiceClient(Protocol):
    """One structured call per conversational turn."""

    async def complete(
        self, thread: list[dict[str, Any]]
    ) -> tuple[VoiceReply, dict[str, Any]]:
        """Return the parsed turn plus meta (``model``, ``latency_ms``)."""
        ...


class OpenAIVoiceClient:
    """The real voice call: Responses API, strict JSON schema, inline images."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float | None = None,
        client: Any | None = None,
        reasoning_effort: str | None = "minimal",
    ) -> None:
        if not api_key:
            raise RuntimeError("OpenAIVoiceClient requires an API key")
        self.model = model
        self.reasoning_effort = reasoning_effort
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout)

    async def complete(
        self, thread: list[dict[str, Any]]
    ) -> tuple[VoiceReply, dict[str, Any]]:
        started = time.perf_counter()
        kwargs: dict[str, Any] = dict(
            model=self.model, input=list(thread), text=VOICE_TEXT_FORMAT
        )
        # Wording a single sentence is not a puzzle, and the wearer is standing
        # there waiting through the latency. Retry without it for models that
        # reject the parameter, exactly as the clerk's client does.
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
        meta = {
            "model": getattr(response, "model", None) or self.model,
            "latency_ms": latency_ms,
            "raw_len": len(raw),
        }
        return self._parse(raw), meta

    @staticmethod
    def _parse(raw: str) -> VoiceReply:
        try:
            return VoiceReply.model_validate(json.loads(raw))
        except Exception as first:
            log.warning("voice reply did not parse (%s); retrying de-fenced", first)
            return VoiceReply.model_validate(json.loads(_strip_fences(raw)))


# -- the fake -------------------------------------------------------------

#: Checked first and on its own: a denial almost always arrives wrapped in
#: affirmative words ("mine, but I'm not drinking it"), and reading one as a
#: yes writes a drink into the day that nobody had.
_NO_RE = re.compile(
    r"\b(no|nope|nah|not mine|isn'?t mine|not my|someone else|somebody else|"
    r"not drinking|not having|not eating|didn'?t|don'?t|isn'?t|wasn'?t)\b",
    re.IGNORECASE,
)
_YES_RE = re.compile(
    r"\b(yes|yeah|yeh|yep|yup|sure|mine|correct|it is|i am|i did)\b",
    re.IGNORECASE,
)


class FakeVoiceClient:
    """Deterministic turns over the same thread the real model reads.

    Opening turn: a question hand-off asks "Is that yours?", a statement
    hand-off says "Noted." and closes. Reply turn: one statement, "Got it.",
    with ``settled.confirmed`` read off a yes or a no in the transcript. A
    transcript of a single word that is neither is not an answer -- ``heard``
    is false and the conversation closes in silence.
    """

    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.last_thread: list[dict[str, Any]] | None = None

    async def complete(
        self, thread: list[dict[str, Any]]
    ) -> tuple[VoiceReply, dict[str, Any]]:
        self.calls += 1
        self.last_thread = list(thread)
        started = time.perf_counter()
        reply = self._decide(thread)
        meta = {
            "model": self.model,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
        return reply, meta

    # -- thread reading --------------------------------------------------

    @staticmethod
    def _last_user_text(thread: list[dict[str, Any]]) -> str:
        for message in reversed(thread):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            return "\n".join(
                str(item.get("text", ""))
                for item in content or []
                if item.get("type") == "input_text"
            )
        return ""

    @staticmethod
    def _is_reply_turn(thread: list[dict[str, Any]]) -> bool:
        return any(m.get("role") == "assistant" for m in thread)

    @staticmethod
    def _field(text: str, prefix: str) -> str:
        for line in text.splitlines():
            if line.startswith(prefix):
                return line[len(prefix):].strip()
        return ""

    # -- rules -----------------------------------------------------------

    def _decide(self, thread: list[dict[str, Any]]) -> VoiceReply:
        text = self._last_user_text(thread)
        if not self._is_reply_turn(thread):
            if self._field(text, MODE_LINE).startswith("question"):
                return VoiceReply(
                    utterance="Is that yours?", kind="question", done=False
                )
            return VoiceReply(utterance="Noted.", kind="statement", done=True)

        if self._field(text, HEARD_LINE).startswith("false"):
            # The phone opened the microphone and nothing came back. There is
            # nothing to read: close in silence rather than repeat the
            # question at somebody who already did not answer it (§3).
            return VoiceReply(utterance="", kind="statement", heard=False, done=True)

        transcript = self._field(text, TRANSCRIPT_LINE).strip()
        probe = transcript.replace("’", "'")
        confirmed: bool | None = None
        if _NO_RE.search(probe):
            confirmed = False
        elif _YES_RE.search(probe):
            confirmed = True

        if confirmed is None and len(probe.split()) <= 1:
            # A single word that is neither a yes nor a no is noise, an
            # interface word, or a fragment: not an answer (§3).
            return VoiceReply(utterance="", kind="statement", heard=False, done=True)

        return VoiceReply(
            utterance="Got it.",
            kind="statement",
            settled=VoiceSettled(confirmed=confirmed, note=transcript or None),
            heard=True,
            done=True,
        )


# -- factory --------------------------------------------------------------


def make_voice_client(
    settings: Settings, mode: Literal["openai", "fake"]
) -> VoiceClient:
    """Build a voice client. ``mode`` is explicit -- no silent fallback.

    A key-less ``openai`` run raises at startup rather than quietly speaking in
    a rule table's voice for the whole demo.
    """

    if mode == "fake":
        return FakeVoiceClient()
    if mode != "openai":
        raise ValueError(f"unknown voice client mode: {mode!r}")
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set; use mode='fake' for a key-less run"
        )
    return OpenAIVoiceClient(settings.openai_api_key, settings.t1_model)
