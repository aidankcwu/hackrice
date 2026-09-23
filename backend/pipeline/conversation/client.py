"""Model clients for the voice agent (docs/CONVERSATION_DESIGN.md §4).

:class:`OpenAIVoiceClient` is the real one -- the same Responses API pattern the
clerk uses, strict JSON schema, the lowest reasoning effort the model accepts,
one call per turn.
:class:`FakeVoiceClient` is a deterministic stand-in so a key-less run still
opens a conversation, speaks, listens, and closes.

The *thread* is the whole state: the Responses API ``input`` list, kept in
memory for the life of one conversation and discarded at close. Both clients
read it the same way, so the fake exercises the real message shapes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Literal, Protocol, runtime_checkable

from ..config import Settings
from ..reasoner.client import _strip_fences, _t1_http
# The effort ladder lives in ``reasoner.effort`` so the clerk and the answer
# parser share it (and what each learns about the model). Re-exported here,
# where it started, for the callers and tests that import it from here.
from ..reasoner.effort import (
    _ACCEPTED_EFFORT,
    DEFAULT_EFFORT,
    EFFORT_LADDER,
    _is_effort_rejection,
    _next_effort,
    create_with_effort,
    effort_for,
    reset_effort_memory,
)
from .schema import (
    VOICE_OPEN_TEXT_FORMAT,
    VOICE_TEXT_FORMAT,
    VoiceReply,
    VoiceSettled,
)

log = logging.getLogger(__name__)

__all__ = [
    "VoiceClient",
    "OpenAIVoiceClient",
    "FakeVoiceClient",
    "make_voice_client",
    "MODE_LINE",
    "TRANSCRIPT_LINE",
    "HEARD_LINE",
    "OPEN_SCHEMA_ENV",
    "is_reply_turn",
    "text_format_for",
    "EFFORT_LADDER",
    "DEFAULT_EFFORT",
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


#: HTTP timeout for one voice turn. With no SDK retry (``VOICE_MAX_RETRIES``)
#: a stalled call frees the one conversation slot at 6 s instead of holding it
#: to the agent's 8 s turn deadline.
VOICE_TIMEOUT_S = 6.0
#: No retry. The turn deadline is 8 s (``agent.TURN_DEADLINE_S``): a retry
#: after a 6 s timeout had 2 s left and was almost always cancelled, so all it
#: did was hold the slot while every prop shown meanwhile was dropped as
#: ``conversation_active``. A lost line is cheaper than a mute glasses.
VOICE_MAX_RETRIES = 0


#: Stage kill switch for the short opening schema: ``VOICE_OPEN_SCHEMA=0`` in
#: the environment (or ``.env`` exported before start) sends every turn the
#: full schema again, exactly as before, without a code change.
OPEN_SCHEMA_ENV = "VOICE_OPEN_SCHEMA"


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def is_reply_turn(thread: list[dict[str, Any]]) -> bool:
    """A reply turn is any turn after the model has already spoken once.

    One definition for the real client (which picks the schema by it) and the
    fake (which picks its rules by it), so the two can never disagree about
    which turn is the opening.
    """

    return any(m.get("role") == "assistant" for m in thread)


def text_format_for(
    thread: list[dict[str, Any]], open_schema: bool = True
) -> dict[str, Any]:
    """The strict schema for this turn: short for an opening, full for a reply."""

    if open_schema and not is_reply_turn(thread):
        return VOICE_OPEN_TEXT_FORMAT
    return VOICE_TEXT_FORMAT


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _usage(response: Any) -> dict[str, int | None]:
    """Token counts for the log: what was sent, what the cache covered, what
    came back, and how much of that was hidden reasoning. Without these the
    prompt-cache hit rate and the effort setting were both unmeasurable."""

    usage = _field(response, "usage")
    return {
        "input": _field(usage, "input_tokens"),
        "cached": _field(_field(usage, "input_tokens_details"), "cached_tokens"),
        "output": _field(usage, "output_tokens"),
        "reasoning": _field(
            _field(usage, "output_tokens_details"), "reasoning_tokens"
        ),
    }


class OpenAIVoiceClient:
    """The real voice call: Responses API, strict JSON schema, inline images."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float | None = None,
        client: Any | None = None,
        reasoning_effort: str | None = DEFAULT_EFFORT,
        max_retries: int | None = None,
        open_schema: bool | None = None,
    ) -> None:
        if not api_key:
            raise RuntimeError("OpenAIVoiceClient requires an API key")
        self.model = model
        self.reasoning_effort = reasoning_effort
        #: Short schema on the opening turn (``VOICE_OPEN_SCHEMA=0`` turns it off).
        self.open_schema = (
            _env_flag(OPEN_SCHEMA_ENV) if open_schema is None else bool(open_schema)
        )
        if client is not None:
            self._client = client
        else:
            from openai import AsyncOpenAI

            extra: dict[str, Any] = {}
            if max_retries is not None:
                extra["max_retries"] = max_retries
            # The clerk's pooled HTTP client (2 min keep-alive, 2 s connect):
            # the voice call is now the first network hop on the cue path, and
            # with the SDK's 5 s keep-alive every opening turn -- tens of
            # seconds apart -- paid a fresh TCP+TLS handshake first.
            http_timeout, http_client = _t1_http(
                VOICE_TIMEOUT_S if timeout is None else timeout
            )
            self._client = AsyncOpenAI(
                api_key=api_key, timeout=http_timeout, http_client=http_client,
                **extra,
            )

    def _effort(self) -> str | None:
        return effort_for(self.model, self.reasoning_effort)

    async def complete(
        self, thread: list[dict[str, Any]]
    ) -> tuple[VoiceReply, dict[str, Any]]:
        started = time.perf_counter()
        # Wording a single sentence is not a puzzle, and the wearer is standing
        # there waiting through the latency: ask for the lowest effort, and if
        # the model rejects it, step down the ladder and remember where it
        # landed so the next turn goes straight there.
        response, effort = await create_with_effort(
            self._client, self.model, self.reasoning_effort,
            dict(model=self.model, input=list(thread),
                 text=text_format_for(thread, self.open_schema)),
            label="voice",
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        raw = getattr(response, "output_text", None) or ""
        usage = _usage(response)
        meta = {
            "model": getattr(response, "model", None) or self.model,
            "latency_ms": latency_ms,
            "raw_len": len(raw),
            "effort": effort,
            "usage": usage,
        }
        log.info(
            "voice call · %s · effort=%s · %d ms · tokens in=%s cached=%s "
            "out=%s reasoning=%s",
            meta["model"], effort, latency_ms, usage["input"], usage["cached"],
            usage["output"], usage["reasoning"],
        )
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
        #: The ``text=`` schema the real client would have sent for each call,
        #: so a key-less run (and the agent's tests) can see the opening turn
        #: take the short schema and the reply turn the full one.
        self.formats: list[str] = []

    async def complete(
        self, thread: list[dict[str, Any]]
    ) -> tuple[VoiceReply, dict[str, Any]]:
        self.calls += 1
        self.last_thread = list(thread)
        self.formats.append(text_format_for(thread)["format"]["name"])
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
        return is_reply_turn(thread)

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
    # A bounded HTTP timeout and no SDK retry: the agent's own turn deadline
    # (8 s) is the outer guard, and a retry after a 6 s timeout cannot finish
    # inside it (see VOICE_MAX_RETRIES).
    return OpenAIVoiceClient(
        settings.openai_api_key, settings.t1_model, timeout=VOICE_TIMEOUT_S,
        max_retries=VOICE_MAX_RETRIES,
        # Explicit, from Settings: .env is read into Settings, not exported, so
        # the client's own environment fallback would miss a value set there.
        open_schema=settings.voice_open_schema,
    )
