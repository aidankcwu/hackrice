"""The dose-response engine on its own (``scoring/brian_score.py``).

The engine is the user's file and stays verbatim; these tests pin the
behaviour the adapter (``scoring/healthspan.py``) relies on, including the
quirks it works around. Nothing here invokes ``__main__`` or prints -- the
engine's strings carry non-ASCII arrows, so every assertion is on an ASCII
substring or a number.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from pipeline.scoring import brian_score as bs

#: The self-test's 20-year-old with a 23:00 bedtime.
ME = bs.Profile(age=20, sex="M", goal="average", bedtime_hh=23.0)

#: The self-test's day of observations (every factor measured).
TODAY = {
    "steps": 9100, "vilpa_min": 2, "resistance_min_wk": 40, "fitness_pct": 70, "gait_speed": 1.35,
    "sleep_hours": 7.4, "sri": 84, "day_light_min": 44, "night_light_lux": 2,
    "social_index": 58, "nature_min_wk": 35, "noise_night_db": 45, "med_adherence": 0.6,
    "alcohol_drinks": 2, "smoker": 0, "sauna_wk": 0, "recovery_ratio": 1.02,
    "last_caffeine_hh": 16, "night_screen_min": 40, "planned_bed_shift_min": 0,
}

#: The self-test's three ledger days.
WEEK = [
    {"nature_min_today": 15, "resistance_min_today": 40, "sauna_today": 0, "steps": 9100,
     "day_light_min": 44, "vilpa_min": 2, "social_index": 58},
    {"nature_min_today": 20, "resistance_min_today": 0, "sauna_today": 0, "steps": 8500,
     "day_light_min": 30, "vilpa_min": 1, "social_index": 40},
    {"nature_min_today": 0, "resistance_min_today": 0, "sauna_today": 0, "steps": 7900,
     "day_light_min": 20, "vilpa_min": 0, "social_index": 62},
]


def synthetic_effect() -> bs.Effect:
    """The self-test's 30-day synthetic: green minutes -> next-night ln HRV, slope 0.004."""

    rng = np.random.default_rng(1)
    green = rng.uniform(0, 60, 30)
    strain = rng.uniform(5, 15, 30)
    weekday = np.arange(30) % 7
    ln_hrv = 3.7 + 0.004 * green - 0.01 * strain + rng.normal(0, 0.05, 30)
    return bs.attribute(green, ln_hrv, covariates=strain, weekday=weekday, prior_beta=0.002,
                        prior_se=0.003, exposure_name="green-view minutes",
                        outcome_name="next-night ln HRV")


def by_key(day: bs.DayScore) -> dict[str, bs.FactorResult]:
    return {f.key: f for f in day.factors}


# -- scoring ---------------------------------------------------------------


def test_best_doses_score_above_99_and_worst_below_1():
    best = {k: f.best_dose() for k, f in bs.FACTORS.items()}
    worst = {k: f.worst_dose() for k, f in bs.FACTORS.items()}
    assert bs.score_day(best, ME).overall > 99
    assert bs.score_day(worst, ME).overall < 1


def test_athlete_profile_overall_within_bounds_and_reweights():
    athlete = bs.Profile(age=20, sex="M", goal="athlete")
    assert 0 <= bs.score_day(TODAY, athlete).overall <= 100
    assert athlete.factor_weight(bs.FACTORS["recovery_ratio"]) == 2.0
    assert ME.factor_weight(bs.FACTORS["recovery_ratio"]) == 1.0
    assert athlete.targets()["sleep_hours"] == 8.5
    assert ME.targets()["sleep_hours"] == 7.5


def test_unmeasured_factor_is_not_credited():
    obs = {k: v for k, v in TODAY.items() if k != "steps"}
    steps = by_key(bs.score_day(obs, ME))["steps"]
    assert steps.measured is False
    assert steps.dose is None
    assert steps.hr is None
    assert steps.hours_today == 0
    assert steps.rel_log_hazard == 0
    # The same factor, measured, earns something.
    assert by_key(bs.score_day(TODAY, ME))["steps"].hours_today > 0


def test_factor_log_hazard_is_capped():
    smoker = bs.FACTORS["smoker"]
    # ln(2.8) * 0.7 is ~0.72, over the cap.
    assert abs(smoker.log_hazard(1)) == bs.FACTOR_CAP
    assert smoker.log_hazard(0) == 0.0


def test_layer_sum_discounts_correlated_factors():
    assert bs._layer_sum([0.5, 0.5]) == pytest.approx(0.8)
    # Largest magnitude first, whatever the input order.
    assert bs._layer_sum([0.1, -0.5]) == pytest.approx(-0.5 + 0.06)
    assert bs._layer_sum([]) == 0.0


def test_remaining_life_years_decreases_with_age():
    for sex in ("M", "F"):
        years = [bs.remaining_life_years(age, sex) for age in range(20, 81)]
        assert all(a > b for a, b in zip(years, years[1:]))
    assert bs.remaining_life_years(40, "F") > bs.remaining_life_years(40, "M")
    # Lower-case and full words map onto the two tables.
    assert bs.remaining_life_years(40, "female") == bs.remaining_life_years(40, "F")


# -- levers ----------------------------------------------------------------


def test_levers_sorted_by_roi_desc_and_all_positive():
    top = len(bs.FACTORS) + 1
    levers = bs.levers(TODAY, ME, top=top)
    assert 0 < len(levers) <= top
    rois = [lv.roi_hours_per_min for lv in levers]
    assert rois == sorted(rois, reverse=True)
    assert all(lv.hours_gain > 0 for lv in levers)
    # The four bundle keys are all measured, so the walk bundle appears.
    bundle = [lv for lv in levers if lv.key == "bundle_walk"]
    assert len(bundle) == 1
    assert bundle[0].time_min == 30
    assert set(bundle[0].layers) <= {"movement", "light", "environment", "social"}


def test_levers_skip_unmeasured_and_saturated():
    # At the top of the curve the step clamps back to the current dose.
    keys = {lv.key for lv in bs.levers({"steps": 12000, "sri": 84}, ME, top=10)}
    assert "steps" not in keys
    assert "sri" in keys
    # An absent key never becomes a lever, and nothing bundles without data.
    assert bs.levers({}, ME, top=10) == []
    assert {lv.key for lv in bs.levers({"sri": 84}, ME, top=10)} == {"sri"}


def test_zero_time_levers_have_roi_equal_to_gain():
    """levers() divides by max(time, 1), so a zero-time factor's ROI is its gain."""

    purpose = [lv for lv in bs.levers({"purpose": 4}, ME, top=10) if lv.key == "purpose"]
    assert len(purpose) == 1
    assert purpose[0].time_min == 0
    assert purpose[0].roi_hours_per_min == purpose[0].hours_gain


# -- forecast --------------------------------------------------------------


def test_forecast_late_caffeine_driver():
    late = bs.forecast_tonight({"last_caffeine_hh": 16}, 7.5, ME)
    assert len(late.drivers) == 1
    assert "caffeine" in late.drivers[0]
    assert late.sleep_hours < 7.5
    early = bs.forecast_tonight({"last_caffeine_hh": 8}, 7.5, ME)
    assert early.drivers == []
    assert early.sleep_hours == 7.5


def test_forecast_cyp1a2_slow_widens_cutoff():
    fast = bs.forecast_tonight({"last_caffeine_hh": 12}, 7.5, ME)
    assert fast.drivers == []
    slow = bs.forecast_tonight(
        {"last_caffeine_hh": 12}, 7.5, bs.Profile(age=20, cyp1a2_slow=True, bedtime_hh=23.0))
    assert len(slow.drivers) == 1
    assert "12 h cutoff" in slow.drivers[0]


def test_forecast_bedtime_after_midnight_normalised():
    """A 00:45 bedtime is 24.75 on the engine's clock, so 16:30 is 8.25 h before it."""

    fc = bs.forecast_tonight({"last_caffeine_hh": 16.5}, 7.5,
                             bs.Profile(age=20, bedtime_hh=24.75))
    assert len(fc.drivers) == 1
    assert fc.sleep_hours < 7.5


# -- ledger ----------------------------------------------------------------


def test_weekly_ledger_projection_and_status():
    lines = {line.key: line for line in bs.weekly_ledger(WEEK, ME)}
    nature = lines["nature_min_wk"]
    assert nature.accrued == 35
    assert nature.projected == 81.7
    assert nature.status == "behind"
    assert nature.deficit == 38.3
    assert nature.days_elapsed == 3
    # Daily keys are means over the days that have a value.
    days = [dict(WEEK[0]), {**WEEK[1], "steps": None}, dict(WEEK[2])]
    steps = {line.key: line for line in bs.weekly_ledger(days, ME)}["steps"]
    assert steps.accrued == pytest.approx((9100 + 7900) / 2)
    assert steps.projected == steps.accrued
    assert steps.status == "on_track"
    assert steps.deficit == 0.0


# -- attribution -----------------------------------------------------------


def test_attribute_under_14_days_returns_prior():
    eff = bs.attribute(np.arange(7.0), np.ones(7), prior_beta=-1.0, prior_se=0.3,
                       exposure_name="x", outcome_name="y")
    assert eff.n == 7
    assert math.isnan(eff.beta)
    assert all(math.isnan(v) for v in eff.ci)
    assert eff.blended_beta == -1.0
    assert "fewer than 14" in eff.note


def test_attribute_recovers_synthetic_slope():
    eff = synthetic_effect()
    assert eff.n == 30
    assert eff.ci[0] < 0.004 < eff.ci[1]
    assert eff.note == "personal estimate"
    # The blend sits between the prior and the personal estimate.
    lo, hi = sorted((0.002, eff.beta))
    assert lo <= eff.blended_beta <= hi


# -- payload and insights --------------------------------------------------


def full_payload(effects: list[bs.Effect] | None = None) -> tuple[dict, list[dict]]:
    day = bs.score_day(TODAY, ME)
    ledger = bs.weekly_ledger(WEEK, ME)
    fc = bs.forecast_tonight(TODAY, 7.5, ME)
    lv = bs.levers(TODAY, ME)
    tips = bs.insights(day, ledger, fc, lv, effects)
    return bs.to_payload(day, ledger, fc, lv, tips), tips


def test_to_payload_round_trips_through_json():
    payload, _ = full_payload()
    json.dumps(payload, allow_nan=False)
    assert set(payload["layers"]) == set(bs.LAYER_LABELS.values())
    assert isinstance(payload["overall"], int)
    assert 0 <= payload["overall"] <= 100
    assert len(payload["factors"]) == len(bs.FACTORS)
    assert {"key", "layer", "label", "dose", "hr", "hours", "grade", "measured", "source"} <= set(
        payload["factors"][0])
    assert payload["hours_ci"][0] <= payload["hours_today"] <= payload["hours_ci"][1]


def test_insight_kinds_are_known():
    known = {"tonight", "today", "week", "lever", "you"}
    _, tips = full_payload()
    assert tips
    assert {t["kind"] for t in tips} <= known
    assert all(t["source"] for t in tips)
    assert "you" not in {t["kind"] for t in tips}
    _, with_effect = full_payload([synthetic_effect()])
    assert {t["kind"] for t in with_effect} <= known
    assert "you" in {t["kind"] for t in with_effect}
    # A sub-14-day effect stays silent by the engine's own gate.
    prior_only = bs.attribute(np.arange(7.0), np.ones(7), prior_beta=-1.0, prior_se=0.3)
    _, silent = full_payload([prior_only])
    assert "you" not in {t["kind"] for t in silent}
