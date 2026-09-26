"""Stage kill switches: each new reactive behaviour turns fully off from env.

FAST_PATH=0 sends persona cues through the clerk like any other trigger (and
takes away their global-gap exemption); CUE_TRIGGER=0 removes the one-tick cue
trigger; VLM_MAX_IN_FLIGHT=1 restores the serial tagger; PUBLISH_ON_LANDING=0,
MOUTH_BUSY_GUARD=0 and VOICE_OPEN_SCHEMA=0 are pinned where they act
(test_capture_bridge, test_conversation, test_voice_client) and here in wiring. These pin the OFF states, since the ON states are what the
rest of the suite already exercises.
"""

from __future__ import annotations

import logging

from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings, Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder
from pipeline.gate import TriggerGate, default_triggers
from pipeline.models import AiBlock, Escalation, SensorBlock, Tick

TIMINGS = Timings.demo(tick_interval_s=1.5)
DESK = dict(scene="office", activity="computer_use", objects=["laptop"],
            caption="laptop on a desk", food_present=False, food_type="none",
            drink="none", in_hand=None, phone_in_hand=None, people_count="1-2")
TREAT = dict(in_hand="rice krispies treat", food_present=True,
             food_type="baked_goods", caption="holding a rice krispies treat",
             objects=["rice krispies treat", "laptop"])


def tick(seq: int, **fields: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=seq * 1.5, seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock.model_validate({"age_ms": 0, **DESK, **fields}),
        frame_ref=f"f_{seq}",
    )


def run_gate(tmp_path, *, cues: bool, fast_path, ticks: list[Tick]):
    db = Database(tmp_path / "switch.db").connect().init_schema()
    episodes = EpisodeBuilder(db, TIMINGS)
    clerk: list[Escalation] = []
    gate = TriggerGate(default_triggers(TIMINGS, True, cues=cues), TIMINGS, db, episodes,
                       lambda e: clerk.append(e) is None, True, fast_path=fast_path)
    try:
        for current in ticks:
            episodes.on_tick(current)
            gate.on_tick(current)
        return gate, clerk
    finally:
        db.close()


def test_fast_path_off_sends_the_cue_to_the_clerk(tmp_path) -> None:
    """FAST_PATH=0 is gate.fast_path None: the cue still fires on one tick,
    but it wakes the clerk (which may speak it) instead of the voice agent."""

    gate, clerk = run_gate(tmp_path, cues=True, fast_path=None,
                           ticks=[tick(0), tick(1), tick(2, **TREAT), tick(3, **TREAT)])
    cue = [e for e in clerk if e.trigger == "cue"]
    assert len(cue) == 1, [e.trigger for e in clerk]
    assert cue[0].cue == "food:treat" and cue[0].handed_off is None
    assert gate.fast_pathed == 0 and not gate.fast_dropped
    assert gate.stats()["fast_path_enabled"] is False


def test_cue_trigger_off_keeps_only_the_old_triggers(tmp_path) -> None:
    """CUE_TRIGGER=0: no one-tick cue, and `change` goes back to reporting the
    hand itself (it needs its two agreeing ticks, as before the fast path)."""

    names = [t.name for t in default_triggers(TIMINGS, True, cues=False)]
    assert "cue" not in names and names[0] == "change"

    mouth: list[Escalation] = []
    gate, clerk = run_gate(
        tmp_path, cues=False, fast_path=lambda e: mouth.append(e) or "handed_off:x",
        ticks=[tick(0), tick(1), tick(2), tick(3, **TREAT), tick(4, **TREAT)])
    assert mouth == [], "no cue trigger, so nothing takes the fast path"
    assert [e.trigger for e in clerk] == ["change"]
    assert "food" in (clerk[0].reason or "")
    assert gate.stats()["cue_trigger_enabled"] is False


def test_wiring_honours_both_gate_switches_and_logs_them_once(tmp_path, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="pipeline.api.wiring"):
        off = build_pipeline(
            Settings(_env_file=None, db_path=tmp_path / "off.db",  # type: ignore[call-arg]
                     fast_path=False, cue_trigger=False),
            source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    try:
        assert off.gate.fast_path is None
        assert all(t.name != "cue" for t in off.gate.triggers)
    finally:
        off.db.close()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("switches:")]
    assert lines == ["switches: FAST_PATH=0 CUE_TRIGGER=0 VLM_MAX_IN_FLIGHT=2 "
                     "PUBLISH_ON_LANDING=1 MOUTH_BUSY_GUARD=1 VOICE_OPEN_SCHEMA=1"]

    on = build_pipeline(Settings(_env_file=None, db_path=tmp_path / "on.db"),  # type: ignore[call-arg]
                        source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    try:
        assert on.gate.fast_path == on.reasoner.fast_path
        assert on.gate.triggers[0].name == "cue"
        assert on.gate.stats()["fast_path_enabled"] is True
    finally:
        on.db.close()


def test_fast_path_off_makes_the_cue_wait_out_the_global_gap(tmp_path) -> None:
    """The cue's gap exemption is justified only because it skips the clerk's
    queue. With FAST_PATH=0 it joins that queue, so a cue 1 s after another
    escalation is held by the gap instead of taking the one T1 slot."""

    for bypass, expect_at_once in ((True, True), (False, False)):
        db = Database(tmp_path / f"gap_{bypass}.db").connect().init_schema()
        episodes = EpisodeBuilder(db, TIMINGS)
        clerk: list[Escalation] = []
        gate = TriggerGate(default_triggers(TIMINGS, True, cue_bypass_gap=bypass),
                           TIMINGS, db, episodes, lambda e: clerk.append(e) is None, True,
                           fast_path=None)
        try:
            for current in (tick(0), tick(1)):
                episodes.on_tick(current)
                gate.on_tick(current)
            assert clerk == []
            cue_tick = tick(2, **TREAT)
            assert TIMINGS.global_escalation_min_gap > 1.0
            gate.last_escalation_t = cue_tick.t - 1.0  # another escalation 1 s ago
            episodes.on_tick(cue_tick)
            gate.on_tick(cue_tick)
            fired = [e.tick.tick_id for e in clerk if e.trigger == "cue"]
            assert (fired == ["t_2"]) is expect_at_once, (bypass, fired)
        finally:
            db.close()


def test_wiring_takes_the_gap_exemption_away_with_fast_path_off(tmp_path) -> None:
    off = build_pipeline(
        Settings(_env_file=None, db_path=tmp_path / "gapoff.db",  # type: ignore[call-arg]
                 fast_path=False),
        source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    try:
        assert off.gate.triggers[0].name == "cue"
        assert not off.gate.triggers[0].bypass_gap
    finally:
        off.db.close()


def test_wiring_passes_the_mouth_guard_switch_from_settings(tmp_path) -> None:
    """From Settings, not os.environ: a value in .env must work in sim mode,
    where nothing calls load_dotenv before the agent is built."""

    for guard in (False, True):
        pipeline = build_pipeline(
            Settings(_env_file=None, db_path=tmp_path / f"guard_{guard}.db",  # type: ignore[call-arg]
                     mouth_busy_guard=guard),
            source="sim", reasoner_mode="fake", speed=1, seed_db=False)
        try:
            assert pipeline.reasoner.conversation.mouth_guard is guard
        finally:
            pipeline.db.close()


def test_seeded_context_is_on_by_default_and_turns_off_from_env(monkeypatch) -> None:
    monkeypatch.delenv("SEEDED_CONTEXT", raising=False)
    assert Settings(_env_file=None).seeded_context is True  # type: ignore[call-arg]
    monkeypatch.setenv("SEEDED_CONTEXT", "0")
    assert Settings(_env_file=None).seeded_context is False  # type: ignore[call-arg]


def test_seeded_context_off_keeps_seeded_history_away_from_the_models(tmp_path) -> None:
    """SEEDED_CONTEXT=0: the clerk reads NO_SEVEN_DAY and an escalation carries
    no "wearable now" line, although the seeded rows are still in the
    database. On (the default), both come through as before."""

    from pipeline.reasoner import NO_SEVEN_DAY

    for seeded in (True, False):
        p = build_pipeline(
            Settings(_env_file=None, db_path=tmp_path / f"seeded_{seeded}.db",  # type: ignore[call-arg]
                     seeded_context=seeded),
            source="sim", reasoner_mode="fake", speed=1, seed_db=True)
        try:
            t = p.source.start_t + 60.0
            esc = Escalation(trigger="change", t=t, tick=tick(0))
            _today, seven_day = p.reasoner._context(esc)
            wearable = p.gate._wearable_lines(t)
            if seeded:
                assert seven_day != NO_SEVEN_DAY and seven_day.strip()
                assert p.gate.feed is not None and wearable, wearable
            else:
                assert seven_day == NO_SEVEN_DAY
                assert p.gate.feed is None and wearable == []
            # The seeded rows themselves are untouched either way.
            assert p.db.biometric_series("heart_rate", t - 600, t + 600)
        finally:
            p.db.close()


def test_seeded_context_off_removes_the_heart_rate_trigger(tmp_path) -> None:
    """The seeded series has a planted heart-rate spike; with SEEDED_CONTEXT=0
    a visitor wearing no watch must not wake the clerk with it."""

    for seeded in (True, False):
        p = build_pipeline(
            Settings(_env_file=None, db_path=tmp_path / f"hr_{seeded}.db",  # type: ignore[call-arg]
                     seeded_context=seeded),
            source="sim", reasoner_mode="fake", speed=1, seed_db=False)
        try:
            names = [t.name for t in p.gate.triggers]
            assert ("biometric_anomaly" in names) is seeded, names
        finally:
            p.db.close()


def test_screen_trigger_is_on_by_default_and_turns_off_from_env(monkeypatch) -> None:
    monkeypatch.delenv("SCREEN_TRIGGER", raising=False)
    assert Settings(_env_file=None).screen_trigger is True  # type: ignore[call-arg]
    monkeypatch.setenv("SCREEN_TRIGGER", "0")
    assert Settings(_env_file=None).screen_trigger is False  # type: ignore[call-arg]


def test_screen_trigger_off_removes_the_sustained_screen_trigger(tmp_path) -> None:
    """SCREEN_TRIGGER=0: a visitor at a desk is never coached about the screen
    in front of them, because the trigger is not in the gate at all."""

    for screen in (True, False):
        p = build_pipeline(
            Settings(_env_file=None, db_path=tmp_path / f"screen_{screen}.db",  # type: ignore[call-arg]
                     screen_trigger=screen),
            source="sim", reasoner_mode="fake", speed=1, seed_db=False)
        try:
            names = [t.name for t in p.gate.triggers]
            assert ("screen_sustained" in names) is screen, names
        finally:
            p.db.close()
