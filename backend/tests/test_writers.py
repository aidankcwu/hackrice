"""Writers over a stubbed Responses transport: no network, no key."""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline.reasoner.client import FakeReasonerClient, OpenAIReasonerClient
from pipeline.reasoner.decider import ACTIONS, Verdict
from pipeline.reasoner.decider_settings import DeciderSettings
from pipeline.reasoner.schema import ANNOTATE_MAX_CHARS, REMEMBER_MAX_CHARS
from pipeline.reasoner.writers import FakeWriters, Writers, make_writers

STATE = {
    "trigger": {"name": "caffeine_seen", "reason": "coffee cup in hand for 4 ticks"},
    "recent": [{"age_s": 0.0, "true": ["cup_visible"], "caption": "coffee at desk"}],
    "episodes": [],
    "today": ["coffee at 09:10"],
    "persona": "",
    "trends": "",
    "clock": {"local": "15:20", "weekday": "Tuesday"},
}
VERDICT = Verdict(
    probabilities={a: 0.9 for a in ACTIONS}, topic="caffeine",
    topic_confidence=0.8, urgency=0.3, model="fake",
)
FALLBACK = "caffeine_seen: coffee cup in hand for 4 ticks"


class StubResponses:
    """Returns ``reply`` (a dict is JSON-encoded), raises it if an exception,
    or sleeps ``delay`` seconds first."""

    def __init__(self, reply: Any = None, delay: float = 0.0) -> None:
        self.reply = reply
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(output_text=text, model="stub", usage=None)


def make(reply: Any = None, delay: float = 0.0, timeout_s: float = 1.0):
    responses = StubResponses(reply, delay)
    transport = OpenAIReasonerClient(
        "sk-test", "clerk-model", client=SimpleNamespace(responses=responses)
    )
    settings = DeciderSettings(writer_model="writer-model", writer_timeout_s=timeout_s)
    return Writers(transport, settings), responses


CASES = [
    ("summary_line", {"line": "coffee at the desk"}, "coffee at the desk"),
    ("insight", {"category": "Caffeine", "text": "second coffee by 15:20"},
     ("caffeine", "second coffee by 15:20")),
    ("persona_fact", {"fact": "drinks coffee at the desk"}, "drinks coffee at the desk"),
    ("handoff_topic", {"topic": "coffee after 3pm, bedtime 11pm"},
     "coffee after 3pm, bedtime 11pm"),
    ("question", {"text": "whether the cup is the wearer's", "answer_kind": "yes_no",
                  "fills": "confirmed"},
     ("whether the cup is the wearer's", "yes_no", "confirmed")),
    ("watch_condition", {"after_s": 60, "condition": None}, (60, None)),
    ("look_question", {"question": "Is the cup coffee or tea?"}, "Is the cup coffee or tea?"),
]
DEFAULTS = {name: None for name, _, _ in CASES} | {"summary_line": FALLBACK}


@pytest.mark.parametrize("name,reply,expected", CASES)
async def test_each_writer_sends_only_its_own_schema_on_the_writer_model(
    name, reply, expected
):
    writers, responses = make(reply)
    assert await getattr(writers, name)(STATE, VERDICT) == expected

    (call,) = responses.calls
    assert call["model"] == "writer-model"
    fmt = call["text"]["format"]
    assert fmt["name"] == name and fmt["strict"] is True
    assert set(fmt["schema"]["properties"]) == set(reply)
    system, user = call["input"]
    assert system["role"] == "system"
    payload = json.loads(user["content"])
    assert payload["state"] == STATE
    assert payload["topic"] == "caffeine"
    assert payload["probability"] == 0.9
    assert writers.calls == [(name, "ok")]


async def test_every_prompt_is_distinct():
    seen = set()
    for name, reply, _ in CASES:
        writers, responses = make(reply)
        await getattr(writers, name)(STATE, VERDICT)
        seen.add(responses.calls[0]["input"][0]["content"])
    assert len(seen) == len(CASES)


@pytest.mark.parametrize("name", [c[0] for c in CASES])
async def test_a_slow_transport_times_out_to_the_default(name, caplog):
    writers, _ = make({"line": "late"}, delay=0.5, timeout_s=0.01)
    with caplog.at_level(logging.WARNING, logger="pipeline.reasoner.writers"):
        assert await getattr(writers, name)(STATE, VERDICT) == DEFAULTS[name]
    assert writers.calls == [(name, "failed")]
    assert any(name in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name", [c[0] for c in CASES])
async def test_a_transport_error_yields_the_default_and_a_failed_record(name, caplog):
    writers, _ = make(RuntimeError("500 upstream"))
    with caplog.at_level(logging.WARNING, logger="pipeline.reasoner.writers"):
        assert await getattr(writers, name)(STATE, VERDICT) == DEFAULTS[name]
    assert writers.calls == [(name, "failed")]
    (record,) = [r for r in caplog.records if r.name == "pipeline.reasoner.writers"]
    assert record.levelno == logging.WARNING and name in record.getMessage()


async def test_unparseable_output_is_a_failure():
    writers, _ = make("not json")
    assert await writers.persona_fact(STATE, VERDICT) is None
    assert writers.calls == [("persona_fact", "failed")]


@pytest.mark.parametrize("name,reply", [
    ("summary_line", {"line": "  "}),
    ("insight", {"category": "diet", "text": ""}),
    ("persona_fact", {"fact": ""}),
    ("handoff_topic", {"topic": ""}),
    ("question", {"text": "", "answer_kind": "yes_no", "fills": "confirmed"}),
    ("watch_condition", {"after_s": None, "condition": ""}),
    ("look_question", {"question": ""}),
])
async def test_an_empty_answer_is_recorded_as_empty(name, reply):
    writers, _ = make(reply)
    assert await getattr(writers, name)(STATE, VERDICT) == DEFAULTS[name]
    assert writers.calls == [(name, "empty")]


async def test_summary_fallback_is_capped():
    state = {**STATE, "trigger": {"name": "x" * 50, "reason": "y" * 100}}
    writers, _ = make(RuntimeError("boom"))
    line = await writers.summary_line(state, VERDICT)
    assert line == f"{'x' * 50}: {'y' * 100}"[:ANNOTATE_MAX_CHARS]


async def test_length_caps_are_enforced():
    writers, _ = make({"line": "a" * 300})
    assert len(await writers.summary_line(STATE, VERDICT)) <= ANNOTATE_MAX_CHARS
    writers, _ = make({"fact": "b" * 300})
    assert len(await writers.persona_fact(STATE, VERDICT)) <= REMEMBER_MAX_CHARS
    writers, _ = make({"question": "c" * 300})
    assert len(await writers.look_question(STATE, VERDICT)) <= 120


async def test_handoff_is_one_line():
    writers, _ = make({"topic": "coffee late\nbedtime soon"})
    assert await writers.handoff_topic(STATE, VERDICT) == "coffee late bedtime soon"


@pytest.mark.parametrize("reply", [
    {"text": "is it yours", "answer_kind": "maybe", "fills": "confirmed"},
    {"text": "is it yours", "answer_kind": "yes_no", "fills": "mood"},
])
async def test_question_rejects_a_bad_answer_kind_or_fills(reply):
    writers, _ = make(reply)
    assert await writers.question(STATE, VERDICT) is None
    assert writers.calls == [("question", "failed")]


async def test_watch_keeps_a_condition_without_a_delay():
    writers, _ = make({"after_s": -5, "condition": "cup still in hand"})
    assert await writers.watch_condition(STATE, VERDICT) == (None, "cup still in hand")


async def test_fake_writers_return_canned_values_and_record_calls():
    fake = FakeWriters(persona_fact=None, look_question="What is on the plate?")
    assert await fake.summary_line(STATE, VERDICT) == "moment noted"
    assert await fake.persona_fact(STATE, VERDICT) is None
    assert await fake.look_question(STATE, VERDICT) == "What is on the plate?"
    q = await fake.question(STATE, VERDICT)
    assert q[1] == "yes_no" and q[2] == "confirmed"
    assert fake.calls == [
        ("summary_line", "ok"), ("persona_fact", "empty"),
        ("look_question", "ok"), ("question", "ok"),
    ]


def test_make_writers_only_for_a_real_openai_client():
    settings = DeciderSettings()
    real = OpenAIReasonerClient("sk-test", "m", client=SimpleNamespace(responses=None))
    assert isinstance(make_writers(settings, real), Writers)
    assert make_writers(settings, FakeReasonerClient()) is None
