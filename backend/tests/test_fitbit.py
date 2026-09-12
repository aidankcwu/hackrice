from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

from pipeline.wearables.fitbit import (
    FitbitClient, FitbitConfig, TokenStore, cardio_to_daily, code_challenge,
    heart_intraday_to_samples, sleep_to_daily,
)
from pipeline.wearables.fitbit_routes import router, set_sync


def test_heart_converter_uses_profile_timezone():
    got = heart_intraday_to_samples("2026-09-12", {
        "activities-heart-intraday": {"dataset": [{"time": "12:00:01", "value": 72}]},
        "activities-heart": [{"dateTime": "2026-09-12", "value": {"restingHeartRate": 58}}],
    }, "America/Chicago")
    assert got["samples"] == [{"t": 1789232401.0, "metric": "heart_rate", "value": 72, "unit": "bpm"}]
    assert got["daily"][0]["metric"] == "resting_hr"


def test_sleep_ignores_nap_and_cardio_range_midpoint():
    payload = {"sleep": [
        {"isMainSleep": False, "minutesAsleep": 20},
        {"isMainSleep": True, "dateOfSleep": "2026-09-12", "minutesAsleep": 420,
         "efficiency": 91, "startTime": "2026-09-11T23:00:00", "endTime": "2026-09-12T06:30:00",
         "levels": {"summary": {"deep": {"minutes": 60}, "rem": {"minutes": 80},
                                  "light": {"minutes": 280}, "wake": {"minutes": 30}}}},
    ]}
    values = {x["metric"]: x["value"] for x in sleep_to_daily("2026-09-12", payload, "America/Chicago")["daily"]}
    assert values == {"sleep_hours": 7, "deep_min": 60, "rem_min": 80, "light_min": 280,
                      "awake_min": 30, "sleep_efficiency": 91, "bed_time": 23, "wake_time": 6.5}
    assert cardio_to_daily("2026-09-12", {"cardioScore": [{"value": {"vo2Max": "42-46"}}]})["daily"][0]["value"] == 44


@pytest.mark.asyncio
async def test_pkce_exchange_and_secure_store(tmp_path):
    seen = {}
    async def handler(request):
        seen["request"] = request
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 100})
    config = FitbitConfig("id", "secret", token_path=str(tmp_path / "token.json"))
    store = TokenStore(config.token_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = FitbitClient(config, store, http)
        url, verifier = client.authorize_url("state")
        query = parse_qs(urlparse(url).query)
        assert query["code_challenge"] == [code_challenge(verifier)]
        await client.exchange_code("code", verifier)
    assert seen["request"].headers["Authorization"] == "Basic " + base64.b64encode(b"id:secret").decode()
    assert store.load()["refresh_token"] == "r"
    assert (tmp_path / "token.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_401_refresh_persists_rotated_token(tmp_path):
    calls = []
    async def handler(request):
        calls.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated", "expires_in": 10000})
        if request.headers["Authorization"] == "Bearer old": return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})
    store = TokenStore(tmp_path / "token.json")
    store.save({"access_token": "old", "refresh_token": "r", "expires_at": 9999999999})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await FitbitClient(FitbitConfig("id", "secret"), store, http).get("/resource") == {"ok": True}
    assert store.load()["refresh_token"] == "rotated"


@pytest.mark.asyncio
async def test_routes_unconfigured_and_authorize_redirect(tmp_path):
    class Sync:
        def __init__(self, client):
            self.client, self.connected, self.last_sync_t, self.last_error = client, False, None, None
        @property
        def requests_last_hour(self): return 0
    app = FastAPI(); app.include_router(router)
    set_sync(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/api/wearables/fitbit/status")).json()["configured"] is False
        client = FitbitClient(FitbitConfig("id", "secret", token_path=str(tmp_path / "t")), TokenStore(tmp_path / "t"))
        set_sync(Sync(client))
        response = await http.get("/api/wearables/fitbit/authorize", follow_redirects=False)
        assert response.status_code == 307
        assert parse_qs(urlparse(response.headers["location"]).query)["code_challenge_method"] == ["S256"]
    set_sync(None)


@pytest.mark.asyncio
async def test_callback_starts_exactly_one_poll_loop():
    class Client:
        class Config:
            client_id = "id"
            client_secret = "secret"
        class Store:
            def load(self): return {}
        config = Config()
        store = Store()
        async def exchange_code(self, code, verifier): return {}
        def authorize_url(self, state): return f"https://example.test/?state={state}", "verifier"

    class Sync:
        def __init__(self):
            self.client = Client()
            self.connected = False
            self.last_sync_t = self.last_error = None
            self.sync_calls = self.poll_calls = 0
        @property
        def requests_last_hour(self): return 0
        async def sync_once(self): self.sync_calls += 1; return {}
        async def run_forever(self):
            self.poll_calls += 1
            await self.sync_once()  # the real loop syncs first, then sleeps
            await asyncio.Event().wait()

    sync = Sync()
    app = FastAPI(); app.include_router(router)
    set_sync(sync)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        redirect = await http.get("/api/wearables/fitbit/authorize", follow_redirects=False)
        state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
        assert (await http.get("/api/wearables/fitbit/callback", params={"code": "ok", "state": state})).status_code == 200
        await asyncio.sleep(0)
        assert sync.connected and sync.sync_calls == 1 and sync.poll_calls == 1  # no double sync
        assert (await http.get("/api/wearables/fitbit/status")).json()["polling"] is True
    set_sync(None)
