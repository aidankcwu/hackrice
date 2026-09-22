"""Live wearable ingest: catalogue, adapters, validation, storage."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from pipeline.db import Database
from pipeline.scoring.scorer import row_provenance
from pipeline.wearables import DEVICES, LIVE_METRICS, Sample
from pipeline.wearables.adapters import (
    WHOOP_SKIN_TEMP_BASELINE_C,
    health_auto_export_to_samples,
    healthkit_seeded_rows,
    healthkit_to_samples,
    is_healthkit,
    parse_health_auto_export_date,
    whoop_seeded_rows,
    whoop_to_samples,
)
from pipeline.wearables.ingest import MAX_CLOCK_SKEW_S, ingest, ingest_samples, parse_payload

NOW = 1_757_700_000.0  # 2025-09-12T18:00Z, a fixed clock for every test here


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "wearables.db").connect().init_schema()
    yield database
    database.close()


# -- catalogue ------------------------------------------------------------


def test_catalogue_is_internally_consistent() -> None:
    assert set(DEVICES) == {"apple_watch", "whoop", "oura", "sim", "fitbit", "healthkit"}
    for metric, info in LIVE_METRICS.items():
        assert info.devices, metric
        assert set(info.devices) <= set(DEVICES), metric
        assert "sim" not in info.devices, metric  # sim is the seed, not a source
        assert info.cadence_s > 0 and info.unit and info.label, metric
    # The metrics the demo strip shows must all be accepted by the ingest.
    assert {"heart_rate", "hrv_rmssd", "spo2", "respiratory_rate",
            "wrist_temp_dev", "strain", "steps_delta", "env_sound_db"} <= set(LIVE_METRICS)


# -- Health Auto Export adapter -------------------------------------------


HAE_PAYLOAD = {
    "data": {
        "metrics": [
            {
                "name": "heart_rate",
                "units": "count/min",
                "data": [
                    {"date": "2025-09-12 13:00:00 -0500", "qty": 72},
                    {"date": "2025-09-12 13:01:00 -0500", "Min": 68, "Max": 96, "Avg": 81.5},
                ],
            },
            {
                "name": "heart_rate_variability",
                "units": "ms",
                "data": [{"date": "2025-09-12 13:00:00 -0500", "qty": 44.2}],
            },
            {
                "name": "blood_oxygen_saturation",
                "units": "%",
                "data": [{"date": "2025-09-12 13:00:00 -0500", "qty": 0.97}],
            },
            {
                "name": "step_count",
                "units": "count",
                "data": [{"date": "2025-09-12 13:00:00 -0500", "qty": 118}],
            },
            {
                "name": "environmental_audio_exposure",
                "units": "dBASPL",
                "data": [{"date": "2025-09-12 13:00:00 -0500", "qty": 61.4}],
            },
            {"name": "dietary_water", "units": "mL",
             "data": [{"date": "2025-09-12 13:00:00 -0500", "qty": 250}]},
        ]
    }
}


def test_health_auto_export_date_keeps_the_phone_offset() -> None:
    t = parse_health_auto_export_date("2025-09-12 13:00:00 -0500")
    assert t == datetime(2025, 9, 12, 18, 0, tzinfo=timezone.utc).timestamp()
    assert parse_health_auto_export_date("not a date") is None
    assert parse_health_auto_export_date(None) is None


def test_health_auto_export_maps_names_and_takes_avg_when_qty_is_absent() -> None:
    samples = health_auto_export_to_samples(HAE_PAYLOAD)
    by_metric: dict[str, list[Sample]] = {}
    for sample in samples:
        by_metric.setdefault(sample.metric, []).append(sample)

    assert set(by_metric) == {"heart_rate", "hrv_rmssd", "spo2",
                              "steps_delta", "env_sound_db"}
    assert [s.value for s in by_metric["heart_rate"]] == [72.0, 81.5]  # qty, then Avg
    assert by_metric["hrv_rmssd"][0].value == 44.2
    assert by_metric["steps_delta"][0].value == 118.0
    assert all(s.device == "apple_watch" for s in samples)
    assert by_metric["heart_rate"][0].unit == "bpm"
    assert by_metric["heart_rate"][0].t == NOW


def test_health_auto_export_survives_garbage() -> None:
    assert health_auto_export_to_samples({}) == []
    assert health_auto_export_to_samples({"data": {"metrics": "nope"}}) == []
    assert health_auto_export_to_samples(
        {"data": {"metrics": [{"name": "heart_rate", "data": [{"qty": 70}]}]}}
    ) == []  # no date -> nothing to stamp it with


# -- WHOOP adapter --------------------------------------------------------


WHOOP_RECOVERY = {
    "cycle_id": 93845,
    "sleep_id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
    "user_id": 10129,
    "created_at": "2025-09-12T12:00:00.000Z",
    "updated_at": "2025-09-12T18:00:00.000Z",
    "score_state": "SCORED",
    "score": {
        "user_calibrating": False,
        "recovery_score": 44.0,
        "resting_heart_rate": 57.0,
        "hrv_rmssd_milli": 41.2,
        "spo2_percentage": 97.3,
        "skin_temp_celsius": 33.4,
    },
}

WHOOP_CYCLE = {
    "id": 93845,
    "user_id": 10129,
    "start": "2025-09-12T06:00:00.000Z",
    "end": "2025-09-12T18:00:00.000Z",
    "score_state": "SCORED",
    "score": {"strain": 9.4, "kilojoule": 8288.3, "average_heart_rate": 68,
              "max_heart_rate": 141},
}

WHOOP_SLEEP = {
    "id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
    "start": "2025-09-12T04:00:00.000Z",
    "end": "2025-09-12T18:00:00.000Z",
    "nap": False,
    "score_state": "SCORED",
    "score": {"respiratory_rate": 14.9, "sleep_performance_percentage": 81.0},
}

WHOOP_WORKOUT = {
    "id": "1a2b3c4d-0000-4000-8000-000000000000",
    "sport_id": 1,
    "sport_name": "running",
    "start": "2025-09-12T17:00:00.000Z",
    "end": "2025-09-12T18:00:00.000Z",
    "score_state": "SCORED",
    "score": {"strain": 8.1, "average_heart_rate": 143, "max_heart_rate": 172},
}


def as_pairs(samples: list[Sample]) -> set[tuple[str, float]]:
    return {(s.metric, round(s.value, 3)) for s in samples}


def test_whoop_recovery_maps_hrv_spo2_and_a_temperature_deviation() -> None:
    samples = whoop_to_samples(WHOOP_RECOVERY)
    assert as_pairs(samples) == {
        ("hrv_rmssd", 41.2), ("spo2", 97.3),
        ("wrist_temp_dev", round(33.4 - WHOOP_SKIN_TEMP_BASELINE_C, 3)),
    }
    assert all(s.device == "whoop" and s.t == NOW for s in samples)

    rows = whoop_seeded_rows(WHOOP_RECOVERY)
    assert len(rows) == 1
    assert (rows[0].metric, rows[0].value, rows[0].source) == ("resting_hr", 57.0, "whoop")


def test_whoop_cycle_sleep_and_workout() -> None:
    cycle = whoop_to_samples(WHOOP_CYCLE)
    assert as_pairs(cycle) == {("strain", 9.4), ("heart_rate", 68.0)}
    assert all(s.t == NOW for s in cycle)  # stamped at cycle end

    sleep = whoop_to_samples(WHOOP_SLEEP)
    assert as_pairs(sleep) == {("respiratory_rate", 14.9)}

    workout = whoop_to_samples(WHOOP_WORKOUT)
    assert as_pairs(workout) == {("heart_rate", 143.0), ("heart_rate", 172.0)}
    assert sorted(s.t for s in workout) == [NOW - 3600, NOW]  # avg at start, max at end
    assert whoop_seeded_rows(WHOOP_WORKOUT) == []


def test_whoop_accepts_a_collection_page_and_an_explicit_type() -> None:
    page = {"records": [WHOOP_RECOVERY, WHOOP_SLEEP], "next_token": "abc"}
    assert as_pairs(whoop_to_samples(page)) >= {("hrv_rmssd", 41.2), ("respiratory_rate", 14.9)}
    hinted = {"type": "cycle", "record": {**WHOOP_CYCLE, "score": {"strain": 3.3}}}
    assert as_pairs(whoop_to_samples(hinted)) == {("strain", 3.3)}
    assert whoop_to_samples({"score": {}}) == []
    assert whoop_to_samples("nonsense") == []


# -- HealthKit (the phone's own sync, PLAN 3.2) ----------------------------

#: Local wall clock, so ``day_key`` agrees on every machine's zone.
HK_DAY = date(2026, 9, 21)
HK_NOW = datetime.combine(HK_DAY, time(10, 0)).timestamp()
HK_WAKE = datetime.combine(HK_DAY, time(6, 30)).timestamp()


def healthkit_body() -> dict:
    """The 3.2 body: STATE.md §5 shape, ``source: "healthkit"``."""

    return {"source": "healthkit", "samples": [
        {"t": HK_WAKE, "metric": "sleep_hours", "value": 7.25, "unit": "hours"},
        {"t": HK_WAKE, "metric": "resting_hr", "value": 54, "unit": "bpm"},
        {"t": HK_WAKE, "metric": "hrv_sdnn", "value": 48.5, "unit": "ms"},
        {"t": HK_NOW, "metric": "steps", "value": 4210, "unit": "count"},
    ]}


def test_healthkit_body_is_recognised_by_source_or_device() -> None:
    assert is_healthkit(healthkit_body())
    assert is_healthkit({"device": "HealthKit", "samples": []})
    assert not is_healthkit(canonical()) and not is_healthkit([])


def test_healthkit_daily_numbers_become_live_rows_filed_like_fitbit() -> None:
    samples, result = parse_payload(healthkit_body())
    assert result["rejected"] == 0
    rows = {r.metric: r for r in healthkit_seeded_rows(samples)}

    assert set(rows) == {"sleep_hours", "resting_hr", "hrv_rmssd_ms", "steps"}
    assert all(r.source == "healthkit" for r in rows.values())
    # Last night is filed under the evening it began; the rest under today.
    yesterday = (HK_DAY - timedelta(days=1)).isoformat()
    assert (rows["sleep_hours"].day, rows["sleep_hours"].value) == (yesterday, 7.25)
    assert rows["steps"].day == rows["resting_hr"].day == HK_DAY.isoformat()
    assert (rows["hrv_rmssd_ms"].value, rows["hrv_rmssd_ms"].unit) == (48.5, "ms")
    # The scorer calls every one of them a live device, never "Seeded".
    assert {row_provenance(r.source) for r in rows.values()} == {"live"}


def test_healthkit_sleep_in_minutes_and_undatable_rows() -> None:
    minutes = [Sample(t=HK_WAKE, metric="sleep", value=435, unit="min", device="healthkit")]
    assert healthkit_seeded_rows(minutes)[0].value == pytest.approx(7.25)
    garbled = [Sample(t=HK_NOW * 1e6, metric="steps", value=1, device="healthkit"),
               Sample(t=HK_NOW, metric="steps", value=float("nan"), device="healthkit")]
    assert healthkit_seeded_rows(garbled) == []


def test_healthkit_hrv_is_also_an_intraday_reading(db: Database) -> None:
    samples, result = parse_payload(healthkit_body())
    intraday = healthkit_to_samples(samples)
    assert [(s.metric, s.value, s.device) for s in intraday] == [("hrv_rmssd", 48.5, "healthkit")]

    stored = ingest_samples(db, intraday, now=HK_NOW, result=result)
    assert stored == {"accepted": 1, "rejected": 0, "reasons": {}}
    assert db.latest_biometric("hrv_rmssd") == (HK_WAKE, 48.5, "healthkit", "live")
    # A catalogue metric in the same body still goes through the plain ingest.
    plain = ingest(db, {"device": "healthkit", "samples": [
        {"t": HK_NOW, "metric": "heart_rate", "value": 61}]}, now=HK_NOW)
    assert plain["accepted"] == 1


# -- ingest ---------------------------------------------------------------


def canonical(**overrides) -> dict:
    sample = {"t": NOW, "metric": "heart_rate", "value": 72, "unit": "bpm"}
    sample.update(overrides)
    return {"device": "apple_watch", "samples": [sample]}


def test_canonical_ingest_round_trips_and_marks_the_rows_live(db: Database) -> None:
    result = ingest(db, {
        "device": "apple_watch",
        "samples": [
            {"t": NOW, "metric": "heart_rate", "value": 72, "unit": "bpm"},
            {"t": NOW + 60, "metric": "heart_rate", "value": 96, "unit": "bpm"},
            {"t": NOW, "metric": "spo2", "value": 97, "unit": "%", "device": "oura"},
        ],
    }, now=NOW)
    assert result == {"accepted": 3, "rejected": 0, "reasons": {}}
    assert db.biometric_series("heart_rate", NOW - 1, NOW + 61) == [(NOW, 72.0), (NOW + 60, 96.0)]
    assert db.latest_biometric("spo2") == (NOW, 97.0, "oura", "live")


def test_ingest_is_idempotent_on_time_and_metric(db: Database) -> None:
    for value in (72, 72, 88):
        ingest(db, canonical(value=value), now=NOW)
    assert db.biometric_series("heart_rate", NOW - 1, NOW + 1) == [(NOW, 88.0)]


def test_ingest_rejects_bad_metrics_devices_values_and_timestamps(db: Database) -> None:
    result = ingest(db, {
        "device": "apple_watch",
        "samples": [
            {"t": NOW, "metric": "blood_pressure", "value": 120},
            {"t": NOW, "metric": "heart_rate", "value": 72, "device": "garmin"},
            {"t": NOW, "metric": "heart_rate", "value": "fast"},
            {"t": NOW + MAX_CLOCK_SKEW_S + 1, "metric": "heart_rate", "value": 72},
            {"t": NOW * 1000, "metric": "heart_rate", "value": 72},  # epoch in ms
            "not an object",
            {"metric": "heart_rate", "value": 72},  # no timestamp
            {"t": NOW, "metric": "heart_rate", "value": 61},  # the one good row
        ],
    }, now=NOW)
    assert result["accepted"] == 1
    assert result["rejected"] == 7
    assert set(result["reasons"]) == {
        "unknown metric 'blood_pressure'", "unknown device 'garmin'",
        "value must be a number", "t outside the +/-48h window",
        "sample is not an object", "t must be epoch seconds",
    }
    assert result["reasons"]["t outside the +/-48h window"] == 2
    assert db.biometric_series("heart_rate", NOW - 1, NOW + 1) == [(NOW, 61.0)]


def test_ingest_never_raises_on_a_malformed_body(db: Database) -> None:
    assert ingest(db, [], now=NOW)["reasons"] == {"payload is not an object": 1}
    assert ingest(db, {"device": "whoop"}, now=NOW)["reasons"] == {"samples must be a list": 1}
    assert ingest(db, {"samples": []}, now=NOW) == {
        "accepted": 0, "rejected": 0, "reasons": {}}


def test_a_live_sample_overrides_the_seeded_series(db: Database) -> None:
    db.insert_biometric_series(
        [(NOW + i, "heart_rate", 58.0, "sim") for i in range(0, 120, 10)])
    ingest_samples(db, [Sample(t=NOW + 55, metric="heart_rate", value=118.0,
                               unit="bpm", device="whoop")], now=NOW)
    assert db.biometric_series("heart_rate", NOW, NOW + 120) == [(NOW + 55, 118.0)]
    # latest_biometric is over the whole table, so the seeded tail still wins
    # on recency; the window read is where live takes over.
    assert db.latest_biometric("heart_rate") == (NOW + 110, 58.0, "sim", "seed")
    assert db.biometric_series("heart_rate", NOW, NOW + 40) == [
        (NOW, 58.0), (NOW + 10, 58.0), (NOW + 20, 58.0), (NOW + 30, 58.0), (NOW + 40, 58.0)]


def test_adapter_output_flows_straight_into_the_ingest(db: Database) -> None:
    """The adapters and the ingest agree on units, devices and metric names."""

    accepted = ingest_samples(db, health_auto_export_to_samples(HAE_PAYLOAD), now=NOW)
    assert accepted["accepted"] == 6 and accepted["rejected"] == 0

    whoop = ingest_samples(
        db, whoop_to_samples({"records": [WHOOP_RECOVERY, WHOOP_CYCLE, WHOOP_SLEEP]}),
        now=NOW)
    assert whoop["rejected"] == 0 and whoop["accepted"] == 6
    live = {row["metric"] for row in db.biometric_metrics_present()
            if row["origin"] == "live"}
    assert {"heart_rate", "hrv_rmssd", "spo2", "respiratory_rate",
            "wrist_temp_dev", "strain", "steps_delta", "env_sound_db"} <= live


def test_a_sample_from_yesterday_is_still_inside_the_window(db: Database) -> None:
    yesterday = (datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(hours=30)).timestamp()
    assert ingest(db, canonical(t=yesterday), now=NOW)["accepted"] == 1
