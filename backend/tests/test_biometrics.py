from pipeline.db import Database
from pipeline.seed.biometrics import (
    BENIGN_SPO2_T,
    SPIKE_END,
    SPIKE_START,
    demo_series,
    extra_series,
    heart_rate_series,
    seed_biometric_series,
)
from pipeline.sim.scenario import DEFAULT_SCENARIO
from pipeline.wearables import LIVE_METRICS


def test_heart_rate_series_is_deterministic_and_spike_matches_lunch():
    start = 1_700_000_000.0
    rows = heart_rate_series(start, duration_s=600, resting_hr=58.0)
    assert rows == heart_rate_series(start, duration_s=600, resting_hr=58.0)

    points = [(t - start, value) for t, metric, value, source in rows]
    assert {metric for _, metric, _, _ in rows} == {"heart_rate"}
    assert {source for _, _, _, source in rows} == {"apple_watch"}
    spike = [(t, value) for t, value in points if 135 <= t <= 235]
    assert len(spike) >= 90
    assert max(value for _, value in spike) >= 58.0 * 1.4
    assert sum(value >= 58.0 * 1.4 for _, value in spike) >= 20
    assert all(value < 58.0 * 1.4 for t, value in points if not 135 <= t <= 235)

    _, segment, _ = DEFAULT_SCENARIO.segment_at(150)
    assert segment.name == "lunch_restaurant"
    assert segment.activity == "eating"
    assert segment.scene == "restaurant"


def test_minute_resolution_outside_spike_and_idempotent_seed():
    start = 1_700_000_000.0
    rows = heart_rate_series(start, duration_s=600)
    outside = [int(t - start) for t, _, _, _ in rows if t - start < 135 or t - start > 235]
    assert outside == list(range(0, 180, 60)) + list(range(240, 600, 60))

    expected = len(demo_series(start, duration_s=600))
    db = Database(":memory:").connect().init_schema()
    try:
        assert seed_biometric_series(db, start, duration_s=600) == expected
        assert seed_biometric_series(db, start, duration_s=600) == 0
        assert seed_biometric_series(db, start, force=True, duration_s=600) == expected
    finally:
        db.close()


# -- the rest of the SPEC §14.1 metric set --------------------------------


def by_metric(start: float, duration_s: int = 3600) -> dict[str, list[tuple[int, float, str]]]:
    out: dict[str, list[tuple[int, float, str]]] = {}
    for t, metric, value, source in extra_series(start, duration_s):
        out.setdefault(metric, []).append((round(t - start), value, source))
    return out


def test_every_extra_metric_is_in_the_catalogue_and_deterministic() -> None:
    start = 1_700_000_000.0
    assert extra_series(start, 3600) == extra_series(start, 3600)
    series = by_metric(start)
    assert set(series) == {
        "hrv_rmssd", "spo2", "respiratory_rate", "wrist_temp_dev",
        "steps_delta", "active_energy", "strain", "env_sound_db",
    }
    for metric, rows in series.items():
        info = LIVE_METRICS[metric]
        assert rows, metric
        for _, _, source in rows:
            assert source in info.devices, metric


def test_cadences_match_the_catalogue() -> None:
    series = by_metric(1_700_000_000.0, duration_s=7200)
    # Metrics whose cadence is exact; RR and sound add extra samples inside
    # the spike / lunch windows on purpose, so they are checked separately.
    for metric, step in (("hrv_rmssd", 3600), ("spo2", 300),
                         ("wrist_temp_dev", 3600), ("steps_delta", 60),
                         ("active_energy", 60), ("strain", 900)):
        stamps = [t for t, _, _ in series[metric]]
        assert stamps == list(range(0, 7200, step)), metric
        assert LIVE_METRICS[metric].cadence_s == step


def test_hrv_dips_in_the_hour_that_holds_the_planted_spike() -> None:
    series = by_metric(1_700_000_000.0, duration_s=7200)
    hrv = dict((t, v) for t, v, _ in series["hrv_rmssd"])
    assert hrv[0] == 40.0  # the spike sits at scenario second 135
    assert 53.0 <= hrv[3600] <= 57.0


def test_respiratory_rate_is_raised_across_the_spike() -> None:
    series = by_metric(1_700_000_000.0, duration_s=3600)
    rr = dict((t, v) for t, v, _ in series["respiratory_rate"])
    inside = [v for t, v in rr.items() if SPIKE_START <= t <= SPIKE_END]
    assert inside and all(v == 17.0 for v in inside)
    assert all(13.5 <= v <= 14.5 for t, v in rr.items() if not SPIKE_START <= t <= SPIKE_END)


def test_spo2_has_one_benign_dip_and_otherwise_sits_at_97_98() -> None:
    series = by_metric(1_700_000_000.0, duration_s=7200)
    spo2 = dict((t, v) for t, v, _ in series["spo2"])
    assert spo2[BENIGN_SPO2_T] == 95.0
    assert sorted({v for t, v in spo2.items() if t != BENIGN_SPO2_T}) == [97.0, 98.0]


def test_steps_and_energy_follow_the_scenario_segments() -> None:
    series = by_metric(1_700_000_000.0, duration_s=3600)
    steps = dict((t, v) for t, v, _ in series["steps_delta"])
    energy = dict((t, v) for t, v, _ in series["active_energy"])
    # 195-225 walk_street then 225-285 park: the only walking in the script.
    for second in (0, 60, 120):
        assert steps[second] == 0.0
        assert energy[second] == 0.3
        assert DEFAULT_SCENARIO.segment_at(second)[1].activity == "seated"
    assert steps[240] >= 85.0
    assert energy[240] == 5.2
    assert DEFAULT_SCENARIO.segment_at(240)[1].activity == "walking"
    assert steps[180] == 6.0  # lunch: a trickle, not a walk


def test_strain_is_monotonic_and_lands_near_nine_and_a_half() -> None:
    strain = [v for _, v, _ in by_metric(1_700_000_000.0, 86400)["strain"]]
    assert strain == sorted(strain)
    assert 5.0 <= strain[0] <= 6.0
    assert 9.0 <= strain[-1] <= 9.5


def test_sound_is_loud_over_lunch_and_quiet_at_the_desk() -> None:
    sound = dict((t, v) for t, v, _ in by_metric(1_700_000_000.0, 3600)["env_sound_db"])
    assert sound[0] == 45.0
    lunch = [v for t, v in sound.items() if 135 <= t < 195]
    assert lunch and all(v == 62.0 for v in lunch)
    assert DEFAULT_SCENARIO.segment_at(150)[1].scene == "restaurant"


def test_seeding_stores_every_metric_with_origin_seed() -> None:
    start = 1_700_000_000.0
    db = Database(":memory:").connect().init_schema()
    try:
        seed_biometric_series(db, start, duration_s=3600)
        present = {row["metric"]: row for row in db.biometric_metrics_present()}
        assert set(present) == set(LIVE_METRICS) - {"walking_hr_avg"}
        assert {row["origin"] for row in present.values()} == {"seed"}
        assert db.biometric_series("spo2", start, start + 3600, origin="live") == []
    finally:
        db.close()


def test_the_seeded_day_renders_a_full_wearable_now_line() -> None:
    """The seeded series must be able to fill every field of the T1 context line."""

    from pipeline.gate.triggers import CallableBiometricFeed, wearable_now_line

    start = 1_700_000_000.0
    db = Database(":memory:").connect().init_schema()
    try:
        seed_biometric_series(db, start, duration_s=3600)
        feed = CallableBiometricFeed(db.biometric_series, lambda: 58.0,
                                     db.latest_biometric)
        line = wearable_now_line(feed, start + 200)  # mid-spike, mid-lunch
        assert line is not None
        assert line.startswith("Wearable now (apple_watch/whoop): ")
        for fragment in ("HR ", "HRV 40 ms", "SpO2 97%", "RR 17",
                         "wrist temp ", "strain 5.5", "steps last 10 min"):
            assert fragment in line, line
    finally:
        db.close()

