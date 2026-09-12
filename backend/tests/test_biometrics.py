from pipeline.db import Database
from pipeline.seed.biometrics import heart_rate_series, seed_biometric_series
from pipeline.sim.scenario import DEFAULT_SCENARIO


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

    db = Database(":memory:").connect().init_schema()
    try:
        inserted = seed_biometric_series(db, start, duration_s=600)
        assert inserted == len(rows)
        assert seed_biometric_series(db, start, duration_s=600) == 0
        assert seed_biometric_series(db, start, force=True, duration_s=600) == len(rows)
    finally:
        db.close()
