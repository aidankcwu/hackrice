from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

from pipeline.wearables.google_health import (
    GoogleHealthClient, GoogleHealthConfig, RateLimitError, TokenStore,
    daily_to_payload, exercise_to_payload, heart_rate_to_payload,
    sleep_to_payload, steps_to_payload,
)
from pipeline.wearables.google_health_routes import router, set_sync


def test_converters_cover_intraday_daily_sleep_and_exercise():
    heart = heart_rate_to_payload({"heartRate": {"sampleTime": {"physicalTime": "2026-09-12T12:00:00Z"},
                                                  "beatsPerMinute": "72"}})
    assert heart["samples"][0]["metric"] == "heart_rate"
    assert steps_to_payload({"steps": {"interval": {"startTime": "2026-09-12T12:00:00Z"}, "count": "123"}})["samples"][0]["value"] == 123
    daily = daily_to_payload("daily-respiratory-rate", {"dailyRespiratoryRate": {
        "date": {"year": 2026, "month": 9, "day": 12}, "breathsPerMinute": 14.5}})
    assert daily["daily"][0]["metric"] == "respiratory_rate"
    sleeps = [{"sleep": {"interval": {"startTime": "2026-09-12T18:00:00Z", "endTime": "2026-09-12T18:20:00Z"}}},
              {"sleep": {"interval": {"startTime": "2026-09-13T04:00:00Z", "endTime": "2026-09-13T11:00:00Z",
                                       "startUtcOffset": "-05:00", "endUtcOffset": "-05:00"},
                         "stages": [{"startTime": "2026-09-13T04:00:00Z", "endTime": "2026-09-13T05:00:00Z", "type": "DEEP"}]}}]
    values = {r["metric"]: r["value"] for r in sleep_to_payload(sleeps)["daily"]}
    assert values["sleep_hours"] == 7 and values["bed_time"] == 23 and values["wake_time"] == 6
    exercise = exercise_to_payload({"exercise": {"interval": {"startTime": "2026-09-12T12:00:00Z", "endTime": "2026-09-12T12:30:00Z"},
                                                 "metricsSummary": {"distanceMillimeters": 5000000, "averageHeartRateBeatsPerMinute": "140"}}})
    assert {r["metric"]: r["value"] for r in exercise["daily"]}["workout_km"] == 5


@pytest.mark.asyncio
async def test_exchange_refresh_retry_rate_limit_and_pagination(tmp_path):
    calls = []
    async def handler(request):
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com": return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        if request.headers.get("Authorization") == "Bearer old": return httpx.Response(401)
        if request.url.params.get("pageToken") == "two": return httpx.Response(200, json={"dataPoints": [{"name": "b"}]})
        return httpx.Response(200, json={"dataPoints": [{"name": "a"}], "nextPageToken": "two"})
    store = TokenStore(tmp_path / "token.json")
    store.save({"access_token": "old", "refresh_token": "keep", "expires_at": time.time() + 10000})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GoogleHealthClient(GoogleHealthConfig("id", "secret"), store, http)
        pages = [p async for p in client.paginate("heart-rate", "a", "b")]
    assert [p["name"] for p in pages] == ["a", "b"] and store.load()["refresh_token"] == "keep"

    async def limited(request): return httpx.Response(429, headers={"Retry-After": "17"})
    store.save({"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 10000})
    async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as http:
        with pytest.raises(RateLimitError, match="rate limit") as exc:
            await GoogleHealthClient(GoogleHealthConfig("id", "secret"), store, http).get("/x")
    assert exc.value.retry_after == 17


@pytest.mark.asyncio
async def test_routes_authorize_callback_status_and_sync():
    class Store:
        token = {}
        def load(self): return self.token
    class Client:
        config = GoogleHealthConfig("id", "secret")
        store = Store()
        def authorize_url(self, state):
            return GoogleHealthClient(self.config, TokenStore("/tmp/unused-google-health-test")).authorize_url(state)
        async def exchange_code(self, code, verifier): self.store.token = {"expires_at": 10}
    class Sync:
        client = Client(); connected = False; last_sync_t = None; last_error = None; requests_last_hour = 0
        calls = 0
        async def sync_once(self): self.calls += 1; return {"samples": 0, "daily": 0, "requests_used": 0}
        async def run_forever(self): await asyncio.Event().wait()
    sync = Sync(); app = FastAPI(); app.include_router(router); set_sync(sync)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/wearables/google-health/authorize", follow_redirects=False)
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"] and len(query["scope"][0].split()) == 3
        await http.get("/api/wearables/google-health/callback", params={"code": "c", "state": query["state"][0]})
        assert (await http.get("/api/wearables/google-health/status")).json()["polling"] is True
        assert (await http.post("/api/wearables/google-health/sync")).status_code == 200
    set_sync(None)


def test_offset_parsing_accepts_duration_strings() -> None:
    from datetime import timedelta
    from pipeline.wearables.google_health import _offset_dt, _parse_offset

    assert _parse_offset("-18000s") == timedelta(seconds=-18000)
    assert _parse_offset("3600.5s") == timedelta(seconds=3600.5)
    assert _parse_offset("-05:00") == timedelta(hours=-5)
    assert _parse_offset("garbage") is None
    dt = _offset_dt("2026-09-12T11:04:00Z", "-18000s")
    assert dt.hour == 6 and dt.utcoffset() == timedelta(hours=-5)
