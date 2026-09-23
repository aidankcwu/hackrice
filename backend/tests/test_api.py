import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.db import day_key
from pipeline.wearables import air


def client_for(app) -> httpx.AsyncClient:
    """An ASGI client against ``app``, for the routes that need their own pipeline."""

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


async def test_dashboard_routes(tmp_path):
    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "api.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    for _ in range(1_000):
        if pipeline.db.stats()["tick_count"] >= 210:
            break
        await asyncio.sleep(0.01)

    app = create_app(pipeline)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        status = (await client.get("/api/status")).json()
        assert {"demo_mode", "source", "tick_count", "ai_coverage"} <= status.keys()

        ticks = (await client.get("/api/ticks/recent?n=3")).json()
        assert len(ticks) == 3 and "frame_ref" in ticks[0]
        assert isinstance((await client.get("/api/episodes")).json(), list)

        decisions = (await client.get("/api/decisions?limit=3")).json()
        assert decisions and {"id", "trigger", "actions", "spoke", "dropped"} <= decisions[0].keys()
        assert isinstance((await client.get("/api/insights?limit=3")).json(), list)

        for period in ("daily", "weekly"):
            body = (await client.get(f"/api/scores?period={period}")).json()
            assert body["period"] == period
            assert isinstance(body["scores"], list)
            assert isinstance(body["overall"], float)

        assert isinstance((await client.get("/api/pending_checks")).json(), list)
        summary = (await client.get("/api/summary/today")).json()
        assert {"day", "lines"} == summary.keys()
        assert isinstance((await client.get("/api/seeded?days=7")).json()["rows"], list)
        biometrics = (await client.get(
            "/api/biometrics",
            params={
                "metric": "heart_rate",
                "from": pipeline.biometrics_start_t + 135,
                "to": pipeline.biometrics_start_t + 155,
            },
        )).json()
        assert biometrics["metric"] == "heart_rate"
        assert biometrics["source"] == "apple_watch"
        assert biometrics["origin"] == "seed"
        assert len(biometrics["points"]) >= 15

        multi = (await client.get("/api/biometrics", params={
            "metrics": "heart_rate,spo2,strain,not_a_metric",
            "from": pipeline.biometrics_start_t,
            "to": pipeline.biometrics_start_t + 400,
        })).json()
        assert set(multi["series"]) == {"heart_rate", "spo2", "strain", "not_a_metric"}
        assert multi["series"]["spo2"]["origin"] == "seed"
        assert multi["series"]["spo2"]["source"] == "apple_watch"
        assert multi["series"]["strain"]["source"] == "whoop"
        assert multi["series"]["not_a_metric"]["points"] == []
        assert len(multi["series"]["heart_rate"]["points"]) >= 15
        event = await client.get("/api/events")
        assert event.status_code == 501
        assert event.json() == {"detail": "SSE deferred; poll"}

        live = pipeline.last_tick.frame_ref
        frame_response = await client.get("/frames", params={"refs": live})
        assert frame_response.status_code == 200
        assert base64.b64decode(frame_response.json()[live]).startswith(b"\xff\xd8")
        pipeline.frame_store.put("expired", b"jpeg", pipeline.last_tick.t)
        # Far past every live frame on purpose: the pump may already hold frame
        # N+1 while last_tick is still N, and the store's expiry stops at the
        # first entry newer than the cutoff -- with a cutoff of +91 it would
        # stop at N+1 and never reach the stale entry appended behind it.
        pipeline.frame_store.expire(pipeline.last_tick.t + 10_000)
        assert (await client.get("/frames?refs=expired")).status_code == 410

        evidence = (await client.get(f"/api/evidence/{decisions[0]['id']}")).json()
        assert evidence
        jpeg = await client.get(
            f"/api/evidence/{decisions[0]['id']}/{evidence[0]['frame_ref']}"
        )
        assert jpeg.status_code == 200
        assert jpeg.headers["content-type"] == "image/jpeg"

        # Last on purpose: the route runs off-loop (asyncio.to_thread), and the
        # frame-expiry check above is sensitive to where the pump has got to.
        body = (await client.get("/api/healthspan")).json()
        assert {"day", "overall", "layers", "hours_today", "hours_ci", "years_delta",
                "years_ci", "factors", "levers", "levers_free", "levers_personalized",
                "forecast", "ledger", "insights", "pins", "experience", "currencies",
                "week_table", "annotations", "narrator_prompts", "driver_rules",
                "provenance", "profile", "window"} <= body.keys()
        assert 0 <= body["overall"] <= 100
        assert len(body["layers"]) == 8
        assert len(body["factors"]) == 20
        assert body["day"] == day_key(pipeline.last_tick.t)
        assert body["as_of_hh"] is not None
        unseeded = (await client.get("/api/healthspan?day=2020-01-01")).json()
        assert unseeded["day"] == "2020-01-01"
        assert unseeded["provenance"]["steps"]["source"] == "missing"
        assert unseeded["as_of_hh"] is None
        bad = await client.get("/api/healthspan?day=nonsense")
        assert bad.status_code == 400
        assert bad.json() == {"detail": "day must be YYYY-MM-DD"}
    await pipeline.stop()


async def test_healthspan_week_and_registry(tmp_path):
    """``?days=N`` answers the week chart in one round trip; the registry is static."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "week.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    try:
        async with client_for(create_app(pipeline)) as client:
            week = (await client.get("/api/healthspan?days=7")).json()
            assert len(week["days"]) == 7
            days = [d["day"] for d in week["days"]]
            assert days == sorted(days)
            assert days[-1] == week["today"]["day"] == week["day"]
            lite = week["days"][-1]
            # A lite day carries the chart and the table, not 20 factor rows.
            assert {"day", "overall", "hours_today", "hours_ci", "layers", "experience",
                    "currencies", "forecast", "drivers", "measured"} <= lite.keys()
            assert "factors" not in lite and "pins" not in lite
            assert set(lite["drivers"]) == {"caffeine_late", "alcohol", "night_screen",
                                            "late_bed", "no_daylight", "isolated"}
            assert lite["hours_today"] == week["today"]["hours_today"]
            one = (await client.get("/api/healthspan?days=1")).json()
            assert [d["day"] for d in one["days"]] == [week["day"]]
            assert (await client.get("/api/healthspan?days=0")).status_code == 422
            assert (await client.get("/api/healthspan?days=32")).status_code == 422

            registry = (await client.get("/api/healthspan/registry")).json()
            assert {"pipeline", "factors", "limitations", "leading_indicators",
                    "adapter"} <= registry.keys()
            assert len(registry["factors"]) == 20
            sources = registry["adapter"]["factor_sources"]
            assert len(sources) == 20
            assert all(row["source"] != "unmapped" for row in sources.values())
            assert "OpenAQ" in sources["pm25"]["source"]
            assert sources["sauna_wk"]["bonus"]
            assert sources["steps"]["bonus"] == ""
            assert registry["adapter"]["state_markers"] == [
                "gait_speed", "recovery_ratio", "rt_z"]
    finally:
        await pipeline.stop()


async def test_pvt_writes_rows_and_feeds_mind(tmp_path):
    """A filed PVT becomes the day's ``rt_z`` factor and its fully-lived hours."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "pvt.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    try:
        async with client_for(create_app(pipeline)) as client:
            before = (await client.get("/api/healthspan")).json()
            assert before["provenance"]["rt_z"] == {
                "source": "missing", "basis": "pvt", "detail": "no PVT today"}

            posted = await client.post("/api/pvt", json={
                "rt_z": 0.8, "lapses": 2, "rt_ms_median": 312.0,
                "energy": 3, "mood": 4, "clarity": 3})
            assert posted.status_code == 200
            filed = posted.json()
            assert filed["day"] == day_key(pipeline.last_tick.t)
            assert 0.4 <= filed["experience"]["utility"] <= 1.0
            assert filed["experience"]["fully_lived_hours"] == pytest.approx(
                24 * filed["experience"]["utility"], abs=0.05)
            assert filed["experience"]["components"]["pvt"] == {"rt_z": 0.8, "lapses": 2.0}
            assert filed["experience"]["components"]["check"] == {
                "energy": 3.0, "mood": 4.0, "clarity": 3.0}

            after = (await client.get("/api/healthspan")).json()
            rt_z = {row["key"]: row for row in after["factors"]}["rt_z"]
            assert rt_z["measured"] is True
            assert rt_z["dose"] == pytest.approx(0.8)
            assert rt_z["hours"] < 0  # slower than baseline costs hours
            assert after["provenance"]["rt_z"]["source"] == "derived"
            assert after["provenance"]["rt_z"]["basis"] == "pvt"
            assert "2 lapse(s)" in after["provenance"]["rt_z"]["detail"]
            assert after["experience"]["components"]["pvt"]["rt_z"] == pytest.approx(0.8)
            # Asserted on rt_z alone: the sim pipeline keeps building episodes
            # between the two reads, so a live factor may turn measured on its own.
            measured_before = {r["key"] for r in before["factors"] if r["measured"]}
            measured_after = {r["key"] for r in after["factors"] if r["measured"]}
            assert "rt_z" not in measured_before
            assert "rt_z" in measured_after
            assert measured_before <= measured_after
            # rt_z is a state marker: it never becomes something to "do".
            assert "rt_z" not in {lv["key"] for lv in after["levers"] + after["levers_free"]}

            # A second filing replaces the day's rows rather than adding to them.
            again = await client.post("/api/pvt", json={"rt_z": -0.4})
            assert again.status_code == 200
            latest = (await client.get("/api/healthspan")).json()
            assert {r["key"]: r for r in latest["factors"]}["rt_z"]["dose"] == pytest.approx(-0.4)
            assert "lapse(s)" in latest["provenance"]["rt_z"]["detail"]  # the old row stands
    finally:
        await pipeline.stop()


@pytest.mark.parametrize("body, field", [
    ({}, "rt_z"),
    ({"rt_z": 6.0}, "rt_z"),
    ({"rt_z": -6.0}, "rt_z"),
    ({"rt_z": "fast"}, "rt_z"),
    ({"rt_z": True}, "rt_z"),
    ({"rt_z": 0.0, "lapses": -1}, "lapses"),
    ({"rt_z": 0.0, "lapses": 101}, "lapses"),
    ({"rt_z": 0.0, "energy": 0}, "energy"),
    ({"rt_z": 0.0, "mood": 6}, "mood"),
    ({"rt_z": 0.0, "clarity": "good"}, "clarity"),
])
async def test_pvt_rejects_implausible_input(tmp_path, body, field):
    """Out of range is a 400, never a clamp -- a clamped reading is an invented one."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / f"pvt_bad_{field}.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    try:
        async with client_for(create_app(pipeline)) as client:
            response = await client.post("/api/pvt", json=body)
            assert response.status_code == 400
            assert field in response.json()["detail"]
            assert (await client.get("/api/healthspan")).json()[
                "provenance"]["rt_z"]["source"] == "missing"
    finally:
        await pipeline.stop()


async def test_air_status_unconfigured_is_honest_not_an_error(tmp_path):
    """No coordinates is the default: configured false, no value, no request made."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "air.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    try:
        async with client_for(create_app(pipeline)) as client:
            body = (await client.get("/api/air/status")).json()
            assert body == {"configured": False, "lat": None, "lon": None,
                            "last_value": None, "last_t": None, "source": None}
            # pm25 stays unmeasured, and that is not an error anywhere.
            prov = (await client.get("/api/healthspan")).json()["provenance"]["pm25"]
            assert prov["source"] == "missing"
            assert prov["basis"] == "openaq"
            assert "AIR_LAT" in prov["detail"]
    finally:
        await pipeline.stop()


async def test_air_status_reports_the_cached_reading(tmp_path):
    """With coordinates the route reports the reading and makes no request of its own.

    The cache is primed through a fake transport in the same poll bucket, so the
    route's own ``poll_pm25`` is answered from memory -- which is also what stops
    a dashboard poll storm from becoming an OpenAQ poll storm.
    """

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "air_on.db", air_lat=29.7174, air_lon=-95.4018,
                 air_poll_s=10**9),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    air.clear_cache()
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/latest"):
                return httpx.Response(200, json={"results": [
                    {"sensorsId": 88, "value": 31.0,
                     "datetime": {"utc": "2026-09-13T17:00:00Z"}}]})
            return httpx.Response(200, json={"results": [
                {"id": 4321, "name": "Houston Aldine",
                 "sensors": [{"id": 88, "parameter": {"id": 2, "name": "pm25"}}]}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            primed = await air.fetch_pm25(29.7174, -95.4018, poll_s=10**9, http=http)
        assert primed is not None

        async with client_for(create_app(pipeline)) as client:
            body = (await client.get("/api/air/status")).json()
            assert body["configured"] is True
            assert (body["lat"], body["lon"]) == (29.7174, -95.4018)
            assert body["last_value"] == pytest.approx(31.0)
            assert body["last_t"] is not None
            assert body["source"] == "openaq · Houston Aldine"

            # The route wrote the day's row, so Outside reads it like any other.
            today = (await client.get("/api/healthspan")).json()
            assert today["provenance"]["pm25"]["source"] == "seeded"
            assert today["provenance"]["pm25"]["basis"] == "openaq"
            assert today["observations"]["pm25"] == pytest.approx(31.0)
            assert {r["key"]: r for r in today["factors"]}["pm25"]["hours"] < 0
    finally:
        air.clear_cache()
        await pipeline.stop()


NOW_SAMPLE = {"metric": "heart_rate", "value": 118, "unit": "bpm"}


async def wearables_client(tmp_path, name: str):
    """A started pipeline plus an ASGI client, for the ingest routes."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / f"{name}.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    for _ in range(1_000):
        if pipeline.last_tick is not None:
            break
        await asyncio.sleep(0.01)
    app = create_app(pipeline)
    transport = httpx.ASGITransport(app=app)
    return pipeline, httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_wearable_ingest_round_trip_and_status(tmp_path):
    pipeline, client = await wearables_client(tmp_path, "ingest")
    async with client:
        before = (await client.get("/api/wearables/status")).json()
        assert before["live_connected"] is False
        assert before["live_devices"] == []
        assert {"heart_rate", "spo2", "strain"} <= {r["metric"] for r in before["metrics"]}
        assert {r["origin"] for r in before["metrics"]} == {"seed"}
        assert before["catalogue"]["heart_rate"]["unit"] == "bpm"

        wall_t = time.time()
        t = pipeline.clock.wall_to_tick(wall_t)
        body = {"device": "whoop",
                "samples": [{**NOW_SAMPLE, "t": wall_t + i} for i in range(3)]}
        posted = (await client.post("/api/wearables/ingest", json=body)).json()
        assert posted == {"accepted": 3, "rejected": 0, "reasons": {},
                          "wall_t": [wall_t + i for i in range(3)]}

        # The live rows now win for that window, seeded rows and all.
        read = (await client.get("/api/biometrics", params={
            "metric": "heart_rate", "from": t, "to": t + 2 * pipeline.clock.speed})).json()
        assert read["origin"] == "live" and read["source"] == "whoop"
        assert [v for _, v in read["points"]] == [118.0, 118.0, 118.0]

        after = (await client.get("/api/wearables/status")).json()
        assert after["live_connected"] is True
        assert after["live_devices"] == ["whoop"]
    await pipeline.stop()


async def test_health_auto_export_and_whoop_adapter_routes(tmp_path):
    pipeline, client = await wearables_client(tmp_path, "adapters")
    async with client:
        wall_t = time.time()
        t = pipeline.clock.wall_to_tick(float(int(wall_t)))
        stamp = datetime.fromtimestamp(wall_t, tz=timezone.utc).astimezone(
            timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M:%S %z")
        hae = (await client.post("/api/wearables/ingest/health-auto-export", json={
            "data": {"metrics": [
                {"name": "heart_rate", "units": "count/min",
                 "data": [{"date": stamp, "qty": 101}]},
                {"name": "respiratory_rate", "units": "count/min",
                 "data": [{"date": stamp, "Min": 12, "Max": 20, "Avg": 16}]},
            ]}
        })).json()
        assert hae["accepted"] == 2 and hae["rejected"] == 0 and hae["reasons"] == {}
        assert hae["wall_t"] == [float(int(wall_t))] * 2
        # The seeded day runs on past `t`, so read the window rather than the
        # newest row overall -- live wins inside the window it covers.
        rr = pipeline.db.biometric_window("respiratory_rate", t - 2, t + 2)
        assert rr["origin"] == "live" and rr["source"] == "apple_watch"
        assert [v for _, v in rr["points"]] == [16.0]

        whoop = (await client.post("/api/wearables/ingest/whoop", json={
            "cycle_id": 1, "sleep_id": "s",
            "created_at": datetime.fromtimestamp(wall_t, tz=timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(wall_t, tz=timezone.utc).isoformat(),
            "score": {"recovery_score": 44, "resting_heart_rate": 57,
                      "hrv_rmssd_milli": 41.2, "spo2_percentage": 97.3,
                      "skin_temp_celsius": 33.4},
        })).json()
        assert whoop["accepted"] == 3 and whoop["seeded_rows"] == 1
        whoop_t = pipeline.clock.wall_to_tick(wall_t)
        hrv = pipeline.db.biometric_window("hrv_rmssd", whoop_t - 2, whoop_t + 2)
        assert hrv["origin"] == "live" and hrv["source"] == "whoop"
        assert [v for _, v in hrv["points"]] == [41.2]
        assert [v for _, v in pipeline.db.biometric_series(
            "wrist_temp_dev", whoop_t - 2, whoop_t + 2)] == [0.4]
        resting = [r for r in pipeline.db.list_seeded(day_key(whoop_t), day_key(whoop_t))
                   if r.metric == "resting_hr"]
        assert resting and resting[0].value == 57.0 and resting[0].source == "whoop"
    await pipeline.stop()


async def test_healthkit_body_on_the_canonical_route_lands_as_live_daily_rows(tmp_path):
    """PLAN 3.2's POST: sleep, resting HR, HRV SDNN, steps under ``source: healthkit``."""

    from pipeline.scoring.scorer import Scorer

    pipeline, client = await wearables_client(tmp_path, "healthkit")
    async with client:
        now = datetime.now()
        wake = datetime.combine(now.date(), datetime.min.time()) + timedelta(hours=6, minutes=30)
        wake = min(wake, now - timedelta(minutes=5)).timestamp()
        steps_t = (now - timedelta(minutes=1)).timestamp()
        posted = (await client.post("/api/wearables/ingest", json={
            "source": "healthkit",
            "samples": [
                {"t": wake, "metric": "sleep_hours", "value": 7.25, "unit": "hours"},
                {"t": wake, "metric": "resting_hr", "value": 54, "unit": "bpm"},
                {"t": wake, "metric": "hrv_sdnn", "value": 48.5, "unit": "ms"},
                {"t": steps_t, "metric": "steps", "value": 4210, "unit": "count"},
            ],
        })).json()
        assert posted["accepted"] == 1 and posted["rejected"] == 0  # the HRV reading
        assert posted["seeded_rows"] == 4

        night = day_key(wake - 12 * 3600)
        rows = {r.metric: r for r in pipeline.db.list_seeded(night, day_key(steps_t))
                if r.source == "healthkit"}
        assert {m: r.value for m, r in rows.items()} == {
            "sleep_hours": 7.25, "resting_hr": 54.0, "hrv_rmssd_ms": 48.5, "steps": 4210.0}
        assert rows["sleep_hours"].day == night

        # The §8 scorer labels the phone's night live, not "Seeded".
        sleep = next(s for s in Scorer(pipeline.db).score_day(night) if s.metric == "sleep_hours")
        assert sleep.source == "live" and "live from healthkit" in (sleep.note or "")
    await pipeline.stop()


async def test_ingest_token_is_enforced_only_when_the_env_sets_one(tmp_path, monkeypatch):
    pipeline, client = await wearables_client(tmp_path, "token")
    async with client:
        t = pipeline.last_tick.t
        body = {"device": "whoop", "samples": [{**NOW_SAMPLE, "t": t}]}
        monkeypatch.setenv("WEARABLE_INGEST_TOKEN", "hunter2")
        for path in ("/api/wearables/ingest",
                     "/api/wearables/ingest/health-auto-export",
                     "/api/wearables/ingest/whoop"):
            assert (await client.post(path, json=body)).status_code == 401
            assert (await client.post(
                path, json=body, headers={"X-Ingest-Token": "wrong"})).status_code == 401
        ok = await client.post("/api/wearables/ingest", json=body,
                               headers={"X-Ingest-Token": "hunter2"})
        assert ok.status_code == 200 and ok.json()["accepted"] == 1

        monkeypatch.delenv("WEARABLE_INGEST_TOKEN")
        assert (await client.post("/api/wearables/ingest", json=body)).status_code == 200
    await pipeline.stop()


async def test_fast_sim_maps_live_wall_samples_into_the_tick_window(tmp_path):
    pipeline = build_pipeline(Settings(db_path=tmp_path / "fast-live.db"), source="sim",
                              reasoner_mode="fake", speed=10)
    await pipeline.start()
    for _ in range(100):
        if pipeline.last_tick is not None:
            break
        await asyncio.sleep(0.01)
    app = create_app(pipeline)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        wall_t = time.time()
        response = await client.post("/api/wearables/ingest", json={
            "device": "whoop", "samples": [{**NOW_SAMPLE, "t": wall_t}],
        })
        assert response.json()["accepted"] == 1
        tick_t = pipeline.clock.wall_to_tick(wall_t)
        rows = (await client.get("/api/biometrics", params={
            "metric": "heart_rate", "from": tick_t - 1, "to": tick_t + 1,
        })).json()
        assert rows["origin"] == "live" and rows["points"] == [[tick_t, 118.0]]

        old = await client.post("/api/wearables/ingest", json={
            "device": "whoop", "samples": [{**NOW_SAMPLE, "t": wall_t - 3 * 86400}],
        })
        assert old.json()["accepted"] == 0
        assert old.json()["reasons"] == {"t outside the +/-48h window": 1}
    await pipeline.stop()


async def test_status_passes_watcher_and_labeler_through(tmp_path):
    from test_health import FakeCapture

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "watch.db"),
        source="sim", reasoner_mode="fake", speed=1,
    )
    labeler = {"calls_per_hour": {"heartbeat": 1}, "capped": False,
               "last_heartbeat_age_s": 200.0, "last_wake_latency_ms": None,
               "frames_sent_per_hour": 1, "mode": "idle"}
    app = create_app(pipeline)  # before the fake: the app mounts a real capture's ring
    pipeline.capture = FakeCapture({"loop": "ticks=1", "watcher": {"frames": 4},
                                    "labeler": labeler, "watcher_error": None})
    async with client_for(app) as client:
        status = (await client.get("/api/status")).json()
    capture = status["capture"]
    assert capture["watcher"] == {"frames": 4}
    assert capture["labeler"] == labeler
    assert capture["watcher_error"] is None
    assert capture["labeler_stale_after_s"] == 120.0
    assert "labeler_heartbeat_stale" in status["health"]["problems"]
    assert "ai_coverage_low" not in status["health"]["problems"]
