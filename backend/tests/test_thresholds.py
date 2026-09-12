"""SPEC §8 coverage and score-function sanity."""

from __future__ import annotations

import math

import pytest

from pipeline.scoring.thresholds import (
    GRADE_WEIGHTS,
    THRESHOLDS,
    MetricSpec,
    band,
    by_period,
    by_source,
    flat_if_present,
    none_is_good,
    ramp,
    step,
)

#: Every row of the SPEC §8 table -> the metric id that scores it.
SPEC_8_ROWS = {
    ("Light", "Daytime melanopic EDI"): "daytime_light_minutes",
    ("Light", "Evening (3 h pre-bed)"): "evening_light_ok",
    ("Sleep", "Duration"): "sleep_hours",
    ("Sleep", "Regularity (SRI)"): "sleep_regularity_sri",
    ("Movement", "Steps"): "steps",
    ("Movement", "VILPA"): "vilpa_minutes",
    ("Movement", "Resistance training"): "resistance_sessions_weekly",
    ("Movement", "Gait speed"): "gait_speed_ms",
    ("Movement", "Balance"): "balance_one_leg_s",
    ("Social", "Integration"): "social_episodes_daily",
    ("Nature", "Weekly dose"): "nature_minutes_weekly",
    ("Heat", "Sauna"): "sauna_sessions_weekly",
    ("Cold", "Cold plunge"): "cold_plunge_weekly",
    ("Stress", "Recovery adequacy"): "hrv_rmssd_ratio",
    ("Stress", "Work hours"): "work_hours_weekly",
    ("Stress", "Breathwork"): "breathwork_minutes",
    ("Diet", "Pattern"): "diet_pattern_daily",
    ("Diet", "Caffeine cutoff"): "caffeine_cutoff_daily",
    ("Diet", "Alcohol"): "alcohol_daily",
    ("Noise", "Night"): "night_noise_db",
    ("Purpose", "Life purpose"): "purpose_score",
}

EDGE_INPUTS = [
    -1e9, -55.0, -1.0, -0.001, 0.0, 0.001, 0.5, 0.9, 1.0, 1.2, 2.0, 3.0, 5.0,
    6.5, 7.0, 9.0, 10.0, 12.0, 30.0, 45.0, 55.0, 60.0, 80.0, 120.0, 300.0,
    7000.0, 1e9,
]


@pytest.mark.parametrize("row,metric", sorted(SPEC_8_ROWS.items()))
def test_every_spec8_row_has_a_metric_spec(row, metric):
    assert metric in THRESHOLDS, f"SPEC §8 row {row} has no MetricSpec"
    spec = THRESHOLDS[metric]
    assert isinstance(spec, MetricSpec)
    assert spec.layer == row[0].lower()
    assert spec.target_text, f"{metric} has no §8 target text"
    assert spec.citation, f"{metric} has no citation"
    assert spec.grade in GRADE_WEIGHTS
    assert spec.source in ("live", "seeded")
    assert spec.period in ("daily", "weekly")


def test_metric_ids_are_snake_case_and_keyed_by_themselves():
    for key, spec in THRESHOLDS.items():
        assert key == spec.metric
        assert key == key.lower()
        assert " " not in key and "-" not in key


def test_source_split_matches_spec_7():
    live = {s.metric for s in by_source("live")}
    seeded = {s.metric for s in by_source("seeded")}
    assert live.isdisjoint(seeded)
    assert live | seeded == set(THRESHOLDS)
    # SPEC §7: anything the camera can see is live.
    assert {"nature_minutes_weekly", "social_episodes_daily", "diet_pattern_daily",
            "caffeine_cutoff_daily", "alcohol_daily"} <= live
    # SPEC §7: light, sleep, HRV and phone sensors are seeded.
    assert {"daytime_light_minutes", "evening_light_ok", "sleep_hours",
            "hrv_rmssd_ratio", "steps", "night_noise_db"} <= seeded


def test_period_split_is_total():
    assert len(by_period("daily")) + len(by_period("weekly")) == len(THRESHOLDS)


@pytest.mark.parametrize("metric", sorted(THRESHOLDS))
@pytest.mark.parametrize("value", EDGE_INPUTS)
def test_score_fns_stay_in_unit_range(metric, value):
    score = THRESHOLDS[metric].score_fn(value)
    assert isinstance(score, float)
    assert not math.isnan(score)
    assert 0.0 <= score <= 1.0


@pytest.mark.parametrize("metric", sorted(THRESHOLDS))
def test_none_scores_zero_with_a_note(metric):
    spec = THRESHOLDS[metric]
    assert spec.score_fn(None) == 0.0
    score, note = spec.evaluate(None)
    assert score == 0.0
    assert note == "no data"


@pytest.mark.parametrize("metric", sorted(THRESHOLDS))
def test_evaluate_clamps_and_keeps_the_standing_note(metric):
    spec = THRESHOLDS[metric]
    score, note = spec.evaluate(1e9)
    assert 0.0 <= score <= 1.0
    assert note == spec.note


# -- the thresholds themselves, at the §8 numbers ------------------------


def test_targets_hit_full_marks_at_the_spec_8_numbers():
    cases = {
        "sleep_hours": (7.0, 8.0, 9.0),
        "sleep_regularity_sri": (80.0, 95.0),
        "steps": (7000.0, 12000.0),
        "vilpa_minutes": (3.0, 10.0),
        "gait_speed_ms": (1.2, 1.6),
        "balance_one_leg_s": (10.0, 30.0),
        "hrv_rmssd_ratio": (1.0, 1.3),
        "breathwork_minutes": (5.0,),
        "night_noise_db": (44.9, 30.0),
        "purpose_score": (5.0,),
        "daytime_light_minutes": (30.0, 90.0),
        "evening_light_ok": (1.0,),
        "nature_minutes_weekly": (120.0, 250.0),
        "social_episodes_daily": (3.0, 6.0),
        "screen_hours_daily": (6.0, 2.0),
        "work_hours_weekly": (40.0, 20.0),
        "meals_logged_daily": (2.0, 3.0),
        "diet_pattern_daily": (1.0,),
        "caffeine_cutoff_daily": (0.0,),
        "alcohol_daily": (0.0,),
        "resistance_sessions_weekly": (2.0, 4.0),
        "sauna_sessions_weekly": (2.0, 3.0, 7.0),
    }
    for metric, values in cases.items():
        for value in values:
            assert THRESHOLDS[metric].score_fn(value) == 1.0, (metric, value)


def test_failing_ends_score_zero():
    assert THRESHOLDS["sleep_hours"].score_fn(4.0) == 0.0
    assert THRESHOLDS["sleep_hours"].score_fn(12.0) == 0.0
    assert THRESHOLDS["sleep_regularity_sri"].score_fn(50.0) == 0.0
    assert THRESHOLDS["screen_hours_daily"].score_fn(12.0) == 0.0
    assert THRESHOLDS["work_hours_weekly"].score_fn(55.0) == 0.0
    assert THRESHOLDS["night_noise_db"].score_fn(60.0) == 0.0
    assert THRESHOLDS["steps"].score_fn(0.0) == 0.0


def test_sleep_is_u_shaped():
    fn = THRESHOLDS["sleep_hours"].score_fn
    assert fn(5.5) < fn(6.5) < fn(7.0) == 1.0
    assert fn(11.0) < fn(10.0) < fn(9.0) == 1.0


def test_alcohol_and_cold_plunge_are_scored_the_way_spec_8_says():
    # "No safe level" -- any sighting is a near-zero, never a pass.
    assert THRESHOLDS["alcohol_daily"].score_fn(0.0) == 1.0
    assert THRESHOLDS["alcohol_daily"].score_fn(1.0) == pytest.approx(0.2)
    assert THRESHOLDS["alcohol_daily"].score_fn(9.0) == pytest.approx(0.2)
    # "No healthspan evidence; log it, score it near zero, say so".
    assert THRESHOLDS["cold_plunge_weekly"].score_fn(5.0) == pytest.approx(0.05)
    assert THRESHOLDS["cold_plunge_weekly"].note and "evidence" in (
        THRESHOLDS["cold_plunge_weekly"].note
    )


def test_caffeine_cutoff_is_a_hard_zero():
    fn = THRESHOLDS["caffeine_cutoff_daily"].score_fn
    assert fn(0.0) == 1.0
    assert fn(1.0) == 0.0


def test_grade_weights_and_spec_weight_property():
    assert GRADE_WEIGHTS == {"A": 1.0, "B": 0.7, "C": 0.3}
    assert THRESHOLDS["steps"].weight == 1.0
    assert THRESHOLDS["balance_one_leg_s"].weight == 0.7
    assert THRESHOLDS["cold_plunge_weekly"].weight == 0.3


# -- the builders --------------------------------------------------------


def test_ramp_descending_and_degenerate():
    assert ramp(6.0, 12.0)(9.0) == pytest.approx(0.5)
    assert ramp(5.0, 5.0)(5.0) == 1.0
    assert ramp(5.0, 5.0)(4.0) == 0.0


def test_band_step_and_flat_builders():
    assert band(7, 9, 4, 12)(5.5) == pytest.approx(0.5)
    assert band(7, 9, 4, 12)(10.5) == pytest.approx(0.5)
    assert step(10.0, below=0.3)(9.99) == pytest.approx(0.3)
    assert none_is_good(0.2)(0.0) == 1.0
    assert flat_if_present(0.05)(0.0) == 0.0
    assert flat_if_present(0.05)(None) == 0.0
