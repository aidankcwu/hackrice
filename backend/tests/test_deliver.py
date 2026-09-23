"""``deliver`` policy: a quiet hand-off waits for a quiet tick (US-M04).

docs/PERCEPTION.md "Gate and actions": ``now`` hands off at once; ``quiet``
waits up to ``quiet_max_s`` (or ``expire_s`` if smaller) for a tick where
``people_interacting`` is cold, ``activity`` is not talking and the device is
not moving fast, else drops as ``deliver_expired``. Built like
``test_conversation.py``: in-memory db and frames, the fake voice client, a
hand-wound latest tick and a short poll.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline.actions.handlers import ActionHandler
from pipeline.actions.questions import QuestionManager
from pipeline.actions.speech import SpeechLimiter, clear_spoken
from pipeline.config import Settings, Timings
from pipeline.conversation import agent as agent_mod
from pipeline.conversation.agent import (
    DELIVER_EXPIRED,
    DELIVER_WAITING,
    ConversationAgent,
    is_quiet,
)
from pipeline.conversation.client import FakeVoiceClient
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.models import (
    AiBlock,
    DeviceBlock,
    Escalation,
    SensorBlock,
    Tick,
    WatchBlock,
)
from pipeline.reasoner.client import FakeAnswerParser, FakeReasonerClient
from pipeline.reasoner.decider import FakeDecider
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.schema import AskAction, SpeakAction, T1Response
from pipeline.reasoner.writers import FakeWriters

pytestmark = pytest.mark.asyncio

T0 = 1_757_700_000.0
WINDOW_N = 12


def tick(i: int = WINDOW_N, *, hot: list[str] | None = None, activity: str = "seated",
         accel: float | None = None, watch: bool = True) -> Tick:
    return Tick(
        tick_id=f"t_{i:08d}", t=T0 + i, seq=i,
        sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.1, phash="0" * 16),
        ai=AiBlock(as_of=T0 + i, age_ms=0, scene="office", activity=activity),
        device=DeviceBlock(accel_rms=accel) if accel is not None else None,
        watch=WatchBlock(hot=hot or []) if watch else None,
        frame_ref=f"f_{i:08d}",
    )


def make_escalation() -> Escalation:
    window = [tick(i) for i in range(WINDOW_N)]
    return Escalation(trigger="alcohol_seen", t=window[-1].t, tick=window[-1],
                      window=window, reason="alcohol_visible")


class Latest:
    """The newest tick, set by the test."""

    def __init__(self, value: Tick | None = None) -> None:
        self.value = value
        self.reads = 0

    def __call__(self) -> Tick | None:
        self.reads += 1
        return self.value


def build(latest: Latest, quiet_max_s: float = 1.0) -> SimpleNamespace:
    db = Database(":memory:").connect().init_schema()
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(WINDOW_N + 1):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    settings = Settings(demo_mode=True, openai_api_key=None)
    clock = lambda: T0 + WINDOW_N  # noqa: E731
    speech = SpeechLimiter(settings.timings.speech_min_gap,
                           settings.timings.speech_max_per_hour)

    async def wire(question) -> bool:
        return True

    questions = QuestionManager(
        db, speech, settings.timings, send=wire, supports_ask=lambda: True,
        has_transport=lambda: True, parser=FakeAnswerParser(), now_fn=clock,
    )
    agent = ConversationAgent(
        db, store, FakeVoiceClient(), speech, settings, questions=questions,
        now_fn=clock, mouth_guard=False, latest_tick=latest, quiet_max_s=quiet_max_s,
    )
    questions.conversation = agent
    return SimpleNamespace(db=db, agent=agent)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(agent_mod, "DELIVER_POLL_S", 0.01)
    clear_spoken()
    yield
    clear_spoken()


async def until(cond, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never held")
        await asyncio.sleep(0.005)


async def test_deliver_now_opens_immediately():
    latest = Latest(tick(hot=["people_interacting"]))
    h = build(latest)
    out = h.agent.request("the drink", "statement", esc=make_escalation())
    assert out.startswith("handed_off:")
    assert latest.reads == 0
    await h.agent.stop()


async def test_quiet_with_a_quiet_tick_opens_on_the_first_poll():
    latest = Latest(tick())
    h = build(latest)
    out = h.agent.request("the drink", deliver="quiet", esc=make_escalation())
    assert out == DELIVER_WAITING
    await until(lambda: h.agent.opened == 1)
    assert latest.reads == 1
    await h.agent.stop()


async def test_quiet_waits_until_people_interacting_clears():
    latest = Latest(tick(hot=["people_interacting"]))
    h = build(latest, quiet_max_s=5.0)
    assert h.agent.request("the drink", deliver="quiet") == DELIVER_WAITING
    await until(lambda: latest.reads >= 3)
    assert h.agent.opened == 0
    latest.value = tick()
    await until(lambda: h.agent.opened == 1)
    await h.agent.stop()


async def test_quiet_that_never_clears_expires():
    latest = Latest(tick(activity="talking"))
    h = build(latest, quiet_max_s=0.05)
    assert h.agent.request("the drink", deliver="quiet") == DELIVER_WAITING
    await until(lambda: h.agent.dropped_deliver_expired == 1)
    assert h.agent.opened == 0
    assert h.agent.stats()["dropped_deliver_expired"] == 1
    assert DELIVER_EXPIRED == "deliver_expired"


async def test_a_second_quiet_request_supersedes_the_first():
    latest = Latest(tick(accel=2.0))
    h = build(latest, quiet_max_s=5.0)
    assert h.agent.request("first", deliver="quiet") == DELIVER_WAITING
    await asyncio.sleep(0.02)
    assert h.agent.request("second", deliver="quiet") == DELIVER_WAITING
    assert h.agent.dropped_deliver_superseded == 1
    latest.value = tick(accel=0.1)
    await until(lambda: h.agent.opened == 1)
    await asyncio.sleep(0.05)
    assert h.agent.opened == 1
    assert [c["topic"] for c in h.agent.list()] == ["second"]
    await h.agent.stop()


async def test_a_tick_without_a_watch_block_is_quiet_on_that_criterion():
    assert is_quiet(tick(watch=False))
    assert not is_quiet(tick(hot=["people_interacting"]))
    assert not is_quiet(tick(activity="talking"))
    assert not is_quiet(tick(accel=0.5))
    assert is_quiet(tick(accel=0.49))
    assert not is_quiet(None)
    latest = Latest(tick(watch=False))
    h = build(latest)
    h.agent.request("the drink", deliver="quiet")
    await until(lambda: h.agent.opened == 1)
    await h.agent.stop()


async def test_expire_s_shorter_than_quiet_max_s_wins(monkeypatch):
    monkeypatch.setattr(agent_mod, "DELIVER_POLL_S", 0.2)
    latest = Latest(tick(hot=["people_interacting"]))
    h = build(latest, quiet_max_s=60.0)
    started = asyncio.get_running_loop().time()
    h.agent.request("the drink", deliver="quiet", expire_s=1)
    await until(lambda: h.agent.dropped_deliver_expired == 1, timeout=3.0)
    assert asyncio.get_running_loop().time() - started < 2.0


class FakeConversation:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def current(self) -> None:
        return None

    def request(self, topic: str, mode: str = "statement", **kw: Any) -> str:
        self.requests.append((topic, mode, kw))
        return DELIVER_WAITING


async def test_handler_passes_deliver_and_expire_s_through():
    db = Database(":memory:").connect().init_schema()
    conv = FakeConversation()
    handler = ActionHandler(db, SpeechLimiter(min_gap_s=0, max_per_hour=10),
                            Timings.demo(), conversation=conv)
    handler.apply("d_1", T0, T1Response(interpretation="…", confidence=0.5, actions=[
        SpeakAction(text="the drink", deliver="quiet", expire_s=30)]))
    handler.apply("d_2", T0, T1Response(interpretation="…", confidence=0.5, actions=[
        AskAction(text="yours?")]))
    (_, _, kw1), (_, _, kw2) = conv.requests
    assert (kw1["deliver"], kw1["expire_s"]) == ("quiet", 30)
    assert (kw2["deliver"], kw2["expire_s"]) == ("now", None)
    db.close()


@pytest.mark.parametrize("urgency,expected", [(1.6, "now"), (1.5, "now"), (1.0, "quiet")])
async def test_reasoner_maps_urgency_to_deliver(urgency, expected):
    db = Database(":memory:").connect().init_schema()
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(WINDOW_N):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    settings = Settings(demo_mode=True, openai_api_key=None)
    reasoner = Reasoner(
        db, store, FakeReasonerClient(),
        SpeechLimiter(settings.timings.speech_min_gap, settings.timings.speech_max_per_hour),
        settings, decider=FakeDecider({"speak": 0.95}, urgency=urgency),
        writers=FakeWriters(),
    )
    assert reasoner.try_escalate(make_escalation()) is True
    await until(lambda: not reasoner.busy)
    (decision,) = db.list_decisions()
    speak = next(a for a in decision.actions if a["type"] == "speak")
    assert speak["deliver"] == expected
    db.close()


async def test_schema_bounds_expire_s():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SpeakAction(text="x", expire_s=0)
    with pytest.raises(ValidationError):
        AskAction(text="x", expire_s=601)
    assert SpeakAction(text="x").deliver == "now"
