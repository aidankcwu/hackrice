from __future__ import annotations

import pytest

from pipeline.config import Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder, EpisodeParams
from pipeline.models import AiBlock, SensorBlock, Tick, WatchBlock

#: Every episode test that must not change with GATE_READS_WATCH runs both ways.
READS_WATCH = pytest.mark.parametrize("reads_watch", [False, True])


def tick(seq: int, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=float(seq), seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{seq}",
    )


def watch_tick(seq: int, hot: list[str], **ai: object) -> Tick:
    current = tick(seq, **ai)
    current.watch = WatchBlock(frames=10, usable=10, hot=hot,
                               scores={name: 0.7 for name in hot})
    return current


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
    assert params.entry["food_sighting"] == (
        params.sighting_min_hits, params.sighting_window_s,
    )
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
    assert params.entry["screen_block"] == (2, 20.0)  # 3 hits at 1 Hz, demo
    assert params.entry["conversation"] == (1, 20.0)  # 2 at 1 Hz
    assert params.entry["outdoor_block"] == (1, 20.0)  # 2 at 1 Hz
    assert params.entry["meal"] == (1, 10.0)  # 1 at 1 Hz
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
    """Visible food without eating opens a sighting, not a meal."""

    db = Database(tmp_path / "slow.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(tick_interval_s=1.5))
    for i in (0.0, 1.5, 3.0):
        builder.on_tick(Tick(
            tick_id=f"t_{i}", t=i, seq=int(i), frame_ref=f"f_{i}",
            sensor=SensorBlock(frame_delta=0.1, phash=f"{int(i * 2):016x}"),
            ai=AiBlock(age_ms=0, scene="restaurant", food_present=True),
        ))
    assert "food_sighting" in builder.open_episodes()
    assert "meal" not in builder.open_episodes()
    db.close()


@READS_WATCH
def test_episode_debounce_gap_close_and_upsert(tmp_path, reads_watch: bool) -> None:
    db = Database(tmp_path / "episodes.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
    for i in (0, 2, 4):
        builder.on_tick(tick(i, scene="restaurant", activity="eating", food_present=True, food_type="mixed"))
    meal = builder.open_episodes()["meal"]
    assert meal.start_t == 0
    builder.on_tick(tick(5))
    assert builder.open_episodes()["meal"].open
    for i in range(6, 22):
        builder.on_tick(tick(i, scene="office", food_present=False))
    assert "meal" not in builder.open_episodes()
    stored = db.list_episodes()
    assert stored[0].open is False and stored[0].dominant["food_type"] == "mixed"
    db.close()


@READS_WATCH
def test_honest_food_and_conversation_classification(tmp_path, reads_watch: bool) -> None:
    db = Database(tmp_path / "honest.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
    for i in (0, 2, 4):
        builder.on_tick(tick(i, scene="home", activity="other", food_present=True,
                             caption="inside a refrigerator"))
    assert "food_sighting" in builder.open_episodes()
    assert "meal" not in builder.open_episodes()
    for i in (5, 7, 9):
        builder.on_tick(tick(i, activity="eating", food_present=True, caption="taking a bite"))
    assert "meal" in builder.open_episodes()

    for i in (30, 32, 34):
        builder.on_tick(tick(i, activity="computer_use", people_present=True,
                             people_interacting=False, screen_present=True))
    assert "conversation" not in builder.open_episodes()
    db.close()


@READS_WATCH
def test_three_second_gap_does_not_split_demo_screen_block(tmp_path, reads_watch: bool) -> None:
    db = Database(tmp_path / "gap.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
    for i in (0, 1, 2):
        builder.on_tick(tick(i, screen_present=True))
    episode_id = builder.open_episodes()["screen_block"].id
    for i in (3, 4, 5):
        builder.on_tick(tick(i, screen_present=False))
    for i in (6, 7):
        builder.on_tick(tick(i, screen_present=True))
    assert builder.open_episodes()["screen_block"].id == episode_id
    db.close()


@READS_WATCH
def test_sightings_and_gym_sauna(tmp_path, reads_watch: bool) -> None:
    db = Database(tmp_path / "point.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
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


def test_a_new_builder_closes_episodes_a_previous_process_left_open(tmp_path):
    """A restart must not inherit open rows: they close at their last update."""
    from pipeline.config import Timings
    from pipeline.db import Database
    from pipeline.episodes.builder import EpisodeBuilder
    from pipeline.models import Episode

    db = Database(tmp_path / "stale.db").connect().init_schema()
    db.upsert_episode(Episode(id="e_0001", kind="screen_block", start_t=1000.0, end_t=None,
                              duration_s=90.0, open=True, day="2026-09-12"))
    EpisodeBuilder(db, Timings.demo(1.5))
    rows = db.list_episodes()
    assert len(rows) == 1 and not rows[0].open
    assert rows[0].end_t == 1090.0 and rows[0].duration_s == 90.0
    db.close()


def test_a_tick_re_sent_with_its_ai_counts_once_and_brings_its_evidence(tmp_path) -> None:
    """Publish-on-landing: T0 sends a tick blind, then the same tick_id again
    once Gemini lands. Nearly all ai now arrives that way, so skipping the
    re-send would starve every episode; counting it would double tick_count."""

    timings = Timings.demo(tick_interval_s=1.0)
    db = Database(tmp_path / "resend.db").connect().init_schema()
    builder = EpisodeBuilder(db, timings)
    try:
        seq = 0
        while "screen_block" not in builder.open_episodes():
            builder.on_tick(tick(seq, screen_present=True))
            seq += 1
            assert seq < 60
        episode = builder.open_episodes()["screen_block"]
        before = episode.tick_count
        builder.on_tick(tick(seq))                          # blind
        builder.on_tick(tick(seq, screen_present=True))     # the same tick, landed
        assert episode.tick_count == before + 1
        assert builder._states["screen_block"].last_hit_t == float(seq)

        # Before an episode opens: the landed evidence counts, the tick once.
        fresh = EpisodeBuilder(db, timings)
        fresh.on_tick(tick(100))
        fresh.on_tick(tick(100, caffeine_visible=True))
        state = fresh._states["caffeine_sighting"]
        assert list(state.positives) == [100.0] and state.candidate_ticks == 1
    finally:
        db.close()


# -- reads_watch: episodes also exit on the watcher going cold (PERCEPTION.md phase 3)


def test_reads_watch_is_off_unless_asked() -> None:
    assert EpisodeParams.from_timings(Timings.demo(), True).reads_watch is False
    assert EpisodeParams.from_timings(Timings.demo(), True, reads_watch=True).reads_watch is True
    assert EpisodeParams(3, 10.0, 4, 10.0, 20.0).reads_watch is False


def open_screen_block(db, reads_watch: bool) -> EpisodeBuilder:
    builder = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
    for i in (0, 1, 2):  # entry is unchanged: three Gemini hits open it
        builder.on_tick(watch_tick(i, ["screen_present"], screen_present=True))
    assert "screen_block" in builder.open_episodes()
    return builder


@pytest.mark.parametrize("reads_watch", [False, True])
def test_an_episode_closes_when_its_concept_is_cold_for_the_exit_window(tmp_path, reads_watch: bool) -> None:
    """No labeler misses at all -- the ticks carry no ai -- but the watcher
    has had ``screen_present`` out of ``hot`` for the exit window: with the
    switch on that closes the block; with it off nothing has said "no" yet
    and it stays open."""

    db = Database(tmp_path / "cold.db").connect().init_schema()
    builder = open_screen_block(db, reads_watch)
    exit_window = builder.params.exit_window_s
    assert exit_window == 15.0
    t = 3
    while t - 3 < exit_window:  # cold, but not yet for the whole window
        builder.on_tick(watch_tick(t, []))
        assert "screen_block" in builder.open_episodes(), t
        t += 1
    builder.on_tick(watch_tick(t, []))
    assert ("screen_block" in builder.open_episodes()) is not reads_watch
    if reads_watch:
        stored = db.list_episodes()
        assert len(stored) == 1 and stored[0].open is False and stored[0].end_t == float(t)
    db.close()


def test_a_hot_tick_restarts_the_cold_span_and_blind_ticks_do_not_count(tmp_path) -> None:
    db = Database(tmp_path / "restart.db").connect().init_schema()
    builder = open_screen_block(db, True)
    for t in range(3, 10):
        builder.on_tick(watch_tick(t, []))
    builder.on_tick(watch_tick(10, ["screen_present"]))  # hot again: the span restarts
    for t in range(11, 26):  # cold 11..25: 14 s, one short of the 15 s window
        builder.on_tick(watch_tick(t, []))
    assert "screen_block" in builder.open_episodes()
    # A tick without `watch` neither extends nor resets the cold span.
    builder.on_tick(tick(26))
    assert "screen_block" in builder.open_episodes()
    builder.on_tick(watch_tick(27, []))  # 16 s cold on watch-bearing ticks
    assert "screen_block" not in builder.open_episodes()
    db.close()


def test_labeler_misses_still_close_first_when_they_come_first(tmp_path) -> None:
    """Whichever exit rule is met first wins: here Gemini says no four times
    over 15 s while the watcher is still warm, and the block closes on the
    misses as it does today."""

    db = Database(tmp_path / "misses.db").connect().init_schema()
    builder = open_screen_block(db, True)
    for t in range(3, 19):
        builder.on_tick(watch_tick(t, ["screen_present"], screen_present=False))
    assert "screen_block" not in builder.open_episodes()
    db.close()


def test_point_sightings_ignore_the_watcher(tmp_path) -> None:
    """A sighting closes on its own idle rule, hot or cold; the cold exit is
    for sustained kinds only."""

    db = Database(tmp_path / "sighting.db").connect().init_schema()
    builder = EpisodeBuilder(db, Timings.demo(), reads_watch=True)
    builder.on_tick(watch_tick(0, [], caffeine_visible=True))
    builder.on_tick(watch_tick(1, [], caffeine_visible=True))
    assert "caffeine_sighting" in builder.open_episodes()
    for t in range(2, 14):
        builder.on_tick(watch_tick(t, [], caffeine_visible=True))
    assert "caffeine_sighting" in builder.open_episodes()  # kept by its hits, not the watcher
    db.close()
