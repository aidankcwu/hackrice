"""The brian_score adapter over the seeded 7-day dataset (``scoring/healthspan.py``).

Every observation the adapter hands the engine must trace to an episode or a
seeded row with a provenance label, and nothing may be credited that was not
measured. The engine's strings carry non-ASCII arrows, so nothing here prints
them and every assertion is on an ASCII substring or a number.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
import pytest

from pipeline.config import Settings
from pipeline.db import Database
from pipeline.models import Episode
from pipeline.scoring import healthspan as hs
from pipeline.scoring.healthspan import healthspan_for_day, profile_from_settings
from pipeline.scoring.scorer import Scorer
from pipeline.seed.fixtures import LATE_CAFFEINE_INDEXES, days_ending
from pipeline.seed.generate import seed_database

#: A Sunday, so the late-caffeine days land on Tue / Thu / Sat.
END_DAY = "2026-09-13"
DAYS = days_ending(END_DAY)
LATE_DAYS = [DAYS[i] for i in LATE_CAFFEINE_INDEXES]
NORMAL_DAYS = [d for i, d in enumerate(DAYS) if i not in LATE_CAFFEINE_INDEXES]

PROVENANCE_LABELS = {"live", "seeded", "derived", "missing"}


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def seeded(db):
    seed_database(db, END_DAY)
    return db


@pytest.fixture
def settings():
    return Settings(_env_file=None)  # type: ignore[call-arg]


def ts(day: str, hour: float) -> float:
    """Local-time epoch seconds for ``hour`` hours after midnight on ``day``."""

    midnight = datetime.combine(date.fromisoformat(day), time.min)
    return (midnight + timedelta(hours=hour)).timestamp()


def add(db: Database, eid: str, kind: str, day: str, hour: float, minutes: float,
        dominant: dict[str, Any] | None = None, *, open: bool = False) -> None:
    start = ts(day, hour)
    db.upsert_episode(Episode(
        id=eid, kind=kind, start_t=start,  # type: ignore[arg-type]
        end_t=None if open else start + minutes * 60.0, duration_s=minutes * 60.0,
        dominant=dominant or {}, open=open,
    ))


def delete_seeded(db: Database, day: str, *metrics: str) -> None:
    marks = ",".join("?" * len(metrics))
    db.conn.execute(f"DELETE FROM seeded WHERE day = ? AND metric IN ({marks})", (day, *metrics))


def by_key(body: dict) -> dict[str, dict]:
    return {f["key"]: f for f in body["factors"]}


def ledger(body: dict) -> dict[str, dict]:
    return {line["key"]: line for line in body["ledger"]}


def walk(x: Any):
    """Every leaf of a payload."""

    if isinstance(x, dict):
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from walk(v)
    else:
        yield x


# -- payload ---------------------------------------------------------------


def test_payload_shape_and_json_roundtrip(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert {
        "day", "as_of_hh", "engine", "overall", "layers", "years_delta", "years_ci",
        "hours_today", "hours_ci", "measured", "factors", "ledger", "forecast", "levers",
        "levers_free", "insights", "pins", "observations", "provenance", "effects", "profile",
        "baseline_sleep_h", "window", "conventions",
    } <= body.keys()
    json.dumps(body, allow_nan=False)
    assert body["day"] == END_DAY
    assert body["engine"] == "brian_score"
    assert body["as_of_hh"] is None
    assert 0 <= body["overall"] <= 100
    assert len(body["layers"]) == 7
    # 18 factors + 3 leading indicators + bedtime_hh + baseline_sleep_h.
    assert len(body["provenance"]) == 23
    assert {p["source"] for p in body["provenance"].values()} <= PROVENANCE_LABELS
    assert len(body["factors"]) == 18
    for row in body["factors"]:
        assert {"provenance", "basis", "detail"} <= row.keys()
        assert row["provenance"] in PROVENANCE_LABELS
        assert row["provenance"] == body["provenance"][row["key"]]["source"]
    assert body["measured"] == {"count": sum(1 for f in body["factors"] if f["measured"]),
                                "total": 18}
    assert body["window"]["days_elapsed"] == 7
    assert body["window"]["uncovered_days"] == [END_DAY]
    assert len(body["conventions"]) == 5


# -- forecast --------------------------------------------------------------


def test_late_caffeine_days_forecast_shorter_sleep_and_tonight_driver(seeded, settings):
    for day in LATE_DAYS:
        body = healthspan_for_day(seeded, settings, day)
        drivers = body["forecast"]["drivers"]
        assert drivers
        assert "caffeine" in drivers[0]
        assert body["forecast"]["sleep_hours"] < body["baseline_sleep_h"]
        assert body["provenance"]["last_caffeine_hh"]["source"] == "live"
        assert body["observations"]["last_caffeine_hh"] == pytest.approx(16.5)
        assert "tonight" in {t["kind"] for t in body["insights"]}
    # The bedtime driver needs three prior nights with a bed_time row: the
    # first late day only has one, the others have three and five.
    first = healthspan_for_day(seeded, settings, LATE_DAYS[0])
    assert first["provenance"]["planned_bed_shift_min"]["source"] == "missing"
    assert not any("bedtime" in d for d in first["forecast"]["drivers"])
    for day in LATE_DAYS[1:]:
        body = healthspan_for_day(seeded, settings, day)
        assert body["observations"]["planned_bed_shift_min"] == pytest.approx(105.0)
        assert any("bedtime +105" in d for d in body["forecast"]["drivers"])
    # Late Saturday: exactly the coffee and the bedtime (no journal alcohol).
    saturday = healthspan_for_day(seeded, settings, LATE_DAYS[2])
    assert len(saturday["forecast"]["drivers"]) == 2
    assert "bedtime +105" in saturday["forecast"]["drivers"][1]
    assert saturday["profile"]["bedtime_hh"] == pytest.approx(24.75)


def test_normal_days_with_morning_coffee_have_no_drivers(seeded, settings):
    for day in NORMAL_DAYS[:-1]:
        body = healthspan_for_day(seeded, settings, day)
        assert body["forecast"]["drivers"] == []
        # The engine rounds the forecast to 2 dp; the baseline is the raw mean.
        assert body["forecast"]["sleep_hours"] == pytest.approx(body["baseline_sleep_h"], abs=0.006)
        assert body["observations"]["last_caffeine_hh"] == pytest.approx(8.25)
        # A mean of the alternating 23:00 / 00:45 history would fire a phantom
        # -52 min driver; the lower median keeps the habit at 23:00.
        assert body["observations"]["planned_bed_shift_min"] == 0.0


# -- provenance ------------------------------------------------------------


def test_today_has_no_episodes_so_live_daily_factors_are_missing(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    prov = body["provenance"]
    for key in ("social_index", "med_adherence", "last_caffeine_hh", "night_screen_min"):
        assert prov[key]["source"] == "missing", key
        assert key not in body["observations"]
    assert prov["nature_min_wk"]["source"] == "live"
    assert prov["sauna_wk"]["source"] == "live"
    assert prov["resistance_min_wk"]["source"] == "derived"
    assert prov["alcohol_drinks"]["source"] == "seeded"
    assert body["observations"]["alcohol_drinks"] == 0.0
    factors = by_key(body)
    assert factors["social_index"]["measured"] is False
    assert factors["social_index"]["dose"] is None
    assert factors["nature_min_wk"]["measured"] is True


def test_provenance_labels(seeded, settings):
    body = healthspan_for_day(seeded, settings, DAYS[2])
    prov = body["provenance"]
    for key in ("steps", "sleep_hours", "gait_speed", "smoker"):
        assert prov[key]["source"] == "seeded", key
    for key in ("fitness_pct", "night_light_lux", "purpose", "social_index", "resistance_min_wk"):
        assert prov[key]["source"] == "derived", key
    for key in ("nature_min_wk", "med_adherence", "sauna_wk"):
        assert prov[key]["source"] == "live", key
    assert prov["steps"]["basis"] == "phone"
    assert prov["sleep_hours"]["basis"] == "whoop"
    assert prov["fitness_pct"]["basis"] == "apple_watch"
    assert prov["social_index"]["basis"] == "glasses"
    assert prov["purpose"]["basis"] == "user"
    # DAYS[3] has a journal '1' and no sighting.
    alcohol = healthspan_for_day(seeded, settings, DAYS[3])["provenance"]["alcohol_drinks"]
    assert (alcohol["source"], alcohol["basis"]) == ("seeded", "whoop")
    assert f"whoop journal answer filed on {DAYS[3]}" in alcohol["detail"]
    assert "at least one drink" in alcohol["detail"]


def test_missing_seeded_rows_leave_factor_unmeasured_not_good(seeded, settings):
    before = healthspan_for_day(seeded, settings, DAYS[2])
    delete_seeded(seeded, DAYS[2], "steps", "sleep_hours")
    body = healthspan_for_day(seeded, settings, DAYS[2])
    for key in ("steps", "sleep_hours"):
        row = by_key(body)[key]
        assert row["measured"] is False
        assert row["dose"] is None
        assert row["hr"] is None
        assert row["hours"] == 0
        assert row["provenance"] == "missing"
        assert key not in body["observations"]
    assert all(row["hours"] == 0 for row in body["factors"] if not row["measured"])
    assert body["overall"] != before["overall"]
    assert body["measured"]["count"] == before["measured"]["count"] - 2


def test_day_light_is_phone_row_when_present_else_derived_fallback(seeded, settings):
    body = healthspan_for_day(seeded, settings, DAYS[0])
    light = by_key(body)["day_light_min"]
    assert light["dose"] == 35.0
    assert light["provenance"] == "seeded"
    assert light["basis"] == "phone"

    # Without the phone row the only evidence is the 18:30 park block, which
    # lies outside the 08:00-18:00 daylight band: a measured zero, not a guess.
    delete_seeded(seeded, DAYS[0], "daytime_light_minutes")
    body = healthspan_for_day(seeded, settings, DAYS[0])
    light = by_key(body)["day_light_min"]
    assert light["dose"] == 0.0
    assert light["measured"] is True
    assert light["provenance"] == "derived"
    assert light["basis"] == "glasses"
    assert "lower bound" in light["detail"]

    add(seeded, "e_park_am", "outdoor_block", DAYS[0], 10.0, 30.0, {"scene": "park"})
    body = healthspan_for_day(seeded, settings, DAYS[0])
    light = by_key(body)["day_light_min"]
    assert light["dose"] == 30.0
    assert light["provenance"] == "derived"


# -- live aggregates -------------------------------------------------------


def test_nature_counts_nature_scenes_and_untagged_blocks(seeded, settings):
    def nature() -> tuple[float, str]:
        body = healthspan_for_day(seeded, settings, END_DAY)
        return body["observations"]["nature_min_wk"], body["provenance"]["nature_min_wk"]["detail"]

    add(seeded, "e_street", "outdoor_block", END_DAY, 9.0, 10.0, {"scene": "street"})
    minutes, detail = nature()
    assert minutes == pytest.approx(84.0)
    assert "no scene tag" not in detail

    add(seeded, "e_campus", "outdoor_block", END_DAY, 10.0, 10.0, {"scene": "campus_outdoor"})
    minutes, detail = nature()
    assert minutes == pytest.approx(94.0)
    assert "campus_outdoor" in detail

    add(seeded, "e_green", "outdoor_block", END_DAY, 11.0, 10.0, {"activity": "walking"})
    minutes, detail = nature()
    assert minutes == pytest.approx(104.0)
    assert "no scene tag" in detail


def test_nature_week_matches_scorer_window(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["observations"]["nature_min_wk"] == pytest.approx(84.0)
    scorer_rows = {s.metric: s for s in Scorer(seeded).score_week("2026-W37", DAYS)}
    assert scorer_rows["nature_minutes_weekly"].value == pytest.approx(
        body["observations"]["nature_min_wk"])
    assert body["window"]["factor_days"] == DAYS
    assert "park" in body["provenance"]["nature_min_wk"]["detail"]
    assert "6 covered days" in body["provenance"]["nature_min_wk"]["detail"]


def test_sauna_over_19_min_and_not_cold_plunge(seeded, settings):
    def sauna() -> dict:
        return healthspan_for_day(seeded, settings, END_DAY)

    add(seeded, "e_sauna_short", "sauna_session", END_DAY, 7.0, 15.0, {"scene": "sauna"})
    body = sauna()
    assert body["observations"]["sauna_wk"] == 0
    assert "1 shorter session(s) ignored" in body["provenance"]["sauna_wk"]["detail"]

    add(seeded, "e_sauna_long", "sauna_session", END_DAY, 7.5, 25.0, {"scene": "sauna"})
    body = sauna()
    assert body["observations"]["sauna_wk"] == 1

    add(seeded, "e_cold", "sauna_session", END_DAY, 8.0, 25.0, {"scene": "cold_plunge"})
    body = sauna()
    assert body["observations"]["sauna_wk"] == 1
    assert "1 cold plunge(s) tracked, not scored" in body["provenance"]["sauna_wk"]["detail"]

    # Two qualifying sessions on one day count twice in the ledger row.
    add(seeded, "e_sauna_long_2", "sauna_session", END_DAY, 19.0, 30.0, {"scene": "sauna"})
    body = sauna()
    assert body["observations"]["sauna_wk"] == 2
    assert ledger(body)["sauna_wk"]["accrued"] == 2.0


def test_night_screen_minutes_are_overlap_with_tonight(seeded, settings):
    def screens() -> dict:
        return healthspan_for_day(seeded, settings, END_DAY)

    add(seeded, "e_screen_eve", "screen_block", END_DAY, 21.5, 120.0, {"scene": "home"})
    body = screens()
    assert body["observations"]["night_screen_min"] == pytest.approx(90.0)
    assert body["provenance"]["night_screen_min"]["source"] == "live"
    assert any("90 min of screens" in d for d in body["forecast"]["drivers"])

    # 00:30-01:00 filed on D belongs to last night, not tonight.
    add(seeded, "e_screen_small_hours", "screen_block", END_DAY, 0.5, 30.0, {"scene": "home"})
    assert screens()["observations"]["night_screen_min"] == pytest.approx(90.0)

    # An open block: duration_s is authoritative, not end_t.
    add(seeded, "e_screen_open", "screen_block", END_DAY, 22.0, 60.0, {"scene": "home"},
        open=True)
    assert screens()["observations"]["night_screen_min"] == pytest.approx(150.0)


def test_open_episode_uses_duration_s(seeded, settings):
    add(seeded, "e_conv_open", "conversation", END_DAY, 10.0, 10.0, {"scene": "office"},
        open=True)
    body = healthspan_for_day(seeded, settings, END_DAY)
    # 10 min of 60 (weight .6) plus 1 encounter of 5 (weight .4).
    assert body["observations"]["social_index"] == pytest.approx(18.0)
    assert by_key(body)["social_index"]["measured"] is True
    assert body["provenance"]["social_index"]["source"] == "derived"


def test_alcohol_prefers_live_sightings_over_journal(seeded, settings):
    def alcohol() -> tuple[float, str]:
        body = healthspan_for_day(seeded, settings, DAYS[3])
        return body["observations"]["alcohol_drinks"], body["provenance"]["alcohol_drinks"]["source"]

    assert alcohol() == (1.0, "seeded")

    add(seeded, "e_alc_1", "alcohol_sighting", DAYS[3], 20.0, 1.0, {"scene": "home"})
    add(seeded, "e_alc_2", "alcohol_sighting", DAYS[3], 22.0, 1.0, {"scene": "home"})
    assert alcohol() == (2.0, "live")

    seeded.conn.execute("DELETE FROM episodes WHERE id LIKE 'e_alc_%'")
    for i in range(5):
        add(seeded, f"e_alc_run_{i}", "alcohol_sighting", DAYS[3], 20.0 + i * 0.1, 1.0,
            {"scene": "home"})
    value, source = alcohol()
    assert (value, source) == (1.0, "live")
    body = healthspan_for_day(seeded, settings, DAYS[3])
    assert "5 sighting(s) in 1 occasion(s)" in body["provenance"]["alcohol_drinks"]["detail"]


def test_med_adherence_ignores_untyped_meals(seeded, settings):
    add(seeded, "e_meal_veg", "meal", END_DAY, 12.5, 30.0,
        {"scene": "restaurant", "food_type": "vegetables"})
    add(seeded, "e_meal_untyped", "meal", END_DAY, 19.0, 30.0, {"scene": "home"})
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["observations"]["med_adherence"] == pytest.approx(1.0)
    assert body["provenance"]["med_adherence"]["source"] == "live"
    assert "1/1 typed meals on-pattern (1 untyped ignored" in body["provenance"]["med_adherence"]["detail"]


# -- ledger and attribution ------------------------------------------------


def test_ledger_is_iso_week_to_date(seeded, settings):
    sunday = healthspan_for_day(seeded, settings, END_DAY)
    assert sunday["window"]["ledger_days"] == DAYS
    assert sunday["window"]["days_elapsed"] == 7
    assert sunday["ledger"]
    for line in sunday["ledger"]:
        assert line["days_elapsed"] == 7
        assert line["projected"] == line["accrued"]
        assert line["status"] in ("on_track", "at_risk", "behind")
    assert ledger(sunday)["nature_min_wk"]["accrued"] == pytest.approx(84.0)

    monday = healthspan_for_day(seeded, settings, DAYS[0])
    assert monday["window"]["ledger_days"] == [DAYS[0]]
    assert monday["window"]["days_elapsed"] == 1
    nature = ledger(monday)["nature_min_wk"]
    assert nature["days_elapsed"] == 1
    assert nature["accrued"] == pytest.approx(12.0)
    assert nature["projected"] == pytest.approx(12.0 * 7)
    # The hazard dose is the trailing week, of which only Monday is covered.
    assert monday["observations"]["nature_min_wk"] == pytest.approx(12.0)
    assert monday["window"]["factor_days"] == Scorer.week_days(DAYS[0])


def test_attribution_returns_prior_with_six_covered_days(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert len(body["effects"]) == 1
    effect = body["effects"][0]
    assert effect["n"] == 6
    assert effect["beta"] is None
    assert effect["ci"] == [None, None]
    assert effect["blended_beta"] == -1.0
    assert "fewer than 14" in effect["note"]
    assert "glasses" in effect["exposure"]
    assert "you" not in {t["kind"] for t in body["insights"]}


def test_attribution_exposure_uses_profile_cutoff(seeded, settings):
    """Exposure = a coffee later than bedtime - cutoff; CYP1A2 slow widens it to 12 h."""

    add(seeded, "e_noon_coffee", "caffeine_sighting", DAYS[0], 12.0, 1.5, {"scene": "office"})
    days = [d for _, d in sorted(hs._load(seeded, DAYS, []).items())]
    default = hs._history(days, profile_from_settings(settings, 23.0))
    assert default is not None
    assert list(default["exposure"]) == [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
    assert list(default["outcome"]) == [7.4, 5.8, 7.6, 6.0, 7.8, 6.2]

    slow = Settings(_env_file=None, profile_cyp1a2_slow=True)  # type: ignore[call-arg]
    exposed = hs._history(days, profile_from_settings(slow, 23.0))
    assert exposed is not None
    assert list(exposed["exposure"]) == [1.0, 1.0, 0.0, 1.0, 0.0, 1.0]
    # The payload's effect uses the same rows either way.
    assert healthspan_for_day(seeded, slow, END_DAY)["effects"][0]["n"] == 6


# -- profile, bedtime, baseline --------------------------------------------


def test_bedtime_from_seeded_row_else_default(seeded, settings):
    late = LATE_DAYS[2]
    body = healthspan_for_day(seeded, settings, late)
    assert body["profile"]["bedtime_hh"] == pytest.approx(24.75)
    assert body["profile"]["bedtime_source"] == "seeded"
    assert body["provenance"]["bedtime_hh"]["source"] == "seeded"
    assert body["provenance"]["bedtime_hh"]["basis"] == "whoop"
    assert "00:45" in body["provenance"]["bedtime_hh"]["detail"]

    delete_seeded(seeded, late, "bed_time")
    body = healthspan_for_day(seeded, settings, late)
    assert body["profile"]["bedtime_hh"] == 23.0
    assert body["profile"]["bedtime_source"] == "missing"
    assert body["provenance"]["bedtime_hh"]["source"] == "missing"
    assert "assumed 23:00" in body["provenance"]["bedtime_hh"]["detail"]


def test_baseline_sleep_excludes_the_day_itself(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["baseline_sleep_h"] == pytest.approx(6.8)  # mean of the six prior nights
    assert body["provenance"]["baseline_sleep_h"]["source"] == "derived"
    assert "today excluded" in body["provenance"]["baseline_sleep_h"]["detail"]

    second = healthspan_for_day(seeded, settings, DAYS[1])
    assert second["baseline_sleep_h"] == 7.5
    assert second["provenance"]["baseline_sleep_h"]["source"] == "missing"
    assert "fewer than 3 prior nights" in second["provenance"]["baseline_sleep_h"]["detail"]


def test_fitness_percentile_is_coarse_but_ordered():
    assert hs._fitness_pct(51, 20, "M") == pytest.approx(77.5)
    assert hs._fitness_pct(51, 20, "F") > hs._fitness_pct(51, 20, "M")
    assert hs._fitness_pct(51, 20, "female") == hs._fitness_pct(51, 20, "F")
    assert hs._fitness_pct(30, 20, "M") < 10
    for vo2 in (0.0, 10.0, 100.0, 1000.0):
        assert 3.0 <= hs._fitness_pct(vo2, 20, "M") <= 97.0
    # Monotone in VO2max, and the same VO2max ranks higher with age.
    assert hs._fitness_pct(40, 20, "M") < hs._fitness_pct(45, 20, "M")
    assert hs._fitness_pct(45, 20, "M") < hs._fitness_pct(45, 65, "M")
    assert isinstance(hs._fitness_pct(51, 20, "M"), float)


def test_profile_from_settings_and_env(seeded, monkeypatch):
    custom = Settings(_env_file=None, profile_age=45, profile_sex="f",  # type: ignore[call-arg]
                      profile_goal="athlete")
    profile = profile_from_settings(custom, 23.0)
    assert profile.age == 45
    assert profile.sex == "F"
    assert profile.goal == "athlete"
    assert profile.cyp1a2_slow is False
    assert profile.bedtime_hh == 23.0
    assert profile.targets()["sleep_hours"] == 8.5
    body = healthspan_for_day(seeded, custom, END_DAY)
    assert body["profile"] == {"age": 45, "sex": "F", "goal": "athlete", "cyp1a2_slow": False,
                               "height_m": None, "bedtime_hh": 23.0, "bedtime_source": "seeded"}
    assert "F 40s" in body["provenance"]["fitness_pct"]["detail"]

    monkeypatch.setenv("PROFILE_CYP1A2_SLOW", "1")
    monkeypatch.setenv("PROFILE_HEIGHT_M", "1.78")
    from_env = Settings(_env_file=None)  # type: ignore[call-arg]
    assert profile_from_settings(from_env, 23.0).cyp1a2_slow is True
    assert from_env.profile_height_m == 1.78
    assert healthspan_for_day(seeded, from_env, END_DAY)["profile"]["height_m"] == 1.78


# -- levers and pins -------------------------------------------------------


def test_levers_exclude_zero_time_and_state_markers(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["levers"]
    assert len(body["levers"]) <= 5
    assert all(lv["time_min"] > 0 for lv in body["levers"])
    rois = [lv["roi_hours_per_min"] for lv in body["levers"]]
    assert rois == sorted(rois, reverse=True)
    assert body["levers_free"]
    assert len(body["levers_free"]) <= 3
    assert all(lv["time_min"] <= 0 for lv in body["levers_free"])
    free_keys = {lv["key"] for lv in body["levers_free"]}
    assert not free_keys & {"gait_speed", "recovery_ratio"}
    assert not free_keys & {lv["key"] for lv in body["levers"]}
    gains = [lv["hours_gain"] for lv in body["levers_free"]]
    assert gains == sorted(gains, reverse=True)
    lever_tips = [t for t in body["insights"] if t["kind"] == "lever"]
    assert len(lever_tips) == 1
    assert lever_tips[0]["text"].startswith("Best use of your next")
    assert f"next {body['levers'][0]['time_min']:.0f} minutes" in lever_tips[0]["text"]


def test_pins_reconciled_with_profile_cutoff(seeded, settings):
    day = LATE_DAYS[0]
    body = healthspan_for_day(seeded, settings, day)
    pins = body["pins"]
    # 08:15 coffee, 11:00 conversation, 12:30 meal, 16:30 coffee, 18:30 park;
    # the 09:30 screen block is outside 22:00-05:00 and is not pinned.
    assert [p["time"] for p in pins] == ["08:15", "11:00", "12:30", "16:30", "18:30"]
    by_time = {p["time"]: p for p in pins}
    assert by_time["16:30"]["kind"] == "debit"
    assert "cutoff" in by_time["16:30"]["effect"]
    assert "bed 00:45" in by_time["16:30"]["effect"]
    assert by_time["08:15"]["kind"] == "credit"
    assert "outside your 9 h cutoff" in by_time["08:15"]["effect"]
    assert by_time["18:30"]["kind"] == "credit"
    assert "counted by the phone" in by_time["18:30"]["effect"]
    assert "nature +18 min" in by_time["18:30"]["effect"]
    assert by_time["12:30"]["seen"] == "Meal: processed"
    assert all(p["img"] is None for p in pins)

    # A 14:30 coffee is "late" on the engine's hard-coded 14:00 rule but sits
    # before this profile's 15:45 cutoff -- the adapter's verdict wins.
    add(seeded, "e_coffee_1430", "caffeine_sighting", day, 14.5, 1.5, {"scene": "office"})
    # Neither a short sauna nor a gym session earns a pin.
    add(seeded, "e_sauna_short", "sauna_session", day, 7.0, 15.0, {"scene": "sauna"})
    add(seeded, "e_gym", "gym_session", day, 17.0, 45.0, {"scene": "gym"})
    pins = healthspan_for_day(seeded, settings, day)["pins"]
    assert [p["time"] for p in pins] == ["08:15", "11:00", "12:30", "14:30", "16:30", "18:30"]
    pin = {p["time"]: p for p in pins}["14:30"]
    assert pin["kind"] == "credit"
    assert "outside your 9 h cutoff" in pin["effect"]


def test_live_episode_moves_the_numbers_today(seeded, settings):
    before = healthspan_for_day(seeded, settings, END_DAY)
    assert before["pins"] == []
    add(seeded, "e_conv", "conversation", END_DAY, 10.0, 20.0, {"scene": "office"})
    add(seeded, "e_park", "outdoor_block", END_DAY, 12.0, 10.0, {"scene": "park"})
    add(seeded, "e_coffee", "caffeine_sighting", END_DAY, 14.7, 1.5, {"scene": "office"})

    body = healthspan_for_day(seeded, settings, END_DAY, now_t=ts(END_DAY, 10.42))
    assert by_key(body)["social_index"]["measured"] is True
    assert body["provenance"]["social_index"]["source"] == "derived"
    assert body["observations"]["nature_min_wk"] == pytest.approx(94.0)
    assert body["provenance"]["night_screen_min"]["source"] == "live"
    assert body["observations"]["night_screen_min"] == 0.0
    assert body["window"]["uncovered_days"] == []
    assert body["observations"]["last_caffeine_hh"] == pytest.approx(14.7)
    # 14:42 is inside a 23:00 - 9 h = 14:00 cutoff, so the driver fires.
    assert 14.7 > body["profile"]["bedtime_hh"] - 9
    assert any("caffeine" in d for d in body["forecast"]["drivers"])
    assert [p["kind"] for p in body["pins"]] == ["credit", "credit", "debit"]
    assert [p["time"] for p in body["pins"]] == ["10:00", "12:00", "14:42"]
    assert body["as_of_hh"] == pytest.approx(10.42, abs=0.01)
    assert (body["overall"], body["hours_today"]) != (before["overall"], before["hours_today"])
    assert body["measured"]["count"] == before["measured"]["count"] + 1

    # now_t on another day leaves as_of_hh unset.
    other = healthspan_for_day(seeded, settings, END_DAY, now_t=ts(DAYS[0], 10.42))
    assert other["as_of_hh"] is None
    assert {k: v for k, v in other.items() if k != "as_of_hh"} == {
        k: v for k, v in body.items() if k != "as_of_hh"}


# -- hygiene ---------------------------------------------------------------


def test_negative_zero_and_nan_are_sanitised(seeded, settings):
    for day in (END_DAY, LATE_DAYS[0], DAYS[3]):
        body = healthspan_for_day(seeded, settings, day)
        leaves = list(walk(body))
        assert leaves
        for leaf in leaves:
            assert not isinstance(leaf, np.generic)
            assert leaf is None or isinstance(leaf, (bool, int, float, str))
            if isinstance(leaf, float):
                assert not math.isnan(leaf)
                assert not math.isinf(leaf)
                if leaf == 0.0:
                    assert math.copysign(1.0, leaf) > 0
        json.dumps(body, allow_nan=False)


def test_deterministic_and_no_db_writes(seeded, settings):
    def counts() -> tuple[int, int, int]:
        return tuple(
            seeded.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("episodes", "scores", "seeded")
        )  # type: ignore[return-value]

    before = counts()
    first = healthspan_for_day(seeded, settings, LATE_DAYS[1])
    second = healthspan_for_day(seeded, settings, LATE_DAYS[1])
    assert first == second
    assert counts() == before
    assert before[1] == 0  # nothing scored into the §8 table either


def test_bad_day_raises_value_error(seeded, settings):
    with pytest.raises(ValueError):
        healthspan_for_day(seeded, settings, "nonsense")
    with pytest.raises(ValueError):
        healthspan_for_day(seeded, settings, "2026-13-01")


def test_empty_database_scores_with_everything_missing(db, settings):
    body = healthspan_for_day(db, settings, END_DAY)
    assert body["measured"] == {"count": 0, "total": 18}
    assert all(row["provenance"] == "missing" for row in body["factors"])
    assert body["observations"] == {"planned_bed_shift_min": 0.0}
    assert body["profile"]["bedtime_source"] == "missing"
    assert body["baseline_sleep_h"] == 7.5
    assert body["forecast"]["drivers"] == []
    assert body["levers"] == [] and body["levers_free"] == []
    assert body["effects"] == []
    assert body["ledger"]  # weekly accruals of zero, daily keys absent
    assert 0 <= body["overall"] <= 100
    json.dumps(body, allow_nan=False)
