"""Scorer over the seeded 7-day dataset (SPEC §10)."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from pipeline.db import Database
from pipeline.models import Episode, Score
from pipeline.scoring import Scorer, THRESHOLDS, rollup
from pipeline.scoring.thresholds import by_period
from pipeline.seed.fixtures import LATE_CAFFEINE_INDEXES, days_ending
from pipeline.seed.generate import seed_database

#: A Sunday, so the late-caffeine days land on Tue / Thu / Sat.
END_DAY = "2026-09-13"
DAYS = days_ending(END_DAY)
LATE_DAYS = [DAYS[i] for i in LATE_CAFFEINE_INDEXES]
NORMAL_DAYS = [d for i, d in enumerate(DAYS) if i not in LATE_CAFFEINE_INDEXES]


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def seeded(db):
    seed_database(db, END_DAY)
    return db


def by_metric(scores: list[Score]) -> dict[str, Score]:
    return {s.metric: s for s in scores}


# -- helpers -------------------------------------------------------------


def test_local_day_matches_the_episode_day_key(db):
    scorer = Scorer(db)
    now = time.time()
    day = scorer.local_day(now)
    assert len(day) == 10 and day.count("-") == 2
    episode = Episode(id="e_x", kind="meal", start_t=now, end_t=now + 60, duration_s=60.0)
    db.upsert_episode(episode)
    assert [e.id for e in db.list_episodes(day)] == ["e_x"]


def test_iso_week_and_week_days():
    scorer = Scorer(None)  # type: ignore[arg-type]
    assert scorer.iso_week("2026-09-13") == "2026-W37"
    assert scorer.iso_week("2026-01-01") == "2026-W01"
    assert scorer.week_days(END_DAY) == DAYS
    assert scorer.week_days(END_DAY)[-1] == END_DAY


# -- daily ---------------------------------------------------------------


def test_score_day_covers_every_daily_metric(seeded):
    scores = by_metric(Scorer(seeded).score_day(DAYS[0]))
    assert set(scores) == {s.metric for s in by_period("daily")}
    for score in scores.values():
        assert score.period == "daily"
        assert score.period_key == DAYS[0]
        assert 0.0 <= score.score <= 1.0
        assert score.target == THRESHOLDS[score.metric].target_text
        assert score.grade in ("A", "B", "C")


def test_late_caffeine_days_score_lower_on_sleep_and_hrv(seeded):
    scorer = Scorer(seeded)
    late = [by_metric(scorer.score_day(d)) for d in LATE_DAYS]
    normal = [by_metric(scorer.score_day(d)) for d in NORMAL_DAYS]

    worst_normal_sleep = min(s["sleep_hours"].score for s in normal)
    best_late_sleep = max(s["sleep_hours"].score for s in late)
    assert best_late_sleep < worst_normal_sleep

    worst_normal_hrv = min(s["hrv_rmssd_ratio"].score for s in normal)
    best_late_hrv = max(s["hrv_rmssd_ratio"].score for s in late)
    assert best_late_hrv < worst_normal_hrv

    # Regularity collapses on those nights too.
    assert max(s["sleep_regularity_sri"].score for s in late) < min(
        s["sleep_regularity_sri"].score for s in normal
    )
    # ...and the HRV rows carry the below-baseline flag.
    for scores in late:
        assert "below baseline" in (scores["hrv_rmssd_ratio"].note or "")
    for scores in normal:
        assert "below baseline" not in (scores["hrv_rmssd_ratio"].note or "")


def test_caffeine_cutoff_uses_the_seeded_bedtime(seeded):
    scorer = Scorer(seeded)
    for day in LATE_DAYS:
        score = by_metric(scorer.score_day(day))["caffeine_cutoff_daily"]
        assert score.score == 0.0
        assert score.value == 1.0  # exactly one late sighting
        # 00:45 bedtime - 9 h = a 15:45 cutoff, and the 16:30 coffee breaks it.
        assert "15:45" in (score.note or "")
        assert "16:30" in (score.note or "")
    # The last of the seven days has no live episodes yet (it is "today").
    for day in [d for d in NORMAL_DAYS if d != END_DAY]:
        score = by_metric(scorer.score_day(day))["caffeine_cutoff_daily"]
        assert score.score == 1.0
        assert score.value == 0.0
        assert "14:00" in (score.note or "")  # 23:00 bedtime - 9 h


def test_live_daily_metrics_come_from_episodes(seeded):
    scores = by_metric(Scorer(seeded).score_day(DAYS[0]))
    assert scores["social_episodes_daily"].value == 2.0
    assert scores["meals_logged_daily"].value == 1.0
    assert scores["screen_hours_daily"].value == pytest.approx(7.5)
    assert "proxy" in (scores["screen_hours_daily"].note or "")
    assert scores["alcohol_daily"].score == 1.0
    # DAYS[0] logged a "mixed" meal -> fully on-pattern; DAYS[1] logged
    # "processed" -> off-pattern.
    assert scores["diet_pattern_daily"].value == pytest.approx(1.0)
    off = by_metric(Scorer(seeded).score_day(DAYS[1]))["diet_pattern_daily"]
    assert off.value == pytest.approx(0.0)
    assert "0/1 meals on-pattern" in (off.note or "")


def test_metrics_with_no_data_score_zero_and_say_so(db):
    scores = by_metric(Scorer(db).score_day("2026-09-13"))
    for metric in ("sleep_hours", "steps", "hrv_rmssd_ratio", "purpose_score"):
        assert scores[metric].value is None
        assert scores[metric].score == 0.0
        assert scores[metric].note == "no data"
    # A day with no meals cannot have a diet pattern -- that is not a zero diet.
    assert scores["diet_pattern_daily"].value is None
    assert scores["diet_pattern_daily"].note == "no data"


# -- weekly --------------------------------------------------------------


def test_nature_weekly_comes_from_the_outdoor_episodes(seeded):
    scores = by_metric(Scorer(seeded).score_week("2026-W37", DAYS))
    nature = scores["nature_minutes_weekly"]
    assert nature.value == pytest.approx(84.0)  # 12+18+10+14+16+14
    assert nature.score == pytest.approx(84.0 / 120.0)
    assert nature.source == "live"
    assert nature.period == "weekly"
    assert nature.period_key == "2026-W37"


def test_work_hours_weekly_is_a_labelled_screen_proxy(seeded):
    scores = by_metric(Scorer(seeded).score_week("2026-W37", DAYS))
    work = scores["work_hours_weekly"]
    # Six days of screen blocks averaging 7.5 h, x7.
    assert work.value == pytest.approx(7.5 * 7.0, rel=1e-6)
    assert "proxy" in (work.note or "")
    assert work.score == pytest.approx((55.0 - 52.5) / 15.0)


def test_weekly_metrics_without_episodes_score_zero(seeded):
    scores = by_metric(Scorer(seeded).score_week("2026-W37", DAYS))
    for metric in ("resistance_sessions_weekly", "sauna_sessions_weekly", "cold_plunge_weekly"):
        assert scores[metric].value == 0.0
        assert scores[metric].score == 0.0


def test_sauna_only_counts_sessions_over_19_minutes(seeded):
    scorer = Scorer(seeded)
    start = scorer._episodes(DAYS[0])[0].start_t
    seeded.upsert_episode(
        Episode(id="e_sauna_short", kind="sauna_session", start_t=start, end_t=start + 600,
                duration_s=600.0, dominant={"scene": "sauna"}, open=False)
    )
    seeded.upsert_episode(
        Episode(id="e_sauna_long", kind="sauna_session", start_t=start, end_t=start + 1500,
                duration_s=1500.0, dominant={"scene": "sauna"}, open=False)
    )
    seeded.upsert_episode(
        Episode(id="e_cold", kind="sauna_session", start_t=start, end_t=start + 180,
                duration_s=180.0, dominant={"scene": "cold_plunge"}, open=False)
    )
    scores = by_metric(scorer.score_week("2026-W37", DAYS))
    assert scores["sauna_sessions_weekly"].value == 1.0
    assert "under 19 min" in (scores["sauna_sessions_weekly"].note or "")
    cold = scores["cold_plunge_weekly"]
    assert cold.value == 1.0
    assert cold.score == pytest.approx(0.05)
    assert "no healthspan evidence" in (cold.note or "")


def test_weekly_sightings_compare_with_the_previous_week(db):
    scorer = Scorer(db)

    def add(day: str, kind: str, index: int) -> None:
        start = datetime.fromisoformat(f"{day}T12:00:00").timestamp() + index
        db.upsert_episode(Episode(
            id=f"e_{kind}_{day}_{index}", kind=kind, start_t=start,
            end_t=start + 1, duration_s=1.0, open=False,
        ))

    days = scorer.week_days(END_DAY)
    previous_days = scorer.week_days("2026-09-06")
    for i in range(5):
        add(previous_days[i], "caffeine_sighting", i)
    for i in range(3):
        add(days[i], "caffeine_sighting", i)
    for i in range(2):
        add(previous_days[i], "alcohol_sighting", i)
    for i in range(4):
        add(days[i], "alcohol_sighting", i)

    scores = by_metric(scorer.score_week("2026-W37", days))
    caffeine = scores["caffeine_sightings_weekly"]
    assert caffeine.value == 3.0
    assert caffeine.score == 1.0
    assert "3 this week" in (caffeine.note or "")
    assert "5 last week" in (caffeine.note or "")
    alcohol = scores["alcohol_sightings_weekly"]
    assert alcohol.value == 4.0
    assert alcohol.score == pytest.approx(0.2)

    assert scorer.sightings_summary(END_DAY) == {
        "caffeine": {"this_week": 3, "last_week": 5},
        "alcohol": {"this_week": 4, "last_week": 2},
    }


def test_weekly_sightings_use_absolute_score_without_previous_data(db):
    scorer = Scorer(db)
    day = scorer.week_days(END_DAY)[0]
    for i in range(14):
        start = datetime.fromisoformat(f"{day}T12:00:00").timestamp() + i
        db.upsert_episode(Episode(
            id=f"e_caffeine_current_{i}", kind="caffeine_sighting", start_t=start,
            end_t=start + 1, duration_s=1.0, open=False,
        ))
    scores = by_metric(scorer.score_week("2026-W37", scorer.week_days(END_DAY)))
    assert scores["caffeine_sightings_weekly"].score == pytest.approx(0.5)
    assert "no prior data" in (scores["caffeine_sightings_weekly"].note or "")
    assert scores["alcohol_sightings_weekly"].score == 1.0


# -- rollup and persistence ----------------------------------------------


def test_rollup_uses_abc_weighting():
    a = Score(metric="steps", layer="movement", period="daily", period_key="d",
              value=7000.0, score=1.0, grade="A")
    c = Score(metric="cold_plunge_weekly", layer="cold", period="weekly", period_key="w",
              value=1.0, score=0.0, grade="C")
    assert rollup([a]) == pytest.approx(1.0)
    assert rollup([a, c]) == pytest.approx(1.0 / 1.3)
    # Rows with no value are excluded, not counted as zeros.
    missing = Score(metric="sleep_hours", layer="sleep", period="daily", period_key="d",
                    value=None, score=0.0, grade="A")
    assert rollup([a, missing]) == pytest.approx(1.0)
    assert rollup([]) == 0.0
    assert rollup([missing]) == 0.0


def test_score_all_returns_both_periods_and_a_bounded_overall(seeded):
    result = Scorer(seeded).score_all(DAYS[0], DAYS)
    assert {s.period for s in result["daily"]} == {"daily"}
    assert {s.period for s in result["weekly"]} == {"weekly"}
    assert 0.0 <= result["overall"] <= 1.0
    assert result["overall"] == pytest.approx(rollup(result["daily"] + result["weekly"]))
    assert result["weekly"][0].period_key == "2026-W37"


def test_score_all_is_worse_on_a_late_caffeine_day(seeded):
    scorer = Scorer(seeded)
    good = scorer.score_all(DAYS[0], DAYS)["overall"]
    bad = scorer.score_all(DAYS[1], DAYS)["overall"]
    assert bad < good


def test_scores_are_upserted_and_readable(seeded):
    scorer = Scorer(seeded)
    scorer.score_all(DAYS[0], DAYS)
    daily = seeded.list_scores("daily", DAYS[0])
    weekly = seeded.list_scores("weekly", "2026-W37")
    assert {s.metric for s in daily} == {s.metric for s in by_period("daily")}
    assert {s.metric for s in weekly} == {s.metric for s in by_period("weekly")}
    stored = {s.metric: s for s in daily}
    assert stored["sleep_hours"].source == "seeded"
    assert stored["social_episodes_daily"].source == "live"
    assert stored["sleep_hours"].target == THRESHOLDS["sleep_hours"].target_text

    # Re-running overwrites rather than duplicating, and is deterministic.
    before = [s.model_dump() for s in seeded.list_scores()]
    scorer.score_all(DAYS[0], DAYS)
    assert [s.model_dump() for s in seeded.list_scores()] == before
