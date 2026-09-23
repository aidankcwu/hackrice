"""The adherence matcher and the ``medication_seen`` trigger (PLAN 2.2).

A dose sighting inside an open window marks the item ``seen`` with the frame
that saw it; outside every window it is an episode only. A dose window that
closes unsighted is ``missed`` and the wearer hears about it exactly once.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Iterator

import pytest

from pipeline.actions.speech import SpeechLimiter, clear_spoken
from pipeline.config import Settings, Timings
from pipeline.db import Database, day_key
from pipeline.episodes import EpisodeBuilder
from pipeline.frames import InMemoryFrameStore
from pipeline.gate import TriggerGate, default_triggers
from pipeline.gate.triggers import MEDICATION_COOLDOWN_S
from pipeline.models import AiBlock, Escalation, SensorBlock, Tick
from pipeline.protocol.adherence import MISSED_LINE, AdherenceMatcher
from pipeline.reasoner.client import FakeReasonerClient
from pipeline.reasoner.reasoner import Reasoner

#: A Monday; every seeded item runs every day.
DAY = date(2026, 9, 21)


def at(hh: int, mm: int, ss: int = 0) -> float:
    """Unix time of local ``hh:mm:ss`` on :data:`DAY`."""

    return datetime(DAY.year, DAY.month, DAY.day, hh, mm, ss).timestamp()


def make_tick(t: float, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{t:.0f}", t=t, seq=int(t) % 100_000,
        sensor=SensorBlock(frame_delta=0.1, phash="0" * 16),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{t:.0f}",
    )


class CountingSpeech(SpeechLimiter):
    """The real limiter, recording what reached ``speak``."""

    def __init__(self) -> None:
        super().__init__(min_gap_s=0.0, max_per_hour=100)
        self.said: list[str] = []

    def speak(self, text: str, urgency: str = "low", t: float | None = None) -> None:
        self.said.append(text)
        super().speak(text, urgency, t=t)


@pytest.fixture(autouse=True)
def _clean_speech() -> Iterator[None]:
    clear_spoken()
    yield
    clear_spoken()


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


def item_named(db: Database, name: str) -> dict:
    return next(i for i in db.list_protocol_items() if i["name"] == name)


def status_of(db: Database, name: str) -> dict | None:
    return db.protocol_status(item_named(db, name)["id"], DAY.isoformat())


class Rig:
    """Episodes, gate, reasoner and matcher wired the way ``build_pipeline`` does."""

    def __init__(self, db: Database) -> None:
        timings = Timings.demo()
        self.db = db
        self.store = InMemoryFrameStore(ttl_s=90.0)
        self.speech = CountingSpeech()
        self.reasoner = Reasoner(db, self.store, FakeReasonerClient(), self.speech,
                                 Settings(demo_mode=True, openai_api_key=None))
        self.matcher = AdherenceMatcher(db, self.speech)
        self.reasoner.on_evidence = self.matcher.on_evidence
        self.episodes = EpisodeBuilder(db, timings)
        self.gate = TriggerGate(default_triggers(timings, True), timings, db,
                                self.episodes, self.reasoner.try_escalate, True)

    async def feed(self, t0: float, n: int, **ai: object) -> None:
        for i in range(n):
            tick = make_tick(t0 + i, **ai)
            self.store.put(tick.frame_ref, f"jpeg {tick.frame_ref}".encode(), tick.t)
            self.episodes.on_tick(tick)
            self.gate.on_tick(tick)
            self.matcher.on_tick(tick.t)
            await self.drain()

    async def drain(self) -> None:
        for _ in range(400):
            if not self.reasoner.busy:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0)


# -- the trigger ---------------------------------------------------------------


def test_medication_seen_reads_the_flag_or_the_held_words_and_cools_down_20_min(db) -> None:
    timings = Timings.demo()
    trigger = next(t for t in default_triggers(timings, True) if t.name == "medication_seen")
    assert trigger.episode_kind == "medication_sighting"
    assert trigger.cooldown_s == MEDICATION_COOLDOWN_S == 1200.0

    episodes = EpisodeBuilder(db, timings)
    fired: list[Escalation] = []
    gate = TriggerGate(default_triggers(timings, True), timings, db, episodes,
                       lambda e: fired.append(e) is None, True)

    def run(t0: float, n: int = 6, **ai: object) -> list[str]:
        for i in range(n):
            tick = make_tick(t0 + i, **ai)
            episodes.on_tick(tick)
            gate.on_tick(tick)
        return [e.trigger for e in fired].count("medication_seen")

    t0 = at(12, 0)
    assert run(t0, in_hand="pencil") == 0          # whole words: "pen" is not "pencil"
    assert run(t0 + 100, in_hand="insulin pen") == 1
    assert "medication_sighting" in {e.kind for e in db.list_episodes()}
    assert run(t0 + 200, in_hand="pill bottles") == 1   # inside the 20 min cooldown
    assert run(t0 + 100 + MEDICATION_COOLDOWN_S, medication_visible=True) == 2
    for word in ("vial", "syringe", "injector", "capsule", "tablet"):
        assert make_tick(t0, in_hand=f"a {word}").medication_in_view() is True
    assert make_tick(t0, medication_visible=False).medication_in_view() is False
    assert make_tick(t0, scene="office").medication_in_view() is None


# -- sightings -------------------------------------------------------------------


async def test_sighting_in_a_dose_window_marks_it_seen_with_evidence(db) -> None:
    rig = Rig(db)
    await rig.feed(at(8, 0), 6, medication_visible=True)

    assert rig.gate.fired["medication_seen"] == 1
    row = status_of(db, "Morning dose")
    assert row is not None and row["status"] == "seen"
    assert at(8, 0) <= row["seen_t"] < at(8, 1)
    decision_id, ref = row["evidence_ref"].split("/", 1)
    decision = next(d for d in db.list_decisions() if d.id == decision_id)
    assert decision.trigger == "medication_seen"
    assert ref == "f_" + decision.trigger_tick_id.removeprefix("t_")
    assert rig.reasoner.evidence.get(decision_id, ref) == f"jpeg {ref}".encode()
    assert status_of(db, "Evening dose") is None

    # A seen dose is not missed when its window closes, and nothing is said.
    rig.matcher.on_tick(at(9, 59, 59))
    rig.matcher.on_tick(at(10, 0))
    assert status_of(db, "Morning dose")["status"] == "seen"
    assert MISSED_LINE.format(name="Morning dose") not in rig.speech.said


async def test_sighting_outside_every_window_is_an_episode_only(db) -> None:
    rig = Rig(db)
    await rig.feed(at(12, 0), 6, in_hand="pill bottle")

    assert rig.gate.fired["medication_seen"] == 1
    assert "medication_sighting" in {e.kind for e in db.list_episodes()}
    assert db.protocol_statuses(DAY.isoformat(), DAY.isoformat()) == []


def test_meal_walk_and_winddown_are_seen_only_by_their_own_triggers(db) -> None:
    matcher = AdherenceMatcher(db, CountingSpeech())

    def escalate(trigger: str, t: float) -> None:
        tick = make_tick(t)
        matcher.on_evidence(Escalation(trigger=trigger, t=t, tick=tick, window=[tick]),
                            "d_0001", {tick.frame_ref: b"jpeg"})

    escalate("medication_seen", at(12, 30))       # a dose sighting is not a meal
    assert status_of(db, "Lunch window") is None
    escalate("food_in_frame", at(12, 30))
    lunch = status_of(db, "Lunch window")
    assert lunch["status"] == "seen" and lunch["evidence_ref"] == f"d_0001/f_{at(12, 30):.0f}"
    escalate("outdoor_sustained", at(8, 15))
    assert status_of(db, "Daylight walk")["status"] == "seen"
    escalate("screen_sustained", at(20, 0))       # before wind-down starts
    assert status_of(db, "Wind‑down") is None
    escalate("screen_sustained", at(21, 45))
    assert status_of(db, "Wind‑down")["status"] == "seen"
    # Their windows closing is not a miss and not a whisper.
    matcher.on_tick(at(13, 59))
    matcher.on_tick(at(16, 1))
    assert matcher.speech.said == []


# -- closing windows ---------------------------------------------------------------


def test_a_dose_window_closing_unsighted_is_missed_and_speaks_once(db) -> None:
    speech = CountingSpeech()
    matcher = AdherenceMatcher(db, speech)

    assert matcher.on_tick(at(9, 59, 59)) == []
    closed = matcher.on_tick(at(10, 0))

    assert [row["status"] for row in closed] == ["missed"]
    assert status_of(db, "Morning dose")["status"] == "missed"
    assert status_of(db, "Evening dose") is None
    assert speech.said == [
        "Your Morning dose window just closed. Take it now, or mark it skipped in Brian."
    ]


def test_a_second_close_event_does_not_speak_again(db) -> None:
    speech = CountingSpeech()
    matcher = AdherenceMatcher(db, speech)
    matcher.on_tick(at(9, 59))
    matcher.on_tick(at(10, 0, 30))
    assert len(speech.said) == 1

    # The same tick again, a later tick, and a replayed clock crossing 10:00.
    matcher.on_tick(at(10, 0, 30))
    matcher.on_tick(at(10, 5))
    matcher.on_tick(at(9, 58))
    assert matcher.on_tick(at(10, 1)) == []
    assert matcher.close_window(item_named(db, "Morning dose"), DAY.isoformat(), at(10, 2)) is None
    # A restarted process sees the stored ``missed`` and stays quiet too.
    restarted = AdherenceMatcher(db, speech)
    restarted.on_tick(at(9, 59))
    assert restarted.on_tick(at(10, 3)) == []

    assert len(speech.said) == 1
    assert status_of(db, "Morning dose")["status"] == "missed"
    assert day_key(at(10, 0)) == DAY.isoformat()


class ProductionSpeech(CountingSpeech):
    """The real limiter under production timings: 600 s gap, 6 lines an hour."""

    def __init__(self) -> None:
        super().__init__()
        timings = Timings.production()
        self.min_gap_s = timings.speech_min_gap
        self.max_per_hour = timings.speech_max_per_hour


def test_a_missed_dose_lands_even_when_the_limiter_would_refuse(db) -> None:
    speech = ProductionSpeech()
    matcher = AdherenceMatcher(db, speech)
    # An ordinary line just used the gap: a plain speak now would be refused.
    assert speech.allow(at(9, 59, 30)) is True
    assert speech.allow(at(9, 59, 45)) is False

    matcher.on_tick(at(9, 59, 50))
    matcher.on_tick(at(10, 0))
    matcher.on_tick(at(10, 0, 1))

    assert speech.said == [MISSED_LINE.format(name="Morning dose")]
    assert status_of(db, "Morning dose")["status"] == "missed"
    # Stamped on the limiter: the next ordinary line waits its gap from 10:00.
    assert speech.last_spoken_t == at(10, 0)
    assert speech.allow(at(10, 5)) is False
    assert speech.allow(at(10, 10)) is True


def test_a_missed_dose_lands_past_the_hourly_cap(db) -> None:
    speech = ProductionSpeech()
    speech.min_gap_s = 0.0
    matcher = AdherenceMatcher(db, speech)
    for i in range(speech.max_per_hour):
        assert speech.allow(at(21, 10) + i) is True
    assert speech.allow(at(21, 59, 30)) is False  # the hour is still full at 22:00

    matcher.on_tick(at(21, 59))
    matcher.on_tick(at(22, 0))

    assert speech.said == [MISSED_LINE.format(name="Evening dose")]
    assert status_of(db, "Evening dose")["status"] == "missed"


def test_a_backend_started_after_the_window_does_not_say_it_just_closed(db) -> None:
    speech = CountingSpeech()
    matcher = AdherenceMatcher(db, speech)
    matcher.on_tick(at(23, 0))
    matcher.on_tick(at(23, 0, 1))
    assert speech.said == []
    assert db.protocol_statuses(DAY.isoformat(), DAY.isoformat()) == []
