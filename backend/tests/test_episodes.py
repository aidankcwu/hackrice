from __future__ import annotations

import pytest

from pipeline.config import Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder, EpisodeParams
from pipeline.models import AiBlock, SensorBlock, Tick


def tick(seq: int, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=float(seq), seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{seq}",
    )


@pytest.mark.parametrize("interval_s", [1.0, 1.5, 2.0])
def test_entry_thresholds_match_demo_trigger_thresholds(interval_s: float) -> None:
    """SPEC §10: an episode and its escalation must agree on boundaries.

    Both sides read the same ``*_min_hits`` through ``Timings.scaled_hits``,
    so the alignment has to hold at every cadence, not just at 1 Hz.
    """

    timings = Timings.demo(tick_interval_s=interval_s)
    params = EpisodeParams.from_timings(timings, demo_mode=True)
    hits = timings.scaled_hits

    assert params.entry["meal"] == (hits(timings.food_min_hits), timings.food_window)
    assert params.entry["screen_block"] == (
        hits(timings.screen_sustained_min_hits),
        timings.screen_sustained_window,
    )
    assert params.entry["conversation"] == (
        hits(timings.people_sustained_min_hits),
        timings.people_sustained_window,
    )
    assert params.entry["outdoor_block"] == (
        hits(timings.outdoor_min_hits),
        timings.outdoor_sustained_window,
    )
    assert params.entry["caffeine_sighting"] == (
        params.sighting_min_hits,
        params.sighting_window_s,
    )
    assert params.entry["alcohol_sighting"] == (
        params.sighting_min_hits,
        params.sighting_window_s,
    )
    assert params.ai_max_age_ms == timings.ai_max_age_ms


def test_entry_thresholds_at_the_glasses_cadence() -> None:
    """The concrete numbers a 1.5 s stream ends up with."""

    params = EpisodeParams.from_timings(Timings.demo(tick_interval_s=1.5), True)
    assert params.entry["screen_block"] == (5, 20.0)  # was 8 hits at 1 Hz
    assert params.entry["conversation"] == (4, 20.0)  # was 6
    assert params.entry["outdoor_block"] == (4, 20.0)  # was 6
    assert params.entry["meal"] == (1, 10.0)  # was 2
    assert params.sighting_min_hits == 1  # a point sighting, floored at 1
    assert (params.entry_min_hits, params.exit_min_misses) == (2, 3)  # 3 / 4 at 1 Hz
    assert params.ai_max_age_ms == 3750


def test_a_slow_demo_is_still_a_demo(tmp_path) -> None:
    """`Timings.demo(1.5)` must not be mistaken for the production preset."""

    db = Database(tmp_path / "cadence.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(tick_interval_s=1.5))
    assert builder.demo_mode is True
    assert builder.params.entry_window_s == 6.0  # demo debounce, not 10.0
    assert builder.params.unknown_grace_s == 8.0
    db.close()


def test_episodes_open_at_the_slow_cadence(tmp_path) -> None:
    """Two positive ticks 1.5 s apart are enough to open a meal at 1.5 s."""

    db = Database(tmp_path / "slow.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(tick_interval_s=1.5))
    for i in (0.0, 1.5, 3.0):
        builder.on_tick(Tick(
            tick_id=f"t_{i}", t=i, seq=int(i), frame_ref=f"f_{i}",
            sensor=SensorBlock(frame_delta=0.1, phash=f"{int(i * 2):016x}"),
            ai=AiBlock(age_ms=0, scene="restaurant", food_present=True),
        ))
    assert "meal" in builder.open_episodes()
    db.close()


def test_episode_debounce_gap_close_and_upsert(tmp_path) -> None:
    db = Database(tmp_path / "episodes.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo())
    for i in (0, 2, 4):
        builder.on_tick(tick(i, scene="restaurant", activity="eating", food_present=True, food_type="mixed"))
    meal = builder.open_episodes()["meal"]
    assert meal.start_t == 0
    builder.on_tick(tick(5))
    assert builder.open_episodes()["meal"].open
    for i in (6, 7, 8, 9):
        builder.on_tick(tick(i, scene="office", food_present=False))
    assert "meal" not in builder.open_episodes()
    stored = db.list_episodes()
    assert stored[0].open is False and stored[0].dominant["food_type"] == "mixed"
    db.close()


def test_sightings_and_gym_sauna(tmp_path) -> None:
    db = Database(tmp_path / "point.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo())
    builder.on_tick(tick(0, caffeine_visible=True))
    builder.on_tick(tick(5, caffeine_visible=True))
    assert "caffeine_sighting" in builder.open_episodes()
    builder.on_tick(tick(20, caffeine_visible=False))
    assert "caffeine_sighting" not in builder.open_episodes()

    builder.on_tick(tick(21, alcohol_visible=True))
    builder.on_tick(tick(25, alcohol_visible=True))
    assert "alcohol_sighting" in builder.open_episodes()
    builder.on_tick(tick(40, alcohol_visible=False))
    assert "alcohol_sighting" not in builder.open_episodes()

    for scene, kind, base in (("gym", "gym_session", 50), ("sauna", "sauna_session", 70)):
        for offset in (0, 2, 4):
            builder.on_tick(tick(base + offset, scene=scene))
        assert kind in builder.open_episodes()
        for offset in (5, 6, 7, 8):
            builder.on_tick(tick(base + offset, scene="home"))
        assert kind not in builder.open_episodes()
    db.close()
