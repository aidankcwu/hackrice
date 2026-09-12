import asyncio
import base64
from datetime import datetime, timedelta, timezone

import httpx

from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.db import day_key


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
        pipeline.frame_store.expire(pipeline.last_tick.t + 91)
        assert (await client.get("/frames?refs=expired")).status_code == 410

        evidence = (await client.get(f"/api/evidence/{decisions[0]['id']}")).json()
        assert evidence
        jpeg = await client.get(
            f"/api/evidence/{decisions[0]['id']}/{evidence[0]['frame_ref']}"
        )
        assert jpeg.status_code == 200
        assert jpeg.headers["content-type"] == "image/jpeg"
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

        t = pipeline.last_tick.t
        body = {"device": "whoop",
                "samples": [{**NOW_SAMPLE, "t": t + i} for i in range(3)]}
        posted = (await client.post("/api/wearables/ingest", json=body)).json()
        assert posted == {"accepted": 3, "rejected": 0, "reasons": {}}

        # The live rows now win for that window, seeded rows and all.
        read = (await client.get("/api/biometrics", params={
            "metric": "heart_rate", "from": t, "to": t + 2})).json()
        assert read["origin"] == "live" and read["source"] == "whoop"
        assert [v for _, v in read["points"]] == [118.0, 118.0, 118.0]

        after = (await client.get("/api/wearables/status")).json()
        assert after["live_connected"] is True
        assert after["live_devices"] == ["whoop"]
    await pipeline.stop()


async def test_health_auto_export_and_whoop_adapter_routes(tmp_path):
    pipeline, client = await wearables_client(tmp_path, "adapters")
    async with client:
        t = pipeline.last_tick.t
        stamp = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(
            timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M:%S %z")
        hae = (await client.post("/api/wearables/ingest/health-auto-export", json={
            "data": {"metrics": [
                {"name": "heart_rate", "units": "count/min",
                 "data": [{"date": stamp, "qty": 101}]},
                {"name": "respiratory_rate", "units": "count/min",
                 "data": [{"date": stamp, "Min": 12, "Max": 20, "Avg": 16}]},
            ]}
        })).json()
        assert hae == {"accepted": 2, "rejected": 0, "reasons": {}}
        # The seeded day runs on past `t`, so read the window rather than the
        # newest row overall -- live wins inside the window it covers.
        rr = pipeline.db.biometric_window("respiratory_rate", t - 2, t + 2)
        assert rr["origin"] == "live" and rr["source"] == "apple_watch"
        assert [v for _, v in rr["points"]] == [16.0]

        whoop = (await client.post("/api/wearables/ingest/whoop", json={
            "cycle_id": 1, "sleep_id": "s",
            "created_at": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "score": {"recovery_score": 44, "resting_heart_rate": 57,
                      "hrv_rmssd_milli": 41.2, "spo2_percentage": 97.3,
                      "skin_temp_celsius": 33.4},
        })).json()
        assert whoop["accepted"] == 3 and whoop["seeded_rows"] == 1
        hrv = pipeline.db.biometric_window("hrv_rmssd", t - 2, t + 2)
        assert hrv["origin"] == "live" and hrv["source"] == "whoop"
        assert [v for _, v in hrv["points"]] == [41.2]
        assert [v for _, v in pipeline.db.biometric_series(
            "wrist_temp_dev", t - 2, t + 2)] == [0.4]
        resting = [r for r in pipeline.db.list_seeded(day_key(t), day_key(t))
                   if r.metric == "resting_hr"]
        assert resting and resting[0].value == 57.0 and resting[0].source == "whoop"
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

