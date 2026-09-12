"""Fitbit sink: samples -> live biometric rows, daily -> seeded rows."""

from pipeline.db import Database
from pipeline.wearables.connect import make_sink


def test_sink_routes_samples_and_daily_rows() -> None:
    db = Database(":memory:").connect().init_schema()
    sink = make_sink(db)
    import time
    now = time.time()
    result = sink({
        "device": "fitbit",
        "samples": [
            {"t": now - 60, "metric": "heart_rate", "value": 71, "unit": "bpm"},
            {"t": now - 30, "metric": "spo2", "value": 97, "unit": "%"},
            {"t": now - 10, "metric": "not_a_metric", "value": 1, "unit": "x"},
        ],
        "daily": [
            {"day": "2026-09-12", "metric": "resting_hr", "value": 58, "unit": "bpm", "source": "fitbit"},
            {"day": "2026-09-12", "metric": "sleep_hours", "value": None, "unit": "h", "source": "fitbit"},
        ],
    })
    assert result["samples"]["accepted"] == 2 and result["samples"]["rejected"] == 1
    assert result["daily"] == 1
    hr = db.biometric_series("heart_rate", now - 120, now)
    assert hr and hr[0][1] == 71
    rows = db.list_seeded("2026-09-12", "2026-09-12")
    assert any(r.metric == "resting_hr" and r.source == "fitbit" for r in rows)
    db.close()
