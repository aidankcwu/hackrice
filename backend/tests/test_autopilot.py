"""Autopilot (PLAN 4.1): the system acts instead of nagging.

Two rule-based producers on the tick clock -- a calendar walk at 16:00 when
the day is short of outdoor minutes, a screen shield at wind-down -- each fire
once per local day. The phone's ``act_result`` flips the decision to
``acted`` or ``act_failed``, and a failure is spoken exactly once.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator

import pytest

from longevity import wire
from longevity.server.ingest import GlassesLink, _handle
from pipeline.actions.autopilot import Autopilot, sunset_t
from pipeline.actions.handlers import (
    ACT_FAILED, ACT_FAILED_LINES, ACTED, ActionHandler,
)
from pipeline.actions.speech import SpeechLimiter, clear_spoken
from pipeline.config import Settings, Timings
from pipeline.db import Database
from pipeline.models import Episode

#: A Monday.
DAY = date(2026, 9, 21)


def ts(hh: int, mm: int = 0, *, days: int = 0) -> float:
    """Unix time of local ``hh:mm`` on :data:`DAY` + ``days`` (timezone-safe)."""

    return (datetime.combine(DAY, time(hh, mm)) + timedelta(days=days)).timestamp()


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


@pytest.fixture
def speech() -> CountingSpeech:
    return CountingSpeech()


@pytest.fixture
def sent() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def handler(db: Database, speech: CountingSpeech, sent: list) -> ActionHandler:
    h = ActionHandler(db, speech, Timings.demo())
    h.send_act = lambda message: sent.append(wire.decode(message)) or True
    return h


@pytest.fixture
def autopilot(db: Database, handler: ActionHandler) -> Autopilot:
    return Autopilot(db, handler, outdoor_target_min=30, wind_down_hhmm="21:30")


def outdoor(db: Database, start: float, minutes: float) -> None:
    db.upsert_episode(Episode(
        id=f"e_{start:.0f}", kind="outdoor_block", start_t=start,
        end_t=start + minutes * 60, duration_s=minutes * 60, open=False,
    ))


def run(autopilot: Autopilot, start: float, end: float, step: float = 60.0) -> None:
    t = start
    while t <= end:
        autopilot.on_tick(t)
        t += step


def auto_decisions(db: Database) -> list:
    return [d for d in db.list_decisions(limit=500) if d.trigger.startswith("autopilot:")]


def test_both_producers_fire_once_per_day(db, autopilot, sent) -> None:
    outdoor(db, ts(9), 10)  # 10 of 30 min by 16:00 on day one; none on day two

    run(autopilot, ts(15, 50), ts(23, 59, days=1))

    assert [(m["type"], m["kind"]) for m in sent] == [
        ("act", "calendar_block"), ("act", "screen_shield"),
        ("act", "calendar_block"), ("act", "screen_shield"),
    ]
    assert sorted(d.trigger for d in auto_decisions(db)) == [
        "autopilot:outdoor", "autopilot:outdoor",
        "autopilot:wind_down", "autopilot:wind_down",
    ]
    # A replayed clock (the stream restarted behind itself) never fires twice.
    run(autopilot, ts(15, 55), ts(22))
    assert len(sent) == 4
    assert all(d.actions[0]["outcome"] == "sent" for d in auto_decisions(db))


def test_the_walk_asks_for_20_min_before_sunset(db, autopilot, sent) -> None:
    run(autopilot, ts(15, 59), ts(16, 1))

    (msg,) = sent
    assert msg["args"] == {"minutes": 20, "earliest": round(ts(16)),
                           "latest": round(ts(18, 30))}  # fallback sunset 19:00
    (decision,) = auto_decisions(db)
    assert decision.actions[0]["id"] == msg["id"]
    # With coordinates it is the real sunset: Houston, 21 Sep 2026, ~00:19 UTC.
    houston = sunset_t(DAY.isoformat(), 29.76, -95.37)
    assert abs(houston - datetime(2026, 9, 22, 0, 19, tzinfo=timezone.utc).timestamp()) < 300


def test_outdoor_target_met_blocks_no_walk(db, autopilot, sent) -> None:
    outdoor(db, ts(8), 20)
    outdoor(db, ts(12), 15)

    run(autopilot, ts(15, 59), ts(21, 31))

    assert [m["kind"] for m in sent] == ["screen_shield"]
    assert sent[0]["args"] == {"until": "07:00"}


def test_act_result_flips_the_decision(db, autopilot, sent, speech, handler) -> None:
    link = GlassesLink()
    link.on_act_result = handler.on_act_result
    run(autopilot, ts(21, 29), ts(21, 31))
    (msg,) = sent

    # Up the real ingest path: the phone's reply, as the socket receives it.
    _handle(link, wire.act_result_message(msg["id"], True, "shield on until 07:00"))

    (decision,) = auto_decisions(db)
    assert decision.actions[0]["outcome"] == ACTED
    assert decision.actions[0]["detail"] == "shield on until 07:00"
    assert speech.said == []


def test_failure_speaks_exactly_once(db, autopilot, sent, speech, handler) -> None:
    outdoor(db, ts(9), 5)
    run(autopilot, ts(15, 59), ts(16, 1))
    (msg,) = sent

    assert handler.on_act_result(msg["id"], False, "no free slot", t=ts(16, 2)) == ACT_FAILED
    # A repeated result for a settled act changes nothing and says nothing.
    assert handler.on_act_result(msg["id"], False, "no free slot", t=ts(16, 3)) is None

    (decision,) = auto_decisions(db)
    assert decision.actions[0]["outcome"] == ACT_FAILED
    assert decision.actions[0]["detail"] == "no free slot"
    assert speech.said == [ACT_FAILED_LINES["calendar_block"]]


def test_no_phone_fails_at_once_and_speaks_once(db, autopilot, speech, handler) -> None:
    handler.send_act = lambda message: False

    run(autopilot, ts(21, 29), ts(21, 31))

    (decision,) = auto_decisions(db)
    assert decision.actions[0]["outcome"] == ACT_FAILED
    assert speech.said == [ACT_FAILED_LINES["screen_shield"]]


def test_a_persona_veto_holds_the_act_back(db, autopilot, sent, speech, handler) -> None:
    handler.act_veto = lambda kind, args, t: "quiet evening" if kind == "screen_shield" else None

    run(autopilot, ts(21, 29), ts(21, 31))

    assert sent == []
    (decision,) = auto_decisions(db)
    assert decision.actions[0]["outcome"] == "vetoed:quiet evening"
    assert speech.said == []


def test_config_defaults_and_a_bad_wind_down_falls_back() -> None:
    settings = Settings(_env_file=None)
    assert (settings.outdoor_target_min, settings.wind_down_hhmm) == (30, "21:30")
    assert Settings(_env_file=None, wind_down_hhmm="9:05").wind_down_hhmm == "09:05"
    assert Settings(_env_file=None, wind_down_hhmm="25:00").wind_down_hhmm == "21:30"
