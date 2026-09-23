"""US-M05: an armed ``watch`` end to end (docs/PERCEPTION.md "Gate and actions").

Schema and normalize; the handler arming through the capture bridge (or falling
back to a timed pending check); the gate's ``escalate_armed``; and the path a
watcher wake-up takes from the T0 loop's hook, through the bridge, to the gate.
Fakes as in ``test_look``; the bridge test builds a real ``LongevityCapture``
over a replay corpus with the fake watcher model, as ``test_capture_bridge`` does.
"""

from __future__ import annotations

import io
import json
import logging
import time
from typing import Any

import pytest
from longevity.watcher import Wakeup
from PIL import Image

from pipeline.actions.handlers import (
    MAX_ARMED_WATCHES,
    WATCH_ARMED,
    WATCH_ARMED_UNAVAILABLE,
    ActionHandler,
    armed_watch_id,
)
from pipeline.actions.speech import SpeechLimiter, clear_spoken
from pipeline.bus import TickBus
from pipeline.capture.bridge import LongevityCapture
from pipeline.config import Settings, Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder
from pipeline.gate.gate import TriggerGate
from pipeline.gate.triggers import Trigger
from pipeline.models import AiBlock, SensorBlock, Tick
from pipeline.reasoner.schema import (
    T1_JSON_SCHEMA,
    WATCH_CONCEPT_UNKNOWN,
    WATCH_WITHIN_DEFAULT_S,
    LogInsightAction,
    T1Response,
    WatchAction,
    normalize,
)

T0 = 1_757_700_000.0
DECISION = "d_0001"
CONCEPT = "screen_present"


# -- fakes ------------------------------------------------------------------


class FakeCapture:
    """``arm`` records the call and answers as a bridge with a watcher would;
    ``has_watcher=False`` answers as one without."""

    on_armed_wake = None

    def __init__(self, has_watcher: bool = True) -> None:
        self.has_watcher = has_watcher
        self.armed: list[tuple[str, float, str]] = []
        self.disarmed: list[str] = []

    def arm(self, concept: str, within_s: float, watch_id: str) -> bool:
        if not self.has_watcher:
            return False
        self.armed.append((concept, within_s, watch_id))
        return True

    def disarm(self, watch_id: str) -> None:
        self.disarmed.append(watch_id)


class FakeGate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, str]] = []

    def escalate_armed(self, watch_id: str, concept: str, t: float, decision_id: str):
        self.calls.append((watch_id, concept, t, decision_id))
        return "escalated"


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
def timings():
    return Timings.demo()


def make_handler(db, timings, **kw) -> ActionHandler:
    return ActionHandler(db, SpeechLimiter(min_gap_s=0, max_per_hour=10), timings, **kw)


def response(*actions) -> T1Response:
    return T1Response(interpretation="…", confidence=0.5, actions=list(actions))


def kinds(resp) -> list[str]:
    return [a.type for a in resp.actions]


def tick(seq: int, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=float(seq), seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{seq}",
    )


# -- schema and normalize ---------------------------------------------------


def test_watch_action_accepts_a_concept_and_within_s():
    resp = T1Response.model_validate({
        "interpretation": "screen again", "confidence": 0.7,
        "actions": [{"type": "watch", "after_s": None, "condition": None,
                     "reason": "screen back?", "concept": CONCEPT, "within_s": 300}],
    })
    (watch,) = resp.actions
    assert isinstance(watch, WatchAction)
    assert (watch.concept, watch.within_s) == (CONCEPT, 300)
    assert WatchAction(reason="plain").concept is None
    variants = {v["properties"]["type"]["enum"][0]: v
                for v in T1_JSON_SCHEMA["properties"]["actions"]["items"]["anyOf"]}
    assert {"concept", "within_s"} <= set(variants["watch"]["required"])
    assert CONCEPT in variants["watch"]["properties"]["concept"]["description"]
    json.dumps(T1_JSON_SCHEMA)


def test_normalize_drops_a_watch_on_an_unknown_concept(caplog):
    caplog.set_level(logging.INFO, logger="pipeline.reasoner.schema")
    norm = normalize(response(WatchAction(concept="unicorn_visible", within_s=60),
                              LogInsightAction(text="screen")), t=T0)
    assert kinds(norm) == ["log_insight", "annotate"]
    assert WATCH_CONCEPT_UNKNOWN in caplog.text and "unicorn_visible" in caplog.text


def test_normalize_fills_and_clamps_within_s():
    norm = normalize(response(WatchAction(concept=CONCEPT),
                              WatchAction(concept="phone_in_hand", within_s=3),
                              WatchAction(concept="food_present", within_s=99_999)), t=T0)
    watches = [a for a in norm.actions if a.type == "watch"]
    assert [w.within_s for w in watches] == [WATCH_WITHIN_DEFAULT_S, 10, 7200]
    assert [w.concept for w in watches] == [CONCEPT, "phone_in_hand", "food_present"]
    # A plain timed watch is untouched.
    plain = normalize(response(WatchAction(after_s=30, reason="posture")), t=T0)
    assert plain.actions[0].after_s == 30 and plain.actions[0].concept is None


# -- handler ----------------------------------------------------------------


def test_watch_with_a_concept_arms_via_the_capture_and_records_watch_armed(db, timings):
    capture = FakeCapture()
    handler = make_handler(db, timings, capture=capture)
    assert capture.on_armed_wake == handler.on_armed_wake, "assigning capture registers"

    result = handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT, within_s=120)))

    watch_id = armed_watch_id(DECISION, CONCEPT)
    assert capture.armed == [(CONCEPT, 120.0, watch_id)]
    assert result["outcomes"] == {0: {"outcome": WATCH_ARMED, "watch_id": watch_id}}
    assert result["watches"] == 1
    assert handler._armed == {watch_id: (DECISION, CONCEPT, T0 + 120.0)}
    assert db.due_pending_checks(T0 + 10_000) == [], "no pending check when armed"


def test_watch_without_a_capture_falls_back_to_a_pending_check(db, timings):
    handler = make_handler(db, timings)
    result = handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT, within_s=60,
                                                              reason="screen back?")))
    watch_id = armed_watch_id(DECISION, CONCEPT)
    assert result["outcomes"] == {0: {"outcome": WATCH_ARMED_UNAVAILABLE, "watch_id": watch_id}}
    assert handler._armed == {}
    (check,) = db.due_pending_checks(T0 + 60)
    assert (check.due_t, check.condition, check.reason, check.decision_id) == (
        T0 + 60, CONCEPT, "screen back?", DECISION)


def test_watch_with_a_capture_but_no_watcher_is_unavailable_too(db, timings):
    handler = make_handler(db, timings, capture=FakeCapture(has_watcher=False))
    result = handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT)))
    assert result["outcomes"][0]["outcome"] == WATCH_ARMED_UNAVAILABLE
    assert len(db.due_pending_checks(T0 + WATCH_WITHIN_DEFAULT_S)) == 1


def test_more_than_eight_armed_watches_evict_the_oldest(db, timings):
    handler = make_handler(db, timings, capture=FakeCapture())
    for i in range(MAX_ARMED_WATCHES + 2):
        handler.apply(f"d_{i:04d}", T0 + i, response(WatchAction(concept=CONCEPT, within_s=600)))
    assert len(handler._armed) == MAX_ARMED_WATCHES
    assert armed_watch_id("d_0000", CONCEPT) not in handler._armed
    assert armed_watch_id("d_0001", CONCEPT) not in handler._armed
    assert armed_watch_id(f"d_{MAX_ARMED_WATCHES + 1:04d}", CONCEPT) in handler._armed


def test_an_armed_wake_reaches_the_gate_with_the_decision_id_once(db, timings):
    handler = make_handler(db, timings, capture=FakeCapture())
    gate = FakeGate()
    handler.gate = gate
    handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT, within_s=120)))
    watch_id = armed_watch_id(DECISION, CONCEPT)

    assert handler.on_armed_wake(watch_id, CONCEPT, T0 + 30.0) == "escalated"
    assert gate.calls == [(watch_id, CONCEPT, T0 + 30.0, DECISION)]
    assert handler._armed == {}, "an armed watch is one-shot"
    # Unknown (spent, evicted, or never armed): ignored, nothing escalates.
    assert handler.on_armed_wake(watch_id, CONCEPT, T0 + 31.0) is None
    assert handler.on_armed_wake("d_9999/food_present", "food_present", T0) is None
    assert len(gate.calls) == 1


def test_an_armed_wake_without_a_gate_is_only_logged(db, timings, caplog):
    caplog.set_level(logging.INFO, logger="pipeline.actions.handlers")
    handler = make_handler(db, timings, capture=FakeCapture())
    handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT)))
    assert handler.on_armed_wake(armed_watch_id(DECISION, CONCEPT), CONCEPT, T0) is None
    assert "no gate is wired" in caplog.text


def test_disarm_forgets_here_and_on_the_capture(db, timings):
    capture = FakeCapture()
    handler = make_handler(db, timings, capture=capture)
    handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT)))
    watch_id = armed_watch_id(DECISION, CONCEPT)
    handler.disarm_watch(watch_id)
    assert handler._armed == {} and capture.disarmed == [watch_id]


# -- gate: escalate_armed ---------------------------------------------------


def make_gate(db, accept, triggers=()):
    episodes = EpisodeBuilder(db, Timings.demo())
    return TriggerGate(list(triggers), Timings.demo(), db, episodes, accept, True)


def test_escalate_armed_builds_a_watch_armed_escalation_through_submit(db):
    seen = []
    gate = make_gate(db, lambda e: seen.append(e) is None)
    gate.on_tick(tick(1, scene="office", screen_present=True))

    esc = gate.escalate_armed("d_0001/screen_present", CONCEPT, 5.0, "d_0001")

    assert esc is not None and seen == [esc]
    assert esc.trigger == "watch_armed"
    assert esc.reason == "armed watch d_0001/screen_present: screen_present came back"
    assert "d_0001" in esc.reason and CONCEPT in esc.reason
    assert esc.extra_text[0] == "armed by decision d_0001 on screen_present"
    assert esc.t == 5.0 and esc.tick.tick_id == "t_1" and [w.tick_id for w in esc.window] == ["t_1"]
    assert gate.fired["watch_armed"] == 1 and gate.last_escalation_t == 5.0


def test_escalate_armed_respects_the_global_gap_but_not_a_per_trigger_cooldown(db):
    seen = []
    gate = make_gate(db, lambda e: seen.append(e) is None)
    assert Timings.demo().global_escalation_min_gap == 2.0
    gate.on_tick(tick(1))

    assert gate.escalate_armed("w1", CONCEPT, 5.0, "d_1") is not None
    # A second one inside the 2 s global gap is dropped ...
    assert gate.escalate_armed("w2", CONCEPT, 6.0, "d_2") is None
    assert gate.suppressed["watch_armed"] == 1
    # ... and one just past it goes through: no 30 s per-trigger cooldown
    # holds an armed watch, since no trigger cooldown applies to it at all.
    assert gate.escalate_armed("w3", CONCEPT, 7.5, "d_3") is not None
    assert [e.trigger for e in seen] == ["watch_armed", "watch_armed"]
    assert gate.fired["watch_armed"] == 2
    # And it shares the gap with the triggers: a trigger firing at 8.0 is held.
    fired_at = []
    trig = Trigger("a", lambda w: w[-1].seq == 8, 0, None, "a", on_fired=fired_at.append)
    gate.triggers.append(trig)
    gate.on_tick(tick(8))
    assert fired_at == [] and gate.suppressed["a"] == 1


def test_escalate_armed_dropped_on_contention_counts_and_needs_a_tick(db):
    gate = make_gate(db, lambda e: False)
    assert gate.escalate_armed("w1", CONCEPT, 5.0, "d_1") is None, "no tick yet"
    gate.on_tick(tick(1))
    esc = gate.escalate_armed("w1", CONCEPT, 5.0, "d_1")
    assert esc is not None and esc.trigger == "watch_armed"
    assert gate.dropped == 1 and gate.fired["watch_armed"] == 0
    assert gate.last_escalation_t is None, "a dropped escalation does not start the gap"


# -- bridge: loop hook -> on_armed_wake -> gate --------------------------------


def _corpus(path, count=2):
    base = int(time.time() * 1000)
    for i in range(count):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (20 * i, 40, 80)).save(buf, "JPEG")
        (path / f"frame_{base + i * 1000}.jpg").write_bytes(buf.getvalue())


@pytest.fixture
def capture(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER", "1")
    monkeypatch.setenv("WATCHER_MODEL", "fake")
    _corpus(tmp_path)
    bridge = LongevityCapture(
        Settings(db_path=tmp_path / "unused.db", tick_interval_s=1.0),
        source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=50, loop=False,
        camera=0, vlm="off", flow=None,
    )
    assert bridge.watcher is not None and bridge.watcher_error is None
    return bridge


def test_the_bridge_arms_the_watcher_and_routes_armed_wakeups_to_the_gate(db, timings, capture):
    handler = make_handler(db, timings, capture=capture)
    gate = FakeGate()
    handler.gate = gate
    assert capture.on_armed_wake == handler.on_armed_wake
    assert capture.loop.on_wakeup_forwarded == capture._on_wakeup_forwarded

    handler.apply(DECISION, T0, response(WatchAction(concept=CONCEPT, within_s=120)))
    watch_id = armed_watch_id(DECISION, CONCEPT)
    assert capture.watcher.stats()["armed"] == 1

    # The T0 loop's hook, exactly as `_forward_wakeup` calls it.
    capture.loop.on_wakeup_forwarded(Wakeup(T0 + 40.0, (CONCEPT,), 0.1, f"watch_armed:{watch_id}"))
    assert gate.calls == [(watch_id, CONCEPT, T0 + 40.0, DECISION)]

    # A concept or novelty wake-up is not an armed one; an unknown id is ignored.
    capture.loop.on_wakeup_forwarded(Wakeup(T0 + 41.0, (CONCEPT,), 0.1, "concept"))
    capture.loop.on_wakeup_forwarded(Wakeup(T0 + 42.0, (), 0.9, "novelty"))
    capture.loop.on_wakeup_forwarded(Wakeup(T0 + 43.0, (CONCEPT,), 0.1, "watch_armed:d_9/x"))
    assert len(gate.calls) == 1

    capture.disarm(watch_id)
    assert capture.watcher.stats()["armed"] == 0


def test_a_bridge_without_a_watcher_cannot_arm(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER", "0")
    _corpus(tmp_path)
    bridge = LongevityCapture(
        Settings(db_path=tmp_path / "unused.db", tick_interval_s=1.0),
        source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=50, loop=False,
        camera=0, vlm="off", flow=None,
    )
    assert bridge.watcher is None
    assert bridge.arm(CONCEPT, 60.0, "w1") is False
    bridge.disarm("w1")  # a no-op, never raises
