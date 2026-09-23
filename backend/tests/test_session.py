from __future__ import annotations

import httpx

from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings, Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder
from pipeline.gate import TriggerGate, default_triggers
from pipeline.models import AiBlock, PendingCheck, SensorBlock, Tick
from pipeline.actions.speech import SpeechLimiter
from pipeline.session import SessionManager


def tick(seq: int, t: float, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=t, seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai), frame_ref=f"f_{seq}",
    )


def components(tmp_path):
    db = Database(tmp_path / "session.db").connect().init_schema()
    timings = Timings.demo(tick_interval_s=1.5)
    episodes = EpisodeBuilder(db, timings)
    escalations = []
    gate = TriggerGate(default_triggers(timings, True), timings, db, episodes,
                       lambda e: escalations.append(e) is None, True)
    speech = SpeechLimiter(timings.speech_min_gap, timings.speech_max_per_hour)
    now = [100.0]
    manager = SessionManager(db, gate, speech, episodes, lambda: now[0])
    return db, episodes, gate, speech, escalations, now, manager


def test_start_resets_gate_cooldown(tmp_path) -> None:
    db, episodes, gate, _, escalations, now, manager = components(tmp_path)
    first = tick(0, 0.0, caffeine_visible=True)
    episodes.on_tick(first)
    gate.on_tick(first)
    second = tick(1, 1.5, caffeine_visible=True)
    episodes.on_tick(second)
    gate.on_tick(second)
    assert [e.trigger for e in escalations] == ["caffeine_seen"]
    assert gate.suppressed["caffeine_seen"] == 1

    now[0] = 1.5
    manager.start("Judge 2")
    third = tick(2, 1.5, caffeine_visible=True)
    episodes.on_tick(third)
    gate.on_tick(third)
    assert [e.trigger for e in escalations] == ["caffeine_seen", "caffeine_seen"]
    db.close()


def test_start_closes_episodes_and_fires_pending_checks(tmp_path) -> None:
    db, episodes, _, _, _, now, manager = components(tmp_path)
    current = tick(0, 0.0, caffeine_visible=True)
    episodes.on_tick(current)
    assert episodes.open_episodes()
    db.insert_pending_check(PendingCheck(id="p1", created_t=0, due_t=999,
                                         reason="leftover watch"))
    now[0] = 12.0
    manager.start()
    saved = db.list_episodes()[0]
    assert saved.open is False
    assert saved.end_t == 12.0
    assert saved.duration_s == 12.0
    assert db.due_pending_checks(1_000) == []
    db.close()


async def test_session_routes_round_trip(tmp_path) -> None:
    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "routes.db"), source="sim",
        reasoner_mode="fake", speed=1, seed_db=False,
    )
    app = create_app(pipeline)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        assert (await client.get("/api/session/current")).json() is None
        started = (await client.post("/api/session/start", json={"name": "Judge A"})).json()
        assert started["name"] == "Judge A" and started["ended_t"] is None
        assert (await client.get("/api/session/current")).json() == started
        status = (await client.get("/api/status")).json()
        assert status["session"]["id"] == started["id"]
        assert status["session"]["elapsed_s"] >= 0
        ended = (await client.post("/api/session/end")).json()
        assert ended["id"] == started["id"] and ended["ended_t"] is not None
        assert (await client.get("/api/session/current")).json() is None
        assert (await client.get("/api/status")).json()["session"] is None
        assert (await client.post("/api/session/end")).status_code == 404
    pipeline.db.close()


async def test_stale_reasoner_work_applies_no_actions(tmp_path) -> None:
    """A T1 call admitted before session start must not speak or watch after it."""
    import asyncio

    from pipeline.actions.speech import clear_spoken, spoken
    from pipeline.frames import InMemoryFrameStore
    from pipeline.models import Escalation
    from pipeline.reasoner.client import FakeReasonerClient
    from pipeline.reasoner.reasoner import Reasoner

    db, episodes, gate, speech, _, now, manager = components(tmp_path)
    clear_spoken()

    class SlowClient(FakeReasonerClient):
        async def complete(self, messages):
            await asyncio.sleep(0.05)
            return await super().complete(messages)

    reasoner = Reasoner(db, InMemoryFrameStore(ttl_s=90), SlowClient(), speech,
                        Settings(db_path=tmp_path / "unused.db"))
    manager.reasoner = reasoner
    # 15:00 local so the fake proposes a speak + watch for caffeine.
    import datetime as _dt
    t = _dt.datetime.now().replace(hour=15, minute=0, second=0).timestamp()
    esc_tick = tick(0, t, caffeine_visible=True)
    assert reasoner.try_escalate(Escalation(trigger="caffeine_seen", t=t, tick=esc_tick,
                                            window=[esc_tick]))
    manager.start("next judge")          # bumps the epoch while the call is in flight
    await asyncio.sleep(0.2)
    assert reasoner.skipped_stale == 1
    assert spoken == []
    assert db.due_pending_checks(t + 10_000) == []
    assert len(db.list_decisions()) == 1  # the row is still written (SPEC §6)
    db.close()


def test_a_session_across_a_stalled_stream_is_not_zero_length(tmp_path):
    """Live defect: the phone stopped sending, the socket stayed open, and an
    eight-second session was stamped start == end -- a log entry covering nothing."""
    import time as _time
    from pipeline.api.wiring import Clock, Pipeline

    now = 1_000_000.0
    pipeline = Pipeline.__new__(Pipeline)          # only the clock path is under test
    pipeline.clock = Clock(wall_start=now, sim_start_t=now, speed=1.0)

    class _Tick:
        t = now
    pipeline.last_tick = _Tick()
    pipeline._last_tick_wall = _time.time() - 8.0  # the last tick arrived 8 s ago

    advanced = Pipeline._session_clock(pipeline)
    assert advanced >= now + 7.5, "a stalled tick clock must still advance with real time"

    pipeline.last_tick = None
    pipeline._last_tick_wall = None
    assert Pipeline._session_clock(pipeline) > 0


def test_auto_session_opens_on_a_frame_and_closes_when_frames_stop(tmp_path):
    """The glasses are the control: streaming opens a session, silence closes it."""
    import time as _time
    from pipeline.api.wiring import AUTO_SESSION_IDLE_S, Pipeline

    class _Sessions:
        def __init__(self): self.open = None; self.started = []; self.ended = []
        def current(self): return self.open
        def start(self, name=""):
            self.open = type("S", (), {"id": f"s_{len(self.started)}", "name": name})()
            self.started.append(name); return self.open
        def end(self):
            ended, self.open = self.open, None
            self.ended.append(ended); return ended

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.settings = type("S", (), {"auto_session": True})()
    pipeline.sessions = _Sessions()
    pipeline.background_tasks = set()
    pipeline._last_tick_wall = None
    recapped = []
    pipeline.spawn_recap = recapped.append

    # No frame yet: nothing to close.
    Pipeline._auto_session_close(pipeline)
    assert pipeline.sessions.ended == []

    # First frame opens exactly one session; later frames do not open more.
    Pipeline._auto_session_open(pipeline)
    Pipeline._auto_session_open(pipeline)
    assert len(pipeline.sessions.started) == 1

    # Frames still arriving: the session stays open.
    pipeline._last_tick_wall = _time.time()
    Pipeline._auto_session_close(pipeline)
    assert pipeline.sessions.open is not None

    # Frames stopped: the session closes and its recap is generated.
    pipeline._last_tick_wall = _time.time() - AUTO_SESSION_IDLE_S - 1
    Pipeline._auto_session_close(pipeline)
    assert pipeline.sessions.open is None
    assert recapped == ["s_0"]

    # Nothing left open, so a second sweep is a no-op.
    Pipeline._auto_session_close(pipeline)
    assert recapped == ["s_0"]


def test_auto_session_can_be_turned_off(tmp_path):
    from pipeline.api.wiring import Pipeline

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.settings = type("S", (), {"auto_session": False})()
    pipeline.sessions = type("S", (), {"current": lambda self: None})()
    Pipeline._auto_session_open(pipeline)   # must not raise, must not start
