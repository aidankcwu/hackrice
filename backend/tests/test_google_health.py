from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI

from pipeline.wearables.google_health import (
    GoogleHealthClient, GoogleHealthConfig, RateLimitError, TokenStore,
    daily_steps_rows, daily_to_payload, exercise_to_payload, heart_rate_to_payload,
    night_day, sleep_to_payload, steps_to_payload,
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
    rows = sleep_to_payload(sleeps)["daily"]
    values = {r["metric"]: r["value"] for r in rows}
    assert values["sleep_hours"] == 7 and values["bed_time"] == 23 and values["wake_time"] == 6
    # 23:00 on the 12th -> 06:00 on the 13th is the night that *starts* on the
    # 12th (seed/fixtures.py), not the morning it ends on.
    assert {r["day"] for r in rows} == {"2026-09-12"}
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


def test_a_night_is_filed_under_the_evening_it_began():
    """seed/fixtures.py: the sleep row for day D is the night that starts on D.

    Google files a sleep session by its end, so the wake instant is walked back
    12 h in the wearer's own offset. Both a normal bedtime and an after-midnight
    one have to land on the evening before the morning they end on.
    """

    def night(start: str, end: str, offset: str = "-18000s") -> dict[str, float | str]:
        point = {"sleep": {"interval": {"startTime": start, "endTime": end,
                                        "startUtcOffset": offset, "endUtcOffset": offset}}}
        rows = sleep_to_payload([point])["daily"]
        return {"day": rows[0]["day"], **{r["metric"]: r["value"] for r in rows}}

    # The wearer's real night, from the live sync: in bed 01:14, up 09:56 on the
    # 12th, local UTC-5. The night began on the evening of the 11th.
    after_midnight = night("2026-09-12T06:14:00Z", "2026-09-12T14:56:00Z")
    assert after_midnight["day"] == "2026-09-11"
    assert round(after_midnight["bed_time"], 2) == 1.23
    assert round(after_midnight["wake_time"], 2) == 9.93
    assert round(after_midnight["sleep_hours"], 1) == 8.7

    # A 22:30 bedtime on the 11th, up 06:30 on the 12th: the same evening.
    before_midnight = night("2026-09-12T03:30:00Z", "2026-09-12T11:30:00Z")
    assert before_midnight["day"] == "2026-09-11"
    assert before_midnight["bed_time"] == 22.5 and before_midnight["wake_time"] == 6.5

    # The shift happens in the wearer's offset, not in UTC: the same wall clock
    # in Tokyo is a different UTC date and still the evening of the 11th.
    assert night("2026-09-11T13:30:00Z", "2026-09-11T21:30:00Z", "32400s")["day"] == "2026-09-11"

    # Only a sleep that ends after local noon is filed on its own day.
    assert night("2026-09-12T13:00:00Z", "2026-09-12T22:00:00Z")["day"] == "2026-09-12"
    assert night_day(datetime.fromisoformat("2026-09-12T09:56:00-05:00")) == "2026-09-11"


def test_nightly_daily_derivations_move_with_the_night_but_resting_hr_does_not():
    """Google dates the sleep derivations by the wake date; resting HR is per-day."""

    def day_of(kind: str, field: str, body: dict) -> str:
        point = {field: {"date": {"year": 2026, "month": 9, "day": 12}, **body}}
        return daily_to_payload(kind, point)["daily"][0]["day"]

    assert day_of("daily-heart-rate-variability", "dailyHeartRateVariability",
                  {"rootMeanSquareOfSuccessiveDifferencesMilliseconds": 47.3}) == "2026-09-11"
    assert day_of("daily-respiratory-rate", "dailyRespiratoryRate",
                  {"breathsPerMinute": 20.2}) == "2026-09-11"
    assert day_of("daily-sleep-temperature-derivations", "dailySleepTemperatureDerivations",
                  {"nightlyTemperatureCelsius": 33.25}) == "2026-09-11"
    assert day_of("daily-oxygen-saturation", "dailyOxygenSaturation",
                  {"averagePercentage": 97.0}) == "2026-09-11"
    # A whole-day value keeps Google's own date.
    assert day_of("daily-resting-heart-rate", "dailyRestingHeartRate",
                  {"beatsPerMinute": 64}) == "2026-09-12"


def test_intraday_step_deltas_become_one_daily_steps_row_per_local_day():
    """The engine reads a daily ``steps`` row; without one it uses the demo phone row."""

    def point(stamp: str, count: int, offset: str | None = "-18000s") -> dict:
        interval = {"startTime": stamp}
        if offset:
            interval["startUtcOffset"] = offset
        return {"steps": {"interval": interval, "count": str(count)}}

    samples = [steps_to_payload(point(*args))["samples"][0] for args in (
        ("2026-09-11T14:00:00Z", 1200),   # 09:00 local on the 11th
        ("2026-09-11T23:30:00Z", 800),    # 18:30 local on the 11th
        ("2026-09-12T04:30:00Z", 400),    # 23:30 local, still the 11th
        ("2026-09-12T06:00:00Z", 250),    # 01:00 local on the 12th
    )]
    assert [s["metric"] for s in samples] == ["steps_delta"] * 4
    rows = daily_steps_rows(samples)
    assert [(r["day"], r["value"]) for r in rows] == [("2026-09-11", 2400.0), ("2026-09-12", 250.0)]
    assert {r["metric"] for r in rows} == {"steps"}
    assert {r["unit"] for r in rows} == {"steps"}
    assert {r["source"] for r in rows} == {"fitbit"}

    # A day the pull only partly covers is dropped: the row is upserted, and a
    # partial sum would overwrite a complete one.
    assert [r["day"] for r in daily_steps_rows(samples, "2026-09-12")] == ["2026-09-12"]

    # No offset from Google: the Mac's own zone, and nothing but steps counts.
    naive = steps_to_payload(point("2026-09-12T06:00:00Z", 9, offset=None))["samples"][0]
    assert "utc_offset" not in naive
    expected = datetime.fromtimestamp(naive["t"]).date().isoformat()
    assert daily_steps_rows([naive, {"t": naive["t"], "metric": "heart_rate", "value": 70}]) == [
        {"day": expected, "metric": "steps", "value": 9.0, "unit": "steps", "source": "fitbit"}]
    assert daily_steps_rows([]) == []


@pytest.mark.asyncio
async def test_sync_writes_a_daily_steps_row_through_the_normal_sink(tmp_path):
    """The steps roll-up rides the same ``daily`` list as sleep and resting HR."""

    from pipeline.wearables import google_health as gh

    now = datetime(2026, 9, 12, 20, 0, tzinfo=timezone.utc)
    stamp = now - timedelta(hours=3)
    minute = stamp.isoformat().replace("+00:00", "Z")
    windows: dict[str, tuple] = {}

    class FakeClient:
        store = TokenStore(tmp_path / "t.json")
        requests_last_hour = 0

        async def paginate(self, kind, start, end):
            windows[kind] = (start, end)
            if kind == "steps":
                for count in (11, 22):
                    yield {"steps": {"interval": {"startTime": minute}, "count": count}}
            return

    seen: list[dict] = []
    sync = gh.GoogleHealthSync(FakeClient(), seen.append, interval_s=1)
    result = await sync.sync_once(now)

    payload = seen[0]
    steps_rows = [r for r in payload["daily"] if r["metric"] == "steps"]
    expected_day = stamp.astimezone().date().isoformat()
    assert steps_rows == [{"day": expected_day, "metric": "steps", "value": 33.0,
                           "unit": "steps", "source": "fitbit"}]
    assert result["daily"] == len(payload["daily"])
    # Steps are pulled over whole local days; everything else keeps the short
    # incremental window.
    assert windows["steps"][0] < windows["heart-rate"][0]
    assert windows["steps"][0].date().isoformat() <= (now - timedelta(days=1)).date().isoformat()


def test_the_sink_stores_the_daily_steps_row_as_a_live_fitbit_row():
    from pipeline.db import Database
    from pipeline.wearables.connect import make_sink

    db = Database(":memory:").connect().init_schema()
    try:
        make_sink(db)({"device": "fitbit", "samples": [],
                       "daily": [{"day": "2026-09-11", "metric": "steps", "value": 2400.0,
                                  "unit": "steps", "source": "fitbit"}]})
        row = next(r for r in db.list_seeded("2026-09-11", "2026-09-11") if r.metric == "steps")
        assert row.value == 2400.0 and row.source == "fitbit" and row.unit == "steps"
    finally:
        db.close()
