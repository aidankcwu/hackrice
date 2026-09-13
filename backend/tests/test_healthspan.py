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
from typing import Any, get_args

import numpy as np
import pytest

from pipeline.config import Settings
from pipeline.db import Database
from pipeline.models import (
    FOOD_TYPES,
    HEALTHY_FOOD_TYPES,
    OUTDOOR_SCENES,
    Episode,
    EpisodeKind,
    PendingQuestion,
    SCENES,
    SeededRow,
)
from pipeline.scoring import brian_score as bs
from pipeline.scoring import healthspan as hs
from pipeline.scoring.healthspan import healthspan_for_day, profile_from_settings
from pipeline.scoring.scorer import Scorer
from pipeline.wearables import air
from pipeline.seed.fixtures import (
    LATE_CAFFEINE_INDEXES,
    SEEDED_SOURCES,
    SEEDED_UNITS,
    days_ending,
)
from pipeline.seed.generate import seed_database

#: A Sunday, so the late-caffeine days land on Tue / Thu / Sat.
END_DAY = "2026-09-13"
DAYS = days_ending(END_DAY)
LATE_DAYS = [DAYS[i] for i in LATE_CAFFEINE_INDEXES]
NORMAL_DAYS = [d for i, d in enumerate(DAYS) if i not in LATE_CAFFEINE_INDEXES]

PROVENANCE_LABELS = {"live", "seeded", "derived", "missing"}
#: Seeded metrics written at runtime rather than by the §7 seed generator.
RUNTIME_METRICS = {"pm25", "pvt_rt_z", "pvt_lapses", "pvt_rt_ms",
                   "pvt_check_energy", "pvt_check_mood", "pvt_check_clarity"}


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


def report(db: Database, qid: str, episode_id: str, parsed: dict, created_t: float) -> None:
    db.insert_question(PendingQuestion(
        id=qid, created_t=created_t, expires_t=None, episode_id=episode_id,
        question="Yours?", status="answered", parsed=parsed))


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
        "levers_free", "levers_personalized", "insights", "pins", "experience", "currencies",
        "week_table", "annotations", "narrator_prompts", "driver_rules", "observations",
        "provenance", "effects", "profile", "baseline_sleep_h", "window", "conventions",
    } <= body.keys()
    json.dumps(body, allow_nan=False)
    assert body["day"] == END_DAY
    assert body["engine"] == "brian_score"
    assert body["as_of_hh"] is None
    assert 0 <= body["overall"] <= 100
    assert len(body["layers"]) == 8
    # 20 factors + 3 leading indicators + bedtime_hh + baseline_sleep_h.
    assert len(body["provenance"]) == 25
    assert {p["source"] for p in body["provenance"].values()} <= PROVENANCE_LABELS
    assert len(body["factors"]) == 20
    for row in body["factors"]:
        assert {"provenance", "basis", "detail"} <= row.keys()
        assert row["provenance"] in PROVENANCE_LABELS
        assert row["provenance"] == body["provenance"][row["key"]]["source"]
    assert body["measured"] == {"count": sum(1 for f in body["factors"] if f["measured"]),
                                "total": 20}
    assert body["window"]["days_elapsed"] == 7
    assert body["window"]["uncovered_days"] == [END_DAY]
    assert len(body["conventions"]) == 8
    assert set(body["driver_rules"]) == {"caffeine_late", "alcohol", "night_screen",
                                         "late_bed", "no_daylight", "isolated"}


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


def test_nature_scenes_are_real_outdoor_scenes():
    """Every NATURE_SCENES member must still be an outdoor ``scene`` on the §9 menu.

    Person A owns the menu (``src/longevity/ai_fields.py``, mirrored into
    ``models.py``); a rename or removal there would otherwise leave this module
    silently matching a scene the VLM can no longer emit -- ``Episode.dominant``
    is ``dict[str, Any]``, so nothing validates the tag at write time.
    """

    assert hs.NATURE_SCENES <= OUTDOOR_SCENES
    assert hs.NATURE_SCENES <= set(SCENES)
    # The built environment is outdoors without being green or blue.
    assert not hs.NATURE_SCENES & {
        "street", "parking_lot", "sports_venue", "construction_site", "outdoor_other"}


def test_episode_kinds_the_adapter_switches_on_still_exist():
    """The kinds ``healthspan`` aggregates by name are still builder outputs."""

    assert {
        "outdoor_block", "gym_session", "sauna_session", "meal", "conversation",
        "screen_block", "caffeine_sighting", "alcohol_sighting",
    } <= set(get_args(EpisodeKind))


def test_healthy_food_types_and_seeded_metrics_still_exist():
    """The ``food_type`` family and every seeded metric the adapter reads as-is."""

    assert HEALTHY_FOOD_TYPES <= set(FOOD_TYPES)
    seeded_metrics = {metric for metric, _ in hs._SEEDED_AS_IS.values()} | {
        "vo2_max", "daytime_light_minutes", "evening_light_ok", "purpose_score",
        "journal_alcohol", "bed_time", "strain", "recovery_score",
    }
    # pm25 and the pvt_* rows are written at runtime (wearables/air.py, POST
    # /api/pvt), never by the §7 seed generator, so they have no fixture entry.
    assert seeded_metrics - RUNTIME_METRICS <= SEEDED_UNITS.keys()
    assert seeded_metrics - RUNTIME_METRICS <= SEEDED_SOURCES.keys()
    assert RUNTIME_METRICS.isdisjoint(SEEDED_UNITS.keys())


def test_nature_counts_nature_scenes_and_untagged_blocks(seeded, settings):
    def nature() -> tuple[float, str]:
        body = healthspan_for_day(seeded, settings, END_DAY)
        return body["observations"]["nature_min_wk"], body["provenance"]["nature_min_wk"]["detail"]

    add(seeded, "e_street", "outdoor_block", END_DAY, 9.0, 10.0, {"scene": "street"})
    minutes, detail = nature()
    assert minutes == pytest.approx(84.0)
    assert "no scene tag" not in detail

    add(seeded, "e_campus", "outdoor_block", END_DAY, 10.0, 10.0, {"scene": "campus"})
    minutes, detail = nature()
    assert minutes == pytest.approx(94.0)
    assert "campus" in detail

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


def test_wearer_reports_override_healthspan_alcohol_and_food(seeded, settings):
    add(seeded, "e_report_alc", "alcohol_sighting", END_DAY, 20.0, 1.0)
    add(seeded, "e_report_meal", "meal", END_DAY, 19.0, 30.0,
        {"food_type": "processed"})
    report(seeded, "q_alc", "e_report_alc", {"confirmed": True, "count": 2.0},
           ts(END_DAY, 20.1))
    report(seeded, "q_meal", "e_report_meal",
           {"confirmed": True, "food_type": "fruit"}, ts(END_DAY, 20.2))

    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["observations"]["alcohol_drinks"] == 2.0
    assert "wearer reported: 2 drinks" in body["provenance"]["alcohol_drinks"]["detail"]
    assert body["observations"]["med_adherence"] == 1.0
    assert "wearer reported: fruit" in body["provenance"]["med_adherence"]["detail"]

    report(seeded, "q_alc_no", "e_report_alc", {"confirmed": False}, ts(END_DAY, 20.3))
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["observations"]["alcohol_drinks"] == 0.0
    assert "wearer reported: not mine" in body["provenance"]["alcohol_drinks"]["detail"]


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


# -- Mind (rt_z) and Air (pm25) --------------------------------------------


def write(db: Database, day: str, metric: str, value: float,
          unit: str = "", source: str = "pvt") -> None:
    db.insert_seeded_rows([SeededRow(day=day, metric=metric, value=value,
                                     unit=unit, source=source)])


def test_rt_z_is_unmeasured_without_a_pvt(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["provenance"]["rt_z"] == {
        "source": "missing", "basis": "pvt", "detail": "no PVT today"}
    row = by_key(body)["rt_z"]
    assert (row["measured"], row["dose"], row["hr"], row["hours"]) == (False, None, None, 0)
    assert row["layer"] == "Cognition"
    assert "rt_z" not in body["observations"]


def test_rt_z_from_the_days_pvt_rows(seeded, settings):
    write(seeded, END_DAY, "pvt_rt_z", 1.2, "z")
    write(seeded, END_DAY, "pvt_lapses", 3, "count")
    body = healthspan_for_day(seeded, settings, END_DAY)
    prov = body["provenance"]["rt_z"]
    assert (prov["source"], prov["basis"]) == ("derived", "pvt")
    assert "+1.20 SD vs your own baseline" in prov["detail"]
    assert "3 lapse(s)" in prov["detail"]
    row = by_key(body)["rt_z"]
    assert row["measured"] is True
    assert row["dose"] == pytest.approx(1.2)
    assert row["hours"] < 0                      # slower than baseline costs hours
    assert body["observations"]["rt_z"] == pytest.approx(1.2)
    # A faster day earns, and a baseline day is neither.
    write(seeded, END_DAY, "pvt_rt_z", -1.0, "z")
    assert by_key(healthspan_for_day(seeded, settings, END_DAY))["rt_z"]["hours"] > 0
    write(seeded, END_DAY, "pvt_rt_z", 0.0, "z")
    assert by_key(healthspan_for_day(seeded, settings, END_DAY))["rt_z"]["hours"] == 0


def test_rt_z_is_a_state_marker_not_a_lever(seeded, settings):
    assert "rt_z" in hs.STATE_MARKERS
    write(seeded, END_DAY, "pvt_rt_z", 1.5, "z")
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert "rt_z" not in {lv["key"] for lv in body["levers"] + body["levers_free"]}


def test_pm25_is_the_openaq_row_else_unmeasured(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    prov = body["provenance"]["pm25"]
    assert (prov["source"], prov["basis"]) == ("missing", "openaq")
    assert "AIR_LAT/AIR_LON unset" in prov["detail"]
    assert by_key(body)["pm25"]["measured"] is False

    write(seeded, END_DAY, "pm25", 31.0, air.PM25_UNIT, air.PM25_SOURCE)
    body = healthspan_for_day(seeded, settings, END_DAY)
    prov = body["provenance"]["pm25"]
    assert (prov["source"], prov["basis"]) == ("seeded", "openaq")
    assert "31 µg/m³" in prov["detail"]
    row = by_key(body)["pm25"]
    assert row["dose"] == pytest.approx(31.0)
    assert row["layer"] == "Environment"
    assert row["hours"] < 0                      # above the 9 µg/m³ reference
    # Clean air earns.
    write(seeded, END_DAY, "pm25", 4.0, air.PM25_UNIT, air.PM25_SOURCE)
    assert by_key(healthspan_for_day(seeded, settings, END_DAY))["pm25"]["hours"] > 0


# -- experience and the two currencies -------------------------------------


def test_experience_uses_recovery_pvt_and_the_three_tap_check(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    experience = body["experience"]
    # The seeded Sunday has a recovery_score but no PVT and no self-check.
    assert experience["components"]["recovery"] == pytest.approx(84.0)
    assert experience["components"]["pvt"] is None
    assert experience["components"]["check"] is None
    assert 0.4 <= experience["utility"] <= 1.0
    assert experience["fully_lived_hours"] == pytest.approx(24 * experience["utility"], abs=0.05)

    for metric, value in (("pvt_rt_z", 0.5), ("pvt_lapses", 1), ("pvt_rt_ms", 298.0),
                          ("pvt_check_energy", 2), ("pvt_check_mood", 2),
                          ("pvt_check_clarity", 2)):
        write(seeded, END_DAY, metric, value)
    worse = healthspan_for_day(seeded, settings, END_DAY)["experience"]
    assert worse["components"]["pvt"] == {"rt_z": 0.5, "lapses": 1.0, "rt_ms_median": 298.0}
    assert worse["components"]["check"] == {"energy": 2.0, "mood": 2.0, "clarity": 2.0}
    assert worse["utility"] < experience["utility"]


def test_experience_is_none_when_nothing_about_the_day_was_measured(db, settings):
    """The engine would return 1.0 off its pain/illness term alone; 1.0 is a lie."""

    body = healthspan_for_day(db, settings, END_DAY)
    assert body["experience"] is None
    assert body["currencies"] is None


def test_currencies_weight_years_by_utility(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    currencies, experience = body["currencies"], body["experience"]
    assert currencies["utility_today"] == experience["utility"]
    assert currencies["fully_lived_hours_today"] == experience["fully_lived_hours"]
    assert currencies["future_healthy_years"] == pytest.approx(
        body["years_delta"] * experience["utility"], abs=0.01)
    lo, hi = currencies["future_healthy_years_ci"]
    assert lo <= currencies["future_healthy_years"] <= hi
    # Six prior days carry a utility -- one short of the seven a 30-day mean needs.
    assert currencies["utility_days"] == 6
    assert currencies["utility_mean_30d"] is None


def test_utility_mean_appears_at_seven_prior_days(seeded, settings):
    """Below the threshold the mean is None, not a two-day number wearing a 30-day label."""

    assert hs.MIN_UTILITY_DAYS == 7
    seventh = Scorer.week_days(END_DAY, 8)[0]
    write(seeded, seventh, "recovery_score", 50.0, "%", "whoop")
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["currencies"]["utility_days"] == 7
    mean = body["currencies"]["utility_mean_30d"]
    assert mean is not None
    assert 0.4 <= mean <= 1.0
    assert body["currencies"]["future_healthy_years"] == pytest.approx(
        body["years_delta"] * mean, abs=0.01)


# -- the narrator ----------------------------------------------------------


def drivers(body: dict) -> dict[str, dict[str, bool]]:
    return {row["day"]: row["drivers"] for row in body["week_table"]}


def test_week_table_is_one_row_per_trailing_day_with_its_own_hours(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    table = body["week_table"]
    assert [row["day"] for row in table] == DAYS
    assert table[-1]["hours"] == pytest.approx(body["hours_today"], abs=0.01)
    for row in table:
        assert row.keys() == {"day", "hours", "sleep", "rec", "sri", "drivers"}
        assert set(row["drivers"]) == set(hs.DRIVER_RULES)
        assert all(isinstance(v, bool) for v in row["drivers"].values())


def test_driver_flags_are_only_true_on_evidence(seeded, settings):
    flags = drivers(healthspan_for_day(seeded, settings, END_DAY))
    # The three late-caffeine days, and only those.
    assert [d for d, f in flags.items() if f["caffeine_late"]] == LATE_DAYS
    # DAYS[3] has the journal alcohol row.
    assert [d for d, f in flags.items() if f["alcohol"]] == [DAYS[3]]
    # Nothing on the seeded week screens late, is isolated, or misses daylight.
    for key in ("night_screen", "no_daylight", "isolated"):
        assert not any(f[key] for f in flags.values()), key


def test_an_unmeasured_day_is_never_a_clean_day(seeded, settings):
    """END_DAY has no episodes, so it is absent from both arms, not in the clean one."""

    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["provenance"]["social_index"]["source"] == "missing"
    assert body["provenance"]["night_screen_min"]["source"] == "missing"
    row = body["week_table"][-1]
    assert row["day"] == END_DAY
    assert row["drivers"]["isolated"] is False
    assert row["drivers"]["night_screen"] is False


def test_night_screen_driver_fires_at_its_documented_threshold(seeded, settings):
    add(seeded, "e_screen", "screen_block", END_DAY, 22.0, hs.NIGHT_SCREEN_MIN_MIN,
        {"scene": "home"})
    assert drivers(healthspan_for_day(seeded, settings, END_DAY))[END_DAY]["night_screen"]

    # One minute short of the threshold is not a night-screen day.
    seeded.conn.execute("DELETE FROM episodes WHERE id = 'e_screen'")
    add(seeded, "e_screen_short", "screen_block", END_DAY, 22.0,
        hs.NIGHT_SCREEN_MIN_MIN - 1, {"scene": "home"})
    assert not drivers(healthspan_for_day(seeded, settings, END_DAY))[END_DAY]["night_screen"]


def test_isolated_driver_is_a_short_day_of_conversation(seeded, settings):
    add(seeded, "e_conv_brief", "conversation", END_DAY, 10.0, 5.0, {"scene": "office"})
    assert drivers(healthspan_for_day(seeded, settings, END_DAY))[END_DAY]["isolated"]
    add(seeded, "e_conv_long", "conversation", END_DAY, 12.0, 45.0, {"scene": "office"})
    assert not drivers(healthspan_for_day(seeded, settings, END_DAY))[END_DAY]["isolated"]


def test_no_daylight_driver_needs_a_measured_zero(seeded, settings):
    # With the phone row in place the day has 35 bright minutes.
    assert not drivers(healthspan_for_day(seeded, settings, DAYS[0]))[DAYS[0]]["no_daylight"]
    # Without it the glasses' own zero inside 08:00-18:00 is the measurement.
    delete_seeded(seeded, DAYS[0], "daytime_light_minutes")
    assert drivers(healthspan_for_day(seeded, settings, DAYS[0]))[DAYS[0]]["no_daylight"]


def test_annotations_and_prompts_carry_the_evidence(seeded, settings):
    body = healthspan_for_day(seeded, settings, END_DAY)
    annotations = body["annotations"]
    assert annotations
    assert len(body["narrator_prompts"]) == len(annotations)
    kinds = {a["kind"] for a in annotations}
    assert {"contrast", "extreme"} <= kinds

    caffeine = next(a for a in annotations
                    if a["kind"] == "contrast" and a["driver"] == "caffeine_late")
    assert (caffeine["n_with"], caffeine["n_without"]) == (3, 4)
    assert caffeine["hours_with"] < caffeine["hours_without"]
    assert caffeine["sleep_with"] < caffeine["sleep_without"]
    assert caffeine["rec_with"] < caffeine["rec_without"]
    assert any("Drake 2013" in line for line in caffeine["evidence"])

    worst = next(a for a in annotations if a["kind"] == "extreme")
    assert worst["worst_hours"] <= worst["best_hours"]
    assert worst["worst_day"] in DAYS and worst["best_day"] in DAYS

    # The prompt hands the model facts only, and forbids inventing numbers.
    prompt = body["narrator_prompts"][0]
    assert "do not add numbers" in prompt
    assert json.dumps(annotations[0]) in prompt


# -- adherence -------------------------------------------------------------


def test_levers_personalized_absent_until_adherence_is_learned(seeded, settings):
    assert healthspan_for_day(seeded, settings, END_DAY)["levers_personalized"] is None

    body = healthspan_for_day(seeded, settings, END_DAY)
    key = body["levers"][0]["key"]
    write(seeded, END_DAY, f"{hs.ADHERENCE_PREFIX}{key}_a", 7.0, "count", "user")
    write(seeded, END_DAY, f"{hs.ADHERENCE_PREFIX}{key}_b", 3.0, "count", "user")
    ranked = healthspan_for_day(seeded, settings, END_DAY)["levers_personalized"]
    assert ranked is not None
    assert {lv["key"] for lv in ranked} == {lv["key"] for lv in body["levers"]}
    by_lever = {lv["key"]: lv for lv in ranked}
    assert by_lever[key]["p_adherence"] == pytest.approx(0.7)
    # A lever with no history sits at the uninformative Beta(1, 1) prior.
    others = [lv for lv in ranked if lv["key"] != key]
    assert all(lv["p_adherence"] == pytest.approx(0.5) for lv in others)
    # Deterministic: the same day ranks the same way on every poll.
    assert healthspan_for_day(seeded, settings, END_DAY)["levers_personalized"] == ranked


# -- episode merging: "55 drinks" is a pipeline bug ------------------------


def test_repeated_sightings_are_one_drink_and_a_small_pin_count(seeded, settings):
    for i in range(55):
        add(seeded, f"e_drink_{i}", "alcohol_sighting", END_DAY, 19.0 + i / 60.0, 1.0,
            {"scene": "bar"})
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["observations"]["alcohol_drinks"] == 1.0
    assert "55 sighting(s) in 1 occasion(s)" in body["provenance"]["alcohol_drinks"]["detail"]
    # The engine's merge window for a sighting is 45 min, so 55 minutes of them
    # collapse to two pins rather than one -- and never to 55.
    assert body["pins_total"] == 2
    assert len(body["pins"]) == 2
    assert all(p["seen"] == "Alcohol in frame" for p in body["pins"])


def test_drinks_stop_at_the_top_of_the_hazard_curve(seeded, settings):
    """Seventeen occasions in a day is a camera re-seeing one table, not 17 drinks."""

    assert hs.MAX_DRINKS_PER_DAY == 6
    for i in range(17):
        add(seeded, f"e_occasion_{i}", "alcohol_sighting", END_DAY, 6.0 + i, 1.0, {"scene": "bar"})
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["observations"]["alcohol_drinks"] == 6.0
    detail = body["provenance"]["alcohol_drinks"]["detail"]
    assert "17 sighting(s) in 17 occasion(s)" in detail
    assert "read as 6" in detail
    assert by_key(body)["alcohol_drinks"]["dose"] == 6.0


def test_today_shows_at_most_twelve_pins(seeded, settings):
    assert hs.MAX_PINS_TODAY == 12
    kinds = ["conversation", "outdoor_block", "meal", "caffeine_sighting"]
    for hour in range(6, 23):
        for i, kind in enumerate(kinds):
            add(seeded, f"e_{kind}_{hour}", kind, END_DAY, hour + i * 0.15, 2.0, {"scene": "home"})
    body = healthspan_for_day(seeded, settings, END_DAY)
    assert body["pins_total"] > 12
    pins = body["pins"]
    assert len(pins) == 12
    # The most recent 12, in order, and each still tied to what it moved.
    times = [p["time"] for p in pins]
    assert times == sorted(times)
    assert times[-1] >= "22:00"
    assert all(p["seen"] and p["effect"] for p in pins)


# -- week view and registry ------------------------------------------------


def test_week_is_a_lite_day_per_day_plus_the_full_today(seeded, settings):
    week = hs.healthspan_week(seeded, settings, END_DAY, 7)
    assert week["day"] == END_DAY
    assert [d["day"] for d in week["days"]] == DAYS
    assert week["today"] == healthspan_for_day(seeded, settings, END_DAY)
    for lite, day in zip(week["days"], DAYS):
        full = healthspan_for_day(seeded, settings, day)
        assert lite["hours_today"] == full["hours_today"]
        assert lite["overall"] == full["overall"]
        assert lite["drivers"] == full["week_table"][-1]["drivers"]
        assert "factors" not in lite
        assert "pins" not in lite
        assert "provenance" not in lite
    json.dumps(week, allow_nan=False)


def test_week_clamps_days_to_the_documented_range(seeded, settings):
    assert hs.MAX_WEEK_DAYS == 31
    assert len(hs.healthspan_week(seeded, settings, END_DAY, 0)["days"]) == 1
    assert len(hs.healthspan_week(seeded, settings, END_DAY, 1)["days"]) == 1
    assert len(hs.healthspan_week(seeded, settings, END_DAY, 99)["days"]) == 31
    with pytest.raises(ValueError):
        hs.healthspan_week(seeded, settings, "nonsense", 7)


def test_registry_maps_every_engine_factor_to_an_app_source():
    registry = hs.healthspan_registry()
    assert {"pipeline", "factors", "limitations", "leading_indicators",
            "adapter"} <= registry.keys()
    assert len(registry["factors"]) == len(bs.FACTORS) == 20
    sources = registry["adapter"]["factor_sources"]
    # Every factor, and none of them left unmapped: a factor the engine gains
    # and the adapter has not wired fails here rather than on the page.
    assert sources.keys() == bs.FACTORS.keys()
    assert [key for key, row in sources.items() if row["source"] == "unmapped"] == []
    assert "OpenAQ" in sources["pm25"]["source"]
    assert "POST /api/pvt" in sources["rt_z"]["source"]
    # The bonus flag is carried so the page can say why sauna is off the layer scale.
    assert sources["sauna_wk"]["bonus"]
    assert all(row["bonus"] == "" for key, row in sources.items() if key != "sauna_wk")
    assert registry["adapter"]["state_markers"] == sorted(hs.STATE_MARKERS)
    assert registry["adapter"]["driver_rules"] == hs.DRIVER_RULES
    assert registry["adapter"]["conventions"] == hs.CONVENTIONS
    assert set(registry["adapter"]["provenance_labels"]) == PROVENANCE_LABELS
    json.dumps(registry, allow_nan=False)


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
    assert body["measured"] == {"count": 0, "total": 20}
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
