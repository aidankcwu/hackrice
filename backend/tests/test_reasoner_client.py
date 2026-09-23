"""The real T1 client's transport and telemetry, without touching the network.

The latency work depends on three things the fake client cannot show: the
connection is pooled with a keep-alive longer than the gap between wake-ups,
a stuck call gives up in seconds with at most one retry, and every call logs
its token counts -- including the cached ones that prove the prompt cache hits.
"""

from __future__ import annotations

import contextlib
import json
import logging
from types import SimpleNamespace
from typing import Any

from pipeline.models import PendingQuestion
from pipeline.reasoner.client import (
    T1_CONNECT_TIMEOUT_S,
    T1_KEEPALIVE_S,
    T1_MAX_RETRIES,
    T1_TIMEOUT_S,
    OpenAIReasonerClient,
    usage_counts,
)

PAYLOAD = json.dumps(
    {
        "interpretation": "coffee cup beside the laptop",
        "confidence": 0.8,
        "actions": [{"type": "annotate", "line": "coffee at the desk"}],
    }
)

USAGE = {
    "input_tokens": 4200,
    "input_tokens_details": {"cached_tokens": 3072},
    "output_tokens": 110,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 4310,
}


class StubResponses:
    def __init__(self, reject_reasoning: bool = False) -> None:
        self.reject_reasoning = reject_reasoning
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.reject_reasoning and "reasoning" in kwargs:
            raise RuntimeError("Unsupported parameter: 'reasoning'")
        return SimpleNamespace(output_text=PAYLOAD, model="stub-model", usage=USAGE)


class StubClient:
    def __init__(self, reject_reasoning: bool = False) -> None:
        self.responses = StubResponses(reject_reasoning)


def test_default_client_reuses_a_pooled_connection_with_short_timeouts():
    client = OpenAIReasonerClient("sk-test", "gpt-test")
    sdk = client._client

    assert sdk.max_retries == T1_MAX_RETRIES == 1
    assert sdk.timeout.read == T1_TIMEOUT_S == 8.0
    assert sdk.timeout.connect == T1_CONNECT_TIMEOUT_S
    # The keep-alive must outlast the ~9 s p50 gap between wake-ups, or each
    # call pays a new TLS handshake (httpx's own default is 5 s).
    pool = sdk._client._transport._pool
    assert pool._keepalive_expiry == T1_KEEPALIVE_S
    assert T1_KEEPALIVE_S > 30


def test_an_explicit_timeout_still_wins():
    client = OpenAIReasonerClient("sk-test", "gpt-test", timeout=3.0, max_retries=0)
    assert client._client.timeout.read == 3.0
    assert client._client.max_retries == 0


def test_usage_counts_flattens_the_responses_usage_block():
    assert usage_counts(USAGE) == {
        "input": 4200, "cached": 3072, "output": 110, "reasoning": 0,
    }
    assert usage_counts(None) == {
        "input": None, "cached": None, "output": None, "reasoning": None,
    }


async def test_complete_logs_token_usage_including_cached(caplog):
    client = OpenAIReasonerClient("sk-test", "gpt-test", client=StubClient())

    with caplog.at_level(logging.INFO, logger="pipeline.reasoner.client"):
        resp, meta = await client.complete([{"role": "user", "content": "x"}])

    assert resp.interpretation == "coffee cup beside the laptop"
    assert meta["usage"] == USAGE, "the full usage block is still passed on"
    assert meta["tokens"] == {
        "input": 4200, "cached": 3072, "output": 110, "reasoning": 0,
    }
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("T1 ")]
    assert "in=4200" in line and "cached=3072" in line and "out=110" in line


async def test_rejected_reasoning_is_retried_without_it_and_logged(caplog):
    stub = StubClient(reject_reasoning=True)
    client = OpenAIReasonerClient("sk-test", "gpt-test", client=stub)

    with caplog.at_level(logging.WARNING, logger="pipeline.reasoner.client"):
        resp, _ = await client.complete([{"role": "user", "content": "x"}])

    assert resp.confidence == 0.8
    assert "reasoning" in stub.responses.calls[0]
    assert "reasoning" not in stub.responses.calls[1]
    assert any("rejected reasoning" in r.getMessage() for r in caplog.records)


class _RejectsEffortOnce:
    """gpt-5.4-mini on demo night: ``none`` is fine, ``minimal`` is a 400."""

    def __init__(self, rejected: str) -> None:
        self.rejected = rejected
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if (kwargs.get("reasoning") or {}).get("effort") == self.rejected:
            raise RuntimeError(
                "Error code: 400 - Unsupported value: 'reasoning.effort' does "
                f"not support '{self.rejected}' with this model."
            )
        return SimpleNamespace(output_text=PAYLOAD, usage=None, model="m")


async def test_t1_defaults_to_the_lowest_effort_the_model_accepts():
    responses = _RejectsEffortOnce("minimal")
    client = OpenAIReasonerClient("sk-test", "gpt-5.4-mini",
                                  client=SimpleNamespace(responses=responses))
    await client.complete([{"role": "user", "content": "x"}])
    assert [c.get("reasoning") for c in responses.calls] == [{"effort": "none"}]


async def test_an_effort_rejection_is_paid_once_per_process_not_per_call():
    """73 of 151 /v1/responses calls on 13 Sep were this 400: every clerk call
    re-sent the rejected effort and paid a second round trip."""

    responses = _RejectsEffortOnce("none")
    client = OpenAIReasonerClient("sk-test", "gpt-5.4-mini",
                                  client=SimpleNamespace(responses=responses))
    await client.complete([{"role": "user", "content": "x"}])
    assert len(responses.calls) == 2
    await client.complete([{"role": "user", "content": "x"}])
    assert len(responses.calls) == 3
    assert responses.calls[-1]["reasoning"] == {"effort": "minimal"}


async def test_the_answer_parser_learns_from_the_clerk():
    """One ladder, one memory: the parser does not pay the 400 again."""

    from pipeline.reasoner.client import OpenAIAnswerParser

    responses = _RejectsEffortOnce("none")
    clerk = OpenAIReasonerClient("sk-test", "gpt-5.4-mini",
                                 client=SimpleNamespace(responses=responses))
    await clerk.complete([{"role": "user", "content": "x"}])
    parser_calls = _RejectsEffortOnce("none")
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini",
                                client=SimpleNamespace(responses=parser_calls))
    question = PendingQuestion(
        id="q_00000001", created_t=0.0, expires_t=25.0, episode_id="ep_1",
        question="That yours?", answer_kind="yes_no", fills="confirmed",
    )
    with contextlib.suppress(Exception):  # PAYLOAD is a T1 reply, not an AnswerParse
        await parser.parse(question, "yes")
    assert len(parser_calls.calls) == 1
    assert parser_calls.calls[0]["reasoning"] == {"effort": "minimal"}
