"""Gemini token usage is recorded per call and never costs a call.

Output tokens are the lever on Gemini latency (~8.7 ms each), so the schema trims
in the plan are measured from these numbers. Usage metadata is optional in the
SDK, so every read is defensive: missing metadata records nothing and raises
nothing, and the tagger's stats still work with clients that record no usage.
"""

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import Any

from longevity import vlm


def response(text: str = '{"scene": "office"}', usage: Any = "default") -> SimpleNamespace:
    if usage == "default":
        usage = SimpleNamespace(prompt_token_count=1290, candidates_token_count=160)
    return SimpleNamespace(text=text, usage_metadata=usage)


class FakeModels:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = responses

    async def generate_content(self, **_: Any) -> SimpleNamespace:
        return self.responses.pop(0)


def gemini(responses: list[SimpleNamespace]) -> vlm.GeminiClient:
    """A GeminiClient without a key or network: only the SDK call is faked."""

    client = object.__new__(vlm.GeminiClient)
    client._client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels(responses)))
    client._model = "test"
    client._schema = {"type": "OBJECT", "properties": {}}
    client.usage = deque(maxlen=200)
    return client


def test_read_usage_is_defensive() -> None:
    assert vlm.read_usage(response()) == (1290, 160)
    assert vlm.read_usage(response(usage=None)) is None
    assert vlm.read_usage(SimpleNamespace(text="{}")) is None
    assert vlm.read_usage(response(usage=SimpleNamespace(
        prompt_token_count=None, candidates_token_count=None))) is None
    assert vlm.read_usage(response(usage=SimpleNamespace(
        prompt_token_count=900, candidates_token_count=None))) == (900, 0)

    class Exploding:
        @property
        def usage_metadata(self):
            raise RuntimeError("sdk changed shape")

    assert vlm.read_usage(Exploding()) is None


async def test_gemini_tag_records_usage_and_keeps_its_return_type() -> None:
    client = gemini([
        response(usage=SimpleNamespace(prompt_token_count=1200, candidates_token_count=150)),
        response(usage=None),  # metadata missing: nothing recorded, nothing raised
        response(usage=SimpleNamespace(prompt_token_count=1300, candidates_token_count=170)),
        response(usage=SimpleNamespace(prompt_token_count=1250, candidates_token_count=190)),
    ])
    for _ in range(4):
        assert await client.tag(b"jpeg") == {"scene": "office"}
    assert list(client.usage) == [(1200, 150), (1300, 170), (1250, 190)]
    assert vlm.token_stats(client.usage) == {
        "token_calls": 3, "prompt_tokens_p50": 1250, "output_tokens_p50": 170,
    }


async def test_tagger_stats_expose_output_token_p50() -> None:
    client = gemini([response(usage=SimpleNamespace(
        prompt_token_count=1200 + i, candidates_token_count=100 + i)) for i in range(5)])
    tagger = vlm.T0Tagger(client, budget_s=1.0, log_every=0)
    await tagger.start()
    try:
        for i in range(5):
            tagger.offer(float(i), b"x")
            for _ in range(20):
                await asyncio.sleep(0)
    finally:
        await tagger.aclose()
    stats = tagger.stats()
    assert stats["token_calls"] == 5
    assert stats["output_tokens_p50"] == 102 and stats["prompt_tokens_p50"] == 1202
    assert "tok_out_p50=102" in tagger.stats_line()


def test_stats_without_usage_are_zeros_not_errors() -> None:
    tagger = vlm.T0Tagger(vlm.FakeClient(latency=0.0), log_every=0)
    stats = tagger.stats()
    assert (stats["token_calls"], stats["output_tokens_p50"]) == (0, 0)
    assert "tok_out_p50" not in tagger.stats_line()

    class Broken:
        @property
        def usage(self):
            raise RuntimeError("boom")

    assert vlm.T0Tagger(Broken(), log_every=0).stats()["token_calls"] == 0  # type: ignore[arg-type]
    assert vlm.T0Tagger(None, log_every=0).stats()["token_calls"] == 0


def test_max_in_flight_env_switch() -> None:
    assert vlm._env_max_in_flight({}) == 2
    assert vlm._env_max_in_flight({"VLM_MAX_IN_FLIGHT": "1"}) == 1
    assert vlm._env_max_in_flight({"T0_MAX_IN_FLIGHT": "1"}) == 1
    assert vlm._env_max_in_flight({"VLM_MAX_IN_FLIGHT": "9"}) == 2
    assert vlm._env_max_in_flight({"VLM_MAX_IN_FLIGHT": "x"}) == 2
