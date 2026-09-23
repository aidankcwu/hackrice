"""The Jev client and its fake (docs/PERCEPTION.md, "Decider and writers").

No network: the request body is built by the SDK's own ``prepare_system_one``
and responses are hand-made ``SystemOneResponse`` objects.
"""

from __future__ import annotations

import json
import logging

import httpx2
import pytest
from typesafe_sdk import SystemOneResponse, TypeSafeAPITimeoutError
from typesafe_sdk._core.config import Config
from typesafe_sdk._core.endpoints import prepare_system_one
from typesafe_sdk._core.errors import TypeSafeRateLimitError, api_error

from pipeline.reasoner.decider import (
    ACTIONS,
    DeciderError,
    FakeDecider,
    JevDecider,
    Verdict,
    build_questions,
    make_decider,
    verdict_from_response,
)
from pipeline.reasoner.decider_settings import DEFAULT_TOPICS, DeciderSettings

TOPICS = ["caffeine", "food", "screen", "other"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(DeciderSettings.model_fields) + ["T1_MODEL"]:
        for key in (name.upper(), name):
            monkeypatch.delenv(key, raising=False)
    for key in ("TYPESAFE_BASE_URL", "TYPESAFE_DEFAULT_MODEL"):
        monkeypatch.delenv(key, raising=False)


def _response_json(probs: dict[str, float], *, drop: str | None = None,
                   wrong: str | None = None) -> str:
    answers: dict = {a: {"type": "noul", "noul": probs.get(a, 0.0)} for a in ACTIONS}
    answers["topic"] = {
        "type": "choice", "choice": "screen", "confidence": 0.8,
        "probabilities": {"caffeine": 0.05, "food": 0.05, "screen": 0.8, "other": 0.1},
    }
    answers["urgency"] = {
        "type": "score", "score": 1.4, "confidence": 0.7,
        "legend": {"0": "can wait", "1": "soon", "2": "now"},
        "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
    }
    if drop:
        del answers[drop]
    if wrong:
        answers[wrong] = {"type": "noul", "noul": 0.5}
    return json.dumps({
        "model": "jev-1", "usage": {"input_tokens": 321, "output_tokens": 12},
        "answers": answers,
    })


def _response(probs: dict[str, float], **kw) -> SystemOneResponse:
    return SystemOneResponse.model_validate_json(_response_json(probs, **kw))


def _verdict(probs: dict[str, float]) -> Verdict:
    return Verdict(probabilities={a: probs.get(a, 0.0) for a in ACTIONS}, topic="other",
                   topic_confidence=1.0, urgency=0.0, model="m", usage={}, latency_ms=1.0)


# (a) the real request body, built by the SDK
def test_request_body_through_sdk() -> None:
    config = Config.resolve("dummy-key", None, "jev-latest", None, None)
    state = {"trigger": {"name": "screen_long", "reason": "x"}, "recent": []}
    request = prepare_system_one(config, state, build_questions(TOPICS), None, None,
                                 None, None, SystemOneResponse)
    body = json.loads(request.content)
    assert body["state"] == state
    assert body["model"] == "jev-latest"
    qs = body["questions"]
    for a in ACTIONS:
        assert qs[a]["type"] == "noul"
        assert qs[a]["instructions"]
        assert set(qs[a]["criteria"]) == {"true", "false"}
    assert [n for n, q in qs.items() if q["type"] == "choice"] == ["topic"]
    assert set(qs["topic"]["criteria"]) == set(TOPICS)
    assert [n for n, q in qs.items() if q["type"] == "score"] == ["urgency"]
    assert qs["urgency"]["criteria"] == ["can wait", "soon", "now"]
    assert request.headers["authorization"] == "Bearer dummy-key"


def test_product_criteria_wording() -> None:
    qs = build_questions(DEFAULT_TOPICS)
    assert qs["speak"].criteria["true"] == (
        "A short spoken remark right now would help the wearer make a healthier next choice")
    assert qs["speak"].criteria["false"] == "Nothing worth saying, or it would interrupt the wearer"
    assert qs["remember"].criteria["true"] == (
        "This reveals a lasting fact about the wearer's habits or preferences")
    assert qs["annotate"].criteria["true"] == "Worth one line in today's running summary"


# (b) response -> Verdict
def test_verdict_from_response_maps_fields() -> None:
    probs = {a: round(0.1 * i, 1) for i, a in enumerate(ACTIONS)}
    v = verdict_from_response(_response(probs), latency_ms=87.5)
    assert v.probabilities == probs
    assert v.topic == "screen"
    assert v.topic_confidence == 0.8
    assert v.urgency == 1.4
    assert v.model == "jev-1"
    assert v.usage == {"input_tokens": 321, "output_tokens": 12}
    assert v.latency_ms == 87.5


# (c) fires
def test_fires_respects_thresholds_and_order() -> None:
    v = _verdict({"look": 0.9, "speak": 0.7, "ask": 0.74, "annotate": 0.0, "remember": 0.2})
    thresholds = DeciderSettings().thresholds()
    assert v.fires(thresholds) == ["annotate", "speak", "look"]
    assert _verdict({}).fires(thresholds) == ["annotate"]
    assert _verdict({"act": 0.8}).fires({"act": 0.8}) == ["annotate", "act"]


# (d) uncertain
def test_uncertain_band() -> None:
    assert _verdict({"speak": 0.9, "ask": 0.5, "act": 0.45}).uncertain(0.4, 0.6) == "ask"
    assert _verdict({"speak": 0.4}).uncertain(0.4, 0.6) == "speak"
    assert _verdict({"speak": 0.61, "ask": 0.39, "look": 0.5}).uncertain(0.4, 0.6) is None
    assert _verdict({"look": 0.5}).uncertain(0.4, 0.6, actions=("look",)) == "look"


# (e) fake
async def test_fake_scripted() -> None:
    fake = FakeDecider({"speak": 0.9}, topic="food", urgency=2.0, latency_ms=3.0)
    v = await fake.decide({"s": 1})
    assert v.probabilities["speak"] == 0.9
    assert v.probabilities["act"] == 0.0 and set(v.probabilities) == set(ACTIONS)
    assert (v.topic, v.urgency, v.latency_ms) == ("food", 2.0, 3.0)
    await fake.decide({"s": 2})
    assert fake.calls == [{"s": 1}, {"s": 2}]
    await fake.aclose()


async def test_fake_callable() -> None:
    fake = FakeDecider(lambda state: _verdict({"ask": state["p"]}))
    v = await fake.decide({"p": 0.55})
    assert v.probabilities["ask"] == 0.55
    assert fake.calls == [{"p": 0.55}]
    assert (await FakeDecider().decide({})).probabilities == {a: 0.0 for a in ACTIONS}


# (f) make_decider
async def test_make_decider_branches(caplog: pytest.LogCaptureFixture) -> None:
    assert make_decider(DeciderSettings(decider="clerk", typesafe_api_key="k")) is None
    with caplog.at_level(logging.WARNING):
        assert make_decider(DeciderSettings(decider="jev")) is None
    assert "DECIDER=jev but TYPESAFE_API_KEY is unset; using the clerk" in caplog.text
    d = make_decider(DeciderSettings(decider="jev", typesafe_api_key="k"))
    assert isinstance(d, JevDecider)
    await d.aclose()


# (g) SDK errors become DeciderError with the status
async def test_jev_api_error_becomes_decider_error() -> None:
    d = JevDecider(DeciderSettings(decider="jev", typesafe_api_key="k"))
    err = api_error(429, {"error": "slow down"}, httpx2.Headers())
    assert isinstance(err, TypeSafeRateLimitError)

    async def boom(**_kw):
        raise err

    d.client.system_one = boom
    with pytest.raises(DeciderError) as info:
        await d.decide({"s": 1})
    assert info.value.status == 429
    assert info.value.__cause__ is err
    await d.aclose()


async def test_jev_timeout_becomes_decider_error() -> None:
    d = JevDecider(DeciderSettings(decider="jev", typesafe_api_key="k"))

    async def slow(**_kw):
        raise TypeSafeAPITimeoutError(2.0)

    d.client.system_one = slow
    with pytest.raises(DeciderError) as info:
        await d.decide({})
    assert info.value.status is None
    await d.aclose()


async def test_jev_decide_success() -> None:
    d = JevDecider(DeciderSettings(decider="jev", typesafe_api_key="k"))
    seen = {}

    async def ok(**kw):
        seen.update(kw)
        return _response({"speak": 0.8})

    d.client.system_one = ok
    v = await d.decide({"s": 1})
    assert v.probabilities["speak"] == 0.8 and v.latency_ms >= 0
    assert seen["state"] == {"s": 1} and seen["questions"] is d.questions
    await d.aclose()


# (h) missing or wrong-typed answers
def test_missing_action_raises() -> None:
    with pytest.raises(DeciderError, match="look"):
        verdict_from_response(_response({}, drop="look"), 1.0)


def test_wrong_typed_answer_raises() -> None:
    with pytest.raises(DeciderError, match="urgency"):
        verdict_from_response(_response({}, wrong="urgency"), 1.0)
