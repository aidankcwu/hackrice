"""The real voice client's request shape, effort fallback, and usage logging.

No network: a stub stands in for ``AsyncOpenAI().responses``. The case that
matters is the one from demo night -- gpt-5.4-mini answered
``reasoning.effort="minimal"`` with a 400 on every single turn, and the client
retried without it every single turn. The client now asks for ``none`` and,
if a model rejects an effort anyway, pays for that at most once per process.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline.config import Settings
from pipeline.conversation import client as voice_client
from pipeline.conversation.client import (
    OpenAIVoiceClient,
    make_voice_client,
    reset_effort_memory,
)
from pipeline.conversation.prompts import VOICE_OBJECTIVE, build_voice_system_prompt

PAYLOAD = json.dumps(
    {
        "utterance": "Put the chips down.",
        "kind": "statement",
        "settled": {"confirmed": None, "count": None, "food_type": None, "note": None},
        "heard": True,
        "done": True,
    }
)
THREAD = [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


class StubResponses:
    def __init__(self, rejects: set[str | None] | None = None,
                 error: Exception | None = None) -> None:
        #: Efforts the "model" refuses; None in the set would refuse omission.
        self.rejects = rejects or set()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        effort = (kwargs.get("reasoning") or {}).get("effort")
        if effort in self.rejects:
            raise RuntimeError(
                "Error code: 400 - Unsupported value: 'reasoning.effort' does "
                f"not support '{effort}' with this model."
            )
        return SimpleNamespace(
            output_text=PAYLOAD,
            model="gpt-5.4-mini",
            usage=SimpleNamespace(
                input_tokens=3900,
                input_tokens_details=SimpleNamespace(cached_tokens=2048),
                output_tokens=70,
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
        )


def make(stub: StubResponses, model: str = "gpt-5.4-mini", **kw: Any) -> OpenAIVoiceClient:
    return OpenAIVoiceClient("sk-test", model, client=SimpleNamespace(responses=stub), **kw)


@pytest.fixture(autouse=True)
def _fresh_memory():
    reset_effort_memory()
    yield
    reset_effort_memory()


async def test_default_effort_is_none_and_accepted_first_time():
    stub = StubResponses()
    reply, meta = await make(stub).complete(THREAD)
    assert reply.utterance == "Put the chips down."
    assert len(stub.calls) == 1, "one HTTP call per turn, not a 400 then a retry"
    assert stub.calls[0]["reasoning"] == {"effort": "none"}
    assert meta["effort"] == "none"


async def test_minimal_is_no_longer_the_default():
    """The demo-night 400: gpt-5.1+ models do not take 'minimal'."""

    assert voice_client.DEFAULT_EFFORT != "minimal"
    assert voice_client.EFFORT_LADDER[0] == "none"


async def test_a_rejected_effort_steps_down_and_is_remembered():
    stub = StubResponses(rejects={"none"})
    first = make(stub)
    await first.complete(THREAD)
    assert [c.get("reasoning") for c in stub.calls] == [
        {"effort": "none"}, {"effort": "minimal"},
    ]
    stub.calls.clear()
    # The next turn -- and a brand-new client for the same model -- go
    # straight to the effort that worked.
    await first.complete(THREAD)
    await make(stub).complete(THREAD)
    assert [c.get("reasoning") for c in stub.calls] == [
        {"effort": "minimal"}, {"effort": "minimal"},
    ]


async def test_every_effort_rejected_omits_reasoning_once_per_process():
    stub = StubResponses(rejects={"none", "minimal", "low"})
    client = make(stub)
    _, meta = await client.complete(THREAD)
    assert "reasoning" not in stub.calls[-1]
    assert meta["effort"] is None
    stub.calls.clear()
    for _ in range(3):
        await client.complete(THREAD)
    assert len(stub.calls) == 3, "no 400s after the first turn"
    assert all("reasoning" not in c for c in stub.calls)


async def test_the_rejection_is_logged_as_a_warning_with_the_body(caplog):
    stub = StubResponses(rejects={"none"})
    with caplog.at_level(logging.WARNING, logger="pipeline.conversation.client"):
        await make(stub).complete(THREAD)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "reasoning.effort" in warnings[0] and "'none'" in warnings[0]


async def test_memory_is_per_model():
    rejecting = StubResponses(rejects={"none"})
    await make(rejecting, model="old-model").complete(THREAD)
    fresh = StubResponses()
    await make(fresh, model="gpt-5.4-mini").complete(THREAD)
    assert fresh.calls[0]["reasoning"] == {"effort": "none"}


async def test_other_errors_raise_without_a_retry():
    stub = StubResponses(error=RuntimeError("Error code: 500 - server exploded"))
    with pytest.raises(RuntimeError):
        await make(stub).complete(THREAD)
    assert len(stub.calls) == 1


async def test_effort_none_configured_sends_no_reasoning():
    stub = StubResponses()
    await make(stub, reasoning_effort=None).complete(THREAD)
    assert "reasoning" not in stub.calls[0]


async def test_usage_is_in_meta_and_on_the_log_line(caplog):
    stub = StubResponses()
    with caplog.at_level(logging.INFO, logger="pipeline.conversation.client"):
        _, meta = await make(stub).complete(THREAD)
    assert meta["usage"] == {"input": 3900, "cached": 2048, "output": 70, "reasoning": 0}
    line = next(r.getMessage() for r in caplog.records if "voice call" in r.getMessage())
    assert "in=3900" in line and "cached=2048" in line and "out=70" in line
    assert "effort=none" in line


async def test_missing_usage_does_not_break_the_turn():
    class Bare(StubResponses):
        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            return SimpleNamespace(output_text=PAYLOAD, model=None, usage=None)

    reply, meta = await make(Bare()).complete(THREAD)
    assert reply.utterance == "Put the chips down."
    assert meta["usage"] == {"input": None, "cached": None, "output": None, "reasoning": None}


def test_factory_bounds_the_http_call():
    client = make_voice_client(Settings(openai_api_key="sk-test"), "openai")
    assert isinstance(client, OpenAIVoiceClient)
    assert client.reasoning_effort == "none"
    # Read bounded at 6 s, connect at 2 s, and no retry: a retry after a 6 s
    # timeout cannot finish inside the agent's 8 s turn deadline, it only
    # holds the one conversation slot.
    assert client._client.timeout.read == 6.0
    assert client._client.timeout.connect == 2.0
    assert client._client.max_retries == 0


def test_voice_client_keeps_its_connection_warm_between_conversations():
    """The voice call is the first hop on the cue path; conversations are tens
    of seconds apart, so the SDK's 5 s keep-alive meant a handshake per line."""

    from pipeline.reasoner.client import T1_KEEPALIVE_S

    client = make_voice_client(Settings(openai_api_key="sk-test"), "openai")
    pool = client._client._client._transport._pool
    assert pool._keepalive_expiry == T1_KEEPALIVE_S > 30


# -- the prompt -------------------------------------------------------------


def test_objective_is_trimmed_but_keeps_its_rules():
    assert len(VOICE_OBJECTIVE) < 3200, "was 5.2k chars, re-sent on every line"
    for rule in (
        "ONE LINE AT A TIME",
        "THE PERSONA ABOVE IS THE BRIEF",
        "A QUESTION OPENS THE MIC",
        "BE SPECIFIC",
        "NOISE IS NOT AN ANSWER",
        "RETURN THE FACTS",
        "CLOSE WITH SOMETHING USEFUL",
        "under twelve words",
        "set heard false",
    ):
        assert rule in VOICE_OBJECTIVE, rule


def test_objective_no_longer_pushes_silence_against_the_persona():
    """The old text said 'said in the last minute, stay silent' and 'silence is
    the right answer more often than a filler line' while the persona said
    speak every time; the code already suppresses back-to-back repeats."""

    assert "last minute" not in VOICE_OBJECTIVE
    assert "more often than a filler" not in VOICE_OBJECTIVE
    assert "A HAND-OFF GETS A LINE" in VOICE_OBJECTIVE
    assert "speak every time, speak" in VOICE_OBJECTIVE


def test_system_prompt_is_byte_stable_for_the_prompt_cache():
    a = build_voice_system_prompt("Rishi. Put the treat down.", ["likes tea"])
    b = build_voice_system_prompt("Rishi. Put the treat down.", ["likes tea"])
    assert a == b and a.startswith("## Who you are talking to")
    assert a.endswith("Respond with JSON matching the required schema and nothing else.")


# -- per-turn schema -------------------------------------------------------

REPLY_THREAD = [
    *THREAD,
    {"role": "assistant", "content": [{"type": "output_text", "text": PAYLOAD}]},
    {"role": "user", "content": [{"type": "input_text", "text": "Transcript: yes"}]},
]


def _schema_of(call: dict[str, Any]) -> tuple[str, list[str]]:
    fmt = call["text"]["format"]
    assert fmt["strict"] is True
    return fmt["name"], list(fmt["schema"]["properties"])


async def test_an_opening_uses_the_two_field_schema(monkeypatch):
    monkeypatch.delenv(voice_client.OPEN_SCHEMA_ENV, raising=False)
    stub = StubResponses()
    await make(stub).complete(THREAD)
    name, props = _schema_of(stub.calls[0])
    assert name == "voice_open"
    # utterance first: the line is what the wearer is waiting on.
    assert props == ["utterance", "kind"]
    assert stub.calls[0]["text"]["format"]["schema"]["required"] == props


async def test_a_reply_keeps_the_full_schema(monkeypatch):
    monkeypatch.delenv(voice_client.OPEN_SCHEMA_ENV, raising=False)
    stub = StubResponses()
    await make(stub).complete(REPLY_THREAD)
    name, props = _schema_of(stub.calls[0])
    assert name == "voice_turn"
    assert props == ["utterance", "kind", "settled", "heard", "done"]


async def test_a_two_field_opening_parses_to_the_defaults():
    stub = StubResponses()
    client = make(stub)
    reply = client._parse(json.dumps({"utterance": "Is that yours?", "kind": "question"}))
    assert reply.utterance == "Is that yours?" and reply.kind == "question"
    assert not reply.settled.any_fact()
    assert reply.heard is True and reply.done is True


async def test_the_open_schema_can_be_turned_off_from_the_environment(monkeypatch):
    monkeypatch.setenv(voice_client.OPEN_SCHEMA_ENV, "0")
    stub = StubResponses()
    await make(stub).complete(THREAD)
    assert _schema_of(stub.calls[0])[0] == "voice_turn"
    # An explicit constructor argument wins over the environment.
    stub2 = StubResponses()
    await make(stub2, open_schema=True).complete(THREAD)
    assert _schema_of(stub2.calls[0])[0] == "voice_open"


def test_make_voice_client_takes_the_open_schema_switch_from_settings(monkeypatch):
    """VOICE_OPEN_SCHEMA is read into Settings (so .env works without
    load_dotenv) and passed explicitly; the environment fallback is not used."""

    monkeypatch.delenv(voice_client.OPEN_SCHEMA_ENV, raising=False)
    off = make_voice_client(Settings(_env_file=None, openai_api_key="sk-test",  # type: ignore[call-arg]
                                     voice_open_schema=False), "openai")
    on = make_voice_client(Settings(_env_file=None, openai_api_key="sk-test"),  # type: ignore[call-arg]
                           "openai")
    assert off.open_schema is False and on.open_schema is True


async def test_the_fake_records_the_schema_each_turn_would_use():
    fake = voice_client.FakeVoiceClient()
    opening = [{"role": "user", "content": [
        {"type": "input_text", "text": f"{voice_client.MODE_LINE} question"}]}]
    reply, _ = await fake.complete(opening)
    assert reply.kind == "question"
    thread = [*opening,
              {"role": "assistant", "content": [
                  {"type": "output_text", "text": reply.model_dump_json()}]},
              {"role": "user", "content": [{"type": "input_text", "text":
                  f"{voice_client.TRANSCRIPT_LINE} yes it's mine\n"
                  f"{voice_client.HEARD_LINE} true"}]}]
    closing, _ = await fake.complete(thread)
    assert closing.settled.confirmed is True
    assert fake.formats == ["voice_open", "voice_turn"]
