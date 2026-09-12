from __future__ import annotations

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


def test_entry_thresholds_match_demo_trigger_thresholds() -> None:
    timings = Timings.demo()
    params = EpisodeParams.from_timings(timings, demo_mode=True)

    assert params.entry["meal"] == (timings.food_min_hits, timings.food_window)
    assert params.entry["screen_block"] == (
        timings.screen_sustained_min_hits,
        timings.screen_sustained_window,
    )
    assert params.entry["conversation"] == (
        timings.people_sustained_min_hits,
        timings.people_sustained_window,
    )
    assert params.entry["outdoor_block"] == (
        timings.outdoor_min_hits,
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
