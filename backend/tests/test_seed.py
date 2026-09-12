"""The fixed 7-day dataset: shape, determinism, idempotency, and the pattern."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from pipeline.db import Database, day_key
from pipeline.scoring.thresholds import by_source
from pipeline.seed.fixtures import (
    DAY_COUNT,
    LATE_CAFFEINE_INDEXES,
    SEEDED_SOURCES,
    days_ending,
    seed_live_episodes,
    seed_rows,
)
from pipeline.seed.generate import seed_database, seven_day_summary

#: A Sunday, so the late-caffeine days land on Tue / Thu / Sat.
END_DAY = "2026-09-13"
DAYS = days_ending(END_DAY)
LATE_DAYS = [DAYS[i] for i in LATE_CAFFEINE_INDEXES]


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


def _weekday(day: str) -> str:
    return date.fromisoformat(day).strftime("%a")


def _hour(t: float) -> float:
    dt = datetime.fromtimestamp(t)
    return dt.hour + dt.minute / 60.0


# -- shape ---------------------------------------------------------------


def test_days_ending_is_seven_days_inclusive():
    assert len(DAYS) == DAY_COUNT
    assert DAYS[-1] == END_DAY
    assert DAYS[0] == "2026-09-07"
    assert DAYS == sorted(DAYS)


def test_every_seeded_metric_has_a_row_on_every_day():
    rows = seed_rows(END_DAY)
    per_day: dict[str, set[str]] = {}
    for row in rows:
        per_day.setdefault(row.day, set()).add(row.metric)
    assert set(per_day) == set(DAYS)
    metric_sets = list(per_day.values())
    assert all(m == metric_sets[0] for m in metric_sets)
    # Every seeded MetricSpec is covered, plus the bedtime/waketime inputs.
    assert {s.metric for s in by_source("seeded")} <= metric_sets[0]
    assert {"bed_time", "wake_time"} <= metric_sets[0]
    assert len(rows) == DAY_COUNT * len(metric_sets[0])


def test_rows_carry_spec_7_provenance_and_units():
    for row in seed_rows(END_DAY):
        assert row.source == SEEDED_SOURCES[row.metric]
        assert row.source in ("whoop", "phone", "user")
        assert row.unit
    sources = {r.metric: r.source for r in seed_rows(END_DAY)}
    assert sources["sleep_hours"] == "whoop"
    assert sources["hrv_rmssd_ratio"] == "whoop"
    assert sources["steps"] == "phone"
    assert sources["purpose_score"] == "user"


def test_nature_is_seeded_at_zero_because_the_live_side_supplies_it():
    values = {r.day: r.value for r in seed_rows(END_DAY) if r.metric == "nature_minutes"}
    assert set(values) == set(DAYS)
    assert set(values.values()) == {0.0}


def test_the_dataset_is_fixed_not_random():
    assert [r.model_dump() for r in seed_rows(END_DAY)] == [
        r.model_dump() for r in seed_rows(END_DAY)
    ]
    assert [e.model_dump() for e in seed_live_episodes(END_DAY)] == [
        e.model_dump() for e in seed_live_episodes(END_DAY)
    ]


def test_episodes_cover_the_previous_six_days_only():
    episodes = seed_live_episodes(END_DAY)
    assert all(e.id.startswith("e_seed_") for e in episodes)
    assert all(e.open is False for e in episodes)
    assert len({e.id for e in episodes}) == len(episodes)
    days = {day_key(e.start_t) for e in episodes}
    assert days == set(DAYS[:-1])
    for day in DAYS[:-1]:
        kinds = [e.kind for e in episodes if day_key(e.start_t) == day]
        assert kinds.count("meal") == 1
        assert 1 <= kinds.count("conversation") <= 2
        assert kinds.count("screen_block") == 1
        assert kinds.count("outdoor_block") == 1
    for episode in episodes:
        if episode.kind == "screen_block":
            assert 6.0 <= episode.duration_s / 3600.0 <= 9.0
        if episode.kind == "outdoor_block":
            assert 10.0 <= episode.duration_s / 60.0 <= 25.0


# -- the pattern worth finding (SPEC §6) ---------------------------------


def test_late_caffeine_days_carry_the_whole_pattern():
    rows = {(r.day, r.metric): r.value for r in seed_rows(END_DAY)}
    for day in DAYS:
        late = day in LATE_DAYS
        if late:
            assert rows[(day, "bed_time")] == 0.75  # 00:45
            assert 5.8 <= rows[(day, "sleep_hours")] <= 6.2
            assert 0.82 <= rows[(day, "hrv_rmssd_ratio")] <= 0.86
            assert 60 <= rows[(day, "sleep_regularity_sri")] <= 64
        else:
            assert rows[(day, "bed_time")] == 23.0
            assert 7.4 <= rows[(day, "sleep_hours")] <= 7.8
            assert rows[(day, "hrv_rmssd_ratio")] >= 1.0
            assert rows[(day, "sleep_regularity_sri")] >= 80


def test_the_late_coffee_is_visible_on_the_live_side():
    episodes = seed_live_episodes(END_DAY)
    late_by_day = {
        day_key(e.start_t)
        for e in episodes
        if e.kind == "caffeine_sighting" and _hour(e.start_t) >= 16.0
    }
    assert late_by_day == set(LATE_DAYS)
    # ...and every day has a harmless morning coffee, so the signal is the
    # timing, not the caffeine.
    morning = {
        day_key(e.start_t)
        for e in episodes
        if e.kind == "caffeine_sighting" and _hour(e.start_t) < 12.0
    }
    assert morning == set(DAYS[:-1])


def test_steps_trend_down_across_the_week():
    steps = [r.value for r in seed_rows(END_DAY) if r.metric == "steps"]
    assert steps == sorted(steps, reverse=True)
    assert steps[0] == 9100.0
    assert steps[-1] == 5500.0


# -- loading -------------------------------------------------------------


def test_seed_database_inserts_rows_and_episodes(db):
    result = seed_database(db, END_DAY)
    assert result["skipped"] is False
    assert result["end_day"] == END_DAY
    assert result["days"] == DAYS
    assert result["seeded_rows"] == len(seed_rows(END_DAY))
    assert result["episodes"] == len(seed_live_episodes(END_DAY))
    assert len(db.list_seeded(DAYS[0], DAYS[-1])) == result["seeded_rows"]
    assert sum(len(db.list_episodes(d)) for d in DAYS) == result["episodes"]


def test_seed_database_is_idempotent(db):
    seed_database(db, END_DAY)
    before = [r.model_dump() for r in db.list_seeded(DAYS[0], DAYS[-1])]
    again = seed_database(db, END_DAY)
    assert again["skipped"] is True
    assert again["seeded_rows"] == 0
    assert again["episodes"] == 0
    assert [r.model_dump() for r in db.list_seeded(DAYS[0], DAYS[-1])] == before

    forced = seed_database(db, END_DAY, force=True)
    assert forced["skipped"] is False
    assert [r.model_dump() for r in db.list_seeded(DAYS[0], DAYS[-1])] == before


# -- the T1 stable-prefix summary (SPEC §4.2 part 2) ---------------------


def test_summary_names_the_coffee_to_sleep_pattern(db):
    seed_database(db, END_DAY)
    summary = seven_day_summary(db, END_DAY)
    lines = summary.splitlines()
    assert 6 <= len(lines) <= 10
    assert "coffee" in summary.lower() or "caffeine" in summary.lower()
    for day in LATE_DAYS:
        assert _weekday(day) in summary
    assert ("Tue", "Thu", "Sat") == tuple(_weekday(d) for d in LATE_DAYS)
    assert "6.9" in summary  # mean sleep hours
    assert "9.1k" in summary and "5.5k" in summary  # the step trend
    assert "00:45" in summary  # the bedtime that slipped
    assert "84 min/week" in summary  # live nature dose
    assert "120" in summary  # against the §8 target


def test_summary_is_derived_not_hardcoded(db):
    seed_database(db, END_DAY)
    full = seven_day_summary(db, END_DAY)
    # Drop the live episodes; the live line must change, the seeded ones stay.
    with db._lock:
        db.conn.execute("DELETE FROM episodes")
        db.conn.commit()
    stripped = seven_day_summary(db, END_DAY)
    assert "84 min/week" in full and "84 min/week" not in stripped
    assert "nature 0 min/week" in stripped
    assert "no screen blocks logged" in stripped
    assert "Sleep averaged 6.9 h" in stripped  # seeded rows untouched


def test_summary_on_an_empty_database_says_nothing_false(db):
    summary = seven_day_summary(db, END_DAY)
    assert "0 seeded rows" in summary
    assert "Sleep averaged" not in summary
