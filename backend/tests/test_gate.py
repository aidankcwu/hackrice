from __future__ import annotations

from pipeline.config import Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder
from pipeline.gate import Trigger, TriggerGate, default_triggers
from pipeline.models import AiBlock, PendingCheck, SensorBlock, Tick
from pipeline.sim import DEFAULT_SCENARIO, SimSource


def tick(seq: int, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=float(seq), seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{seq}",
    )


def test_gate_episode_suppression_drop_and_watch(tmp_path) -> None:
    db = Database(tmp_path / "gate.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    seen = []
    gate = TriggerGate(default_triggers(Timings.demo(), True), Timings.demo(), db, episodes, lambda e: seen.append(e) is None, True)
    for i in range(8):
        current = tick(i, scene="restaurant", food_present=True)
        episodes.on_tick(current)
        gate.on_tick(current)
    assert [e.trigger for e in seen].count("food_in_frame") == 1
    assert gate.suppressed["food_in_frame"] > 0

    dropped_gate = TriggerGate(
        [Trigger("always", lambda _: True, 0, None, "test")], Timings.demo(), db,
        episodes, lambda _: False, True,
    )
    assert dropped_gate.on_tick(tick(100)) is not None
    assert dropped_gate.dropped == 1

    db.insert_pending_check(PendingCheck(id="p1", created_t=100, due_t=101, reason="check posture"))
    watch = []
    watch_gate = TriggerGate([], Timings.demo(), db, episodes, lambda e: watch.append(e) is None, True)
    watch_gate.on_tick(tick(101))
    assert watch[0].trigger == "watch:check posture"
    assert db.due_pending_checks(102) == []
    db.close()


def test_cooldown_and_global_gap(tmp_path) -> None:
    db = Database(tmp_path / "limits.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    accepted = []
    triggers = [
        Trigger("a", lambda w: w[-1].seq in {0, 20}, 30, None, "a"),
        Trigger("b", lambda w: w[-1].seq in {5, 40}, 0, None, "b"),
    ]
    gate = TriggerGate(triggers, Timings.demo(), db, episodes, lambda e: accepted.append(e) is None, True)
    for i in (0, 5, 20, 40):
        gate.on_tick(tick(i))
    assert [e.trigger for e in accepted] == ["a", "b"]
    assert gate.suppressed == {"b": 1, "a": 1}
    db.close()


def test_default_scenario(tmp_path, capsys) -> None:
    db = Database(tmp_path / "scenario.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    escalations = []
    gate = TriggerGate(default_triggers(Timings.demo(), True), Timings.demo(), db, episodes, lambda e: escalations.append(e) is None, True)
    source = SimSource(DEFAULT_SCENARIO, frame_store=None, speed=1, seed=0)
    for _ in range(400):
        current = source.next_tick()
        episodes.on_tick(current)
        gate.on_tick(current)
    names = [e.trigger for e in escalations]
    print("scenario escalations:", names)
    assert len(set(names)) >= 3
    assert "caffeine_seen" in names and "alcohol_seen" in names, names
    kinds = {episode.kind for episode in db.list_episodes()}
    assert {"meal", "screen_block", "outdoor_block", "conversation"} <= kinds
    db.close()


def test_suppressed_trigger_does_not_block_others(tmp_path) -> None:
    """A screen_block already escalated must not stop caffeine_seen firing."""
    db = Database(tmp_path / "s.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    escalations = []
    gate = TriggerGate(default_triggers(Timings.demo(), True), Timings.demo(), db, episodes, lambda e: escalations.append(e) is None, True)
    seq = 0
    for _ in range(30):  # screen only -> screen_sustained fires, screen_block opens
        t = tick(seq, screen_present=True); seq += 1
        episodes.on_tick(t); gate.on_tick(t)
    for _ in range(30):  # coffee appears while still at the screen
        t = tick(seq, screen_present=True, caffeine_visible=True); seq += 1
        episodes.on_tick(t); gate.on_tick(t)
    names = [e.trigger for e in escalations]
    assert names[0] == "screen_sustained"
    assert "caffeine_seen" in names, names
    db.close()
