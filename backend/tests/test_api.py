import asyncio
import base64

import httpx

from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings


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
        assert len(biometrics["points"]) >= 15
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
