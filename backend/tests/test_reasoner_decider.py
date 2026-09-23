"""The reasoner routes escalations through the decider, clerk as fallback
(docs/PERCEPTION.md, "Decider and writers").

Built the way ``test_reasoner.py`` builds a Reasoner -- fake clerk, in-memory
db and frame store, the real ActionHandler -- plus a ``FakeDecider`` and
``FakeWriters``. No network anywhere.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pipeline.actions.speech import SpeechLimiter, clear_spoken
from pipeline.config import Settings
from pipeline.db import Database, day_key
from pipeline.frames import InMemoryFrameStore
from pipeline.models import AiBlock, Escalation, SensorBlock, Tick
from pipeline.reasoner.client import FakeReasonerClient
from pipeline.reasoner.decider import ACTIONS, DeciderError, FakeDecider, Verdict
from pipeline.reasoner.decider_settings import DeciderSettings
from pipeline.reasoner.envelope import build_envelope
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.writers import FakeWriters

T0 = 1_757_700_000.0
WINDOW_N = 12


# -- fixtures (mirroring test_reasoner.py) --------------------------------


def make_window(n: int = WINDOW_N, **flags: Any) -> list[Tick]:
    ticks: list[Tick] = []
    for i in range(n):
        phash = "0000000000000000" if i < n // 2 else "ffffffffffff0000"
        ticks.append(
            Tick(
                tick_id=f"t_{i:08d}",
                t=T0 + i,
                seq=i,
                sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.1, phash=phash),
                ai=AiBlock(as_of=T0 + i, age_ms=0, scene="office",
                           activity="seated", **flags),
                frame_ref=f"f_{i:08d}",
            )
        )
    return ticks


def make_escalation(
    trigger: str = "food_in_frame", window: list[Tick] | None = None
) -> Escalation:
    window = window if window is not None else make_window()
    return Escalation(
        trigger=trigger, t=window[-1].t, tick=window[-1], window=window,
        reason="synthetic",
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's DECIDE_* shell variables must not move the thresholds."""

    for name in list(DeciderSettings.model_fields) + ["T1_MODEL"]:
        for key in (name.upper(), name):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clean_speech():
    clear_spoken()
    yield
    clear_spoken()


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def frame_store():
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(WINDOW_N):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    return store


@pytest.fixture
def settings():
    return Settings(demo_mode=True, openai_api_key=None)


def build_reasoner(db, frame_store, settings, client, **kwargs) -> Reasoner:
    return Reasoner(
        db, frame_store, client,
        SpeechLimiter(settings.timings.speech_min_gap,
                      settings.timings.speech_max_per_hour),
        settings, **kwargs,
    )


async def drain(reasoner: Reasoner, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while reasoner.busy:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("reasoner never released the T1 slot")
        await asyncio.sleep(0.005)
    await asyncio.sleep(0)


async def run_one(reasoner: Reasoner, esc: Escalation | None = None):
    assert reasoner.try_escalate(esc or make_escalation()) is True
    await drain(reasoner)
    (decision,) = reasoner.db.list_decisions()
    return decision


def kinds(decision) -> list[str]:
    return [a["type"] for a in decision.actions]


# -- (a) thresholds decide exactly which actions fire ----------------------


async def test_thresholds_include_and_exclude_exactly(db, frame_store, settings):
    # log_insight sits on its threshold (fires), remember just under (does
    # not), watch well over; speak/ask far outside the uncertain band.
    decider = FakeDecider({"log_insight": 0.60, "remember": 0.69, "watch": 0.9,
                           "speak": 0.1, "ask": 0.0}, topic="caffeine")
    writers = FakeWriters()
    clerk = FakeReasonerClient()
    reasoner = build_reasoner(db, frame_store, settings, clerk,
                              decider=decider, writers=writers,
                              decider_settings=DeciderSettings())

    decision = await run_one(reasoner)

    assert kinds(decision) == ["annotate", "log_insight", "watch"]
    assert decision.path == "decider"
    assert decision.writers == ["summary_line", "insight", "watch_condition"]
    assert decision.model == "fake"
    assert decision.interpretation == "moment noted"
    assert decision.confidence == 0.9
    assert decision.latency_ms is not None
    assert clerk.calls == 0, "the decider settled it; no clerk call"
    watch = next(a for a in decision.actions if a["type"] == "watch")
    assert (watch["after_s"], watch["condition"], watch["reason"]) == (60, None, "caffeine")
    # The actions were applied through the ordinary handler path.
    assert len(db.today_summary_lines(day=day_key(T0 + WINDOW_N - 1))) == 1
    assert len(db.list_insights()) == 1
    assert len(db.due_pending_checks(T0 + WINDOW_N + 3600)) == 1


async def test_a_fired_speak_carries_the_verdicts_urgency(db, frame_store, settings):
    decider = FakeDecider({"speak": 0.95}, urgency=1.6)
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=decider, writers=FakeWriters())

    decision = await run_one(reasoner)

    speak = next(a for a in decision.actions if a["type"] == "speak")
    assert speak["text"] == FakeWriters.DEFAULTS["handoff_topic"]
    assert speak["urgency"] == "high"
    assert decision.writers == ["summary_line", "handoff_topic"]
    assert decision.spoke is True


async def test_the_state_is_built_from_the_envelopes_inputs(db, frame_store, settings):
    decider = FakeDecider({}, topic="food")
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=decider, writers=FakeWriters(),
                              seven_day_summary=lambda: "7d: two coffees a day",
                              persona="be terse")

    await run_one(reasoner)
    reasoner.try_escalate(make_escalation("screen_sustained"))
    await drain(reasoner)

    first, second = decider.calls
    assert first["trigger"] == {"name": "food_in_frame", "reason": "synthetic"}
    assert first["today"] == []
    assert first["persona"] == "be terse"
    assert first["trends"] == "7d: two coffees a day"
    assert len(first["recent"]) == 6
    # The first run's annotate line is in the second state's summary.
    assert second["today"] == ["moment noted"]
    assert second["trigger"]["name"] == "screen_sustained"


# -- (b) uncertain -> clerk ----------------------------------------------


async def test_an_uncertain_speak_hands_the_decision_to_the_clerk(
    db, frame_store, settings
):
    decider = FakeDecider({"speak": 0.5, "log_insight": 0.9})
    writers = FakeWriters()
    clerk = FakeReasonerClient()
    window = make_window(food_present=True, food_type="mixed")
    reasoner = build_reasoner(db, frame_store, settings, clerk,
                              decider=decider, writers=writers)

    decision = await run_one(reasoner, make_escalation("food_in_frame", window))

    assert decision.path == "clerk_fallback:uncertain:speak"
    assert decision.writers == []
    assert writers.calls == [], "no writer runs when the clerk decides"
    assert clerk.calls == 1
    # The clerk's own output, not the decider's.
    assert set(kinds(decision)) == {"annotate", "log_insight"}
    assert "mixed" in decision.interpretation
    assert reasoner.fell_back == {"uncertain:speak": 1}


# -- (c) decider error -> clerk ------------------------------------------


async def test_a_decider_error_hands_the_decision_to_the_clerk(
    db, frame_store, settings, caplog
):
    def explode(state: dict) -> Verdict:
        raise DeciderError("Jev 503", status=503)

    clerk = FakeReasonerClient()
    reasoner = build_reasoner(db, frame_store, settings, clerk,
                              decider=FakeDecider(explode), writers=FakeWriters())

    decision = await run_one(reasoner)

    assert decision.path == "clerk_fallback:error"
    assert decision.dropped is False
    assert clerk.calls == 1
    # The fake clerk's own shape for a food_in_frame trigger, not the decider's.
    assert kinds(decision) == ["annotate", "log_insight"]
    assert reasoner.fell_back == {"error": 1}
    assert any("falling back to the clerk" in r.getMessage() for r in caplog.records)


# -- (d) a writer returning None drops its action -------------------------


async def test_a_failed_writer_drops_its_action_and_is_recorded(
    db, frame_store, settings
):
    decider = FakeDecider({"log_insight": 0.9, "remember": 0.9})
    writers = FakeWriters(insight=None)
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=decider, writers=writers)

    decision = await run_one(reasoner)

    assert kinds(decision) == ["annotate", "remember"]
    assert decision.writers == ["summary_line", "insight:failed", "persona_fact"]
    assert db.list_insights() == []
    assert reasoner.remembered == 1, "remember still goes through _remember"


# -- (e) no writers: only the annotate survives ---------------------------


async def test_without_writers_only_annotate_survives(db, frame_store, settings):
    decider = FakeDecider({a: 0.99 for a in ACTIONS})
    clerk = FakeReasonerClient()
    reasoner = build_reasoner(db, frame_store, settings, clerk,
                              decider=decider, writers=None)

    decision = await run_one(reasoner)

    # The sound act needs no writer, so it is the one thing besides the
    # annotate that survives (US-M03).
    assert kinds(decision) == ["annotate", "act"]
    assert decision.actions[0]["line"] == "food_in_frame: synthetic"
    assert decision.interpretation == "food_in_frame: synthetic"
    assert decision.path == "decider"
    assert decision.writers == [
        "log_insight:no_writer", "remember:no_writer", "watch:no_writer",
        "speak:no_writer", "ask:no_writer", "act:sound", "look:no_writer",
    ]
    assert clerk.calls == 0


async def test_act_and_look_become_real_actions(db, frame_store, settings):
    decider = FakeDecider({"act": 0.9, "look": 0.9}, topic="food")
    writers = FakeWriters()
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=decider, writers=writers)

    decision = await run_one(reasoner)

    assert kinds(decision) == ["annotate", "act", "look"]
    assert decision.writers == ["summary_line", "act:sound", "look_question"]
    assert [name for name, _ in writers.calls] == ["summary_line", "look_question"]
    act = decision.actions[1]
    assert (act["kind"], act["args"]) == ("sound", {"name": "soft"})
    look = decision.actions[2]
    assert (look["question"], look["reason"]) == (FakeWriters.DEFAULTS["look_question"], "food")
    assert look["outcome"] == "look_unavailable", "no capture on this reasoner"


# -- (f) no decider: the clerk path is unchanged --------------------------


async def test_without_a_decider_the_clerk_decides_as_before(
    db, frame_store, settings
):
    window = make_window(food_present=True, food_type="mixed")
    clerk = FakeReasonerClient()
    reasoner = build_reasoner(db, frame_store, settings, clerk)
    assert reasoner.decider is None and reasoner.decider_settings is None

    decision = await run_one(reasoner, make_escalation("food_in_frame", window))

    esc = make_escalation("food_in_frame", window)
    expected, _ = await FakeReasonerClient().complete(
        build_envelope(esc, {tk.frame_ref: b"jpeg" for tk in window}, [], "7d", "p")
    )
    assert kinds(decision) == [a.type for a in expected.actions]
    assert decision.interpretation == expected.interpretation
    assert decision.path == "clerk"
    assert decision.writers == []
    assert clerk.calls == 1
    assert reasoner.decided_by_decider == 0
    assert reasoner.fell_back == {}


async def test_decider_settings_default_when_a_decider_is_given(
    db, frame_store, settings
):
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=FakeDecider({}))
    assert isinstance(reasoner.decider_settings, DeciderSettings)
    assert reasoner.decider_settings.thresholds() == DeciderSettings().thresholds()


# -- (g) normalize still applies -----------------------------------------


async def test_normalize_drops_a_fired_speak_when_ask_also_fired(
    db, frame_store, settings
):
    decider = FakeDecider({"speak": 0.9, "ask": 0.9}, topic="alcohol")
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=decider, writers=FakeWriters())

    decision = await run_one(reasoner)

    assert kinds(decision) == ["annotate", "ask"]
    ask = decision.actions[1]
    assert (ask["text"], ask["answer_kind"], ask["fills"], ask["reason"]) == (
        "whether this is the wearer's", "yes_no", "confirmed", "alcohol"
    )
    # Both writers ran; the drop is normalize's, after the fact.
    assert decision.writers == ["summary_line", "handoff_topic", "question"]


# -- (h) counters ---------------------------------------------------------


async def test_counters_are_exposed_in_stats(db, frame_store, settings):
    verdicts = iter([
        {"log_insight": 0.9},           # decided
        {"ask": 0.5},                   # uncertain -> clerk
        {"act": 0.45},                  # uncertain -> clerk
        "boom",                         # error -> clerk
        {},                             # decided
    ])

    def scripted(state: dict) -> Verdict:
        probs = next(verdicts)
        if probs == "boom":
            raise DeciderError("down")
        return Verdict(probabilities={a: probs.get(a, 0.0) for a in ACTIONS},
                       topic="other", topic_confidence=1.0, urgency=0.0,
                       model="fake")

    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient(),
                              decider=FakeDecider(scripted), writers=FakeWriters())

    for _ in range(5):
        assert reasoner.try_escalate(make_escalation()) is True
        await drain(reasoner)

    stats = reasoner.stats()
    assert stats["decided_by_decider"] == 2
    assert stats["fell_back"] == {"uncertain:ask": 1, "uncertain:act": 1, "error": 1}
    assert stats["completed"] == 5
    paths = sorted(d.path for d in db.list_decisions())
    assert paths == sorted([
        "decider", "clerk_fallback:uncertain:ask", "clerk_fallback:uncertain:act",
        "clerk_fallback:error", "decider",
    ])
