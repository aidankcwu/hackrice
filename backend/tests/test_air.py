"""The OpenAQ v3 PM2.5 client (``pipeline/wearables/air.py``).

No network: every test drives ``httpx.MockTransport``, so a failure here is a
failure in the parsing, the cache or the honesty rules, never in someone's
Wi-Fi. The rules under test are the ones the product depends on --
**never invent a value** (every failure path answers ``None`` and writes no
row), **never raise into a request path**, and **one upstream request per poll
window** however often the dashboard asks.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from pipeline.db import Database
from pipeline.wearables import air

LAT, LON = 29.7174, -95.4018          # Houston, Rice University
DAY = "2026-09-13"
#: Local noon on DAY as epoch seconds, so ``day_key`` lands on DAY whatever the zone.
NOON = datetime.fromisoformat(f"{DAY}T12:00:00").timestamp()

LOCATIONS = {"results": [{
    "id": 4321,
    "name": "Houston Aldine",
    "sensors": [
        {"id": 77, "parameter": {"id": 1, "name": "pm10"}},
        {"id": 88, "parameter": {"id": 2, "name": "pm25"}},
    ],
}]}
LATEST = {"results": [
    {"sensorsId": 77, "value": 40.0, "datetime": {"utc": "2026-09-13T17:00:00Z"}},
    {"sensorsId": 88, "value": 8.4, "datetime": {"utc": "2026-09-13T16:00:00Z"}},
    {"sensorsId": 88, "value": 12.6, "datetime": {"utc": "2026-09-13T17:00:00Z"}},
]}


@pytest.fixture(autouse=True)
def clean_cache():
    """The module cache is process-global; no test may inherit another's hour."""

    air.clear_cache()
    yield
    air.clear_cache()


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


def fake(*, locations=LOCATIONS, latest=LATEST, status=200,
         log: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    """A client whose two OpenAQ GETs are answered from memory."""

    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(request)
        if request.url.path.endswith("/latest"):
            return httpx.Response(status, json=latest)
        return httpx.Response(status, json=locations)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def boom(exc: Exception) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def rows(db: Database, day: str = DAY) -> dict[str, float]:
    return {row.metric: row.value for row in db.list_seeded(day, day)}


# -- the happy path --------------------------------------------------------


async def test_nearest_station_newest_pm25_value():
    """The newest PM2.5 row wins, and a PM10 row on the same station is ignored."""

    log: list[httpx.Request] = []
    async with fake(log=log) as http:
        reading = await air.fetch_pm25(LAT, LON, api_key="k", http=http, now=NOON)
    assert reading is not None
    assert reading.value == pytest.approx(12.6)          # 17:00, not the 16:00 8.4
    assert reading.station == "Houston Aldine"
    assert reading.station_id == 4321
    assert reading.measured_at == "2026-09-13T17:00:00Z"

    assert [r.url.path for r in log] == ["/v3/locations", "/v3/locations/4321/latest"]
    assert all(r.headers["X-API-Key"] == "k" for r in log)
    query = dict(log[0].url.params)
    assert query["coordinates"] == f"{LAT:.4f},{LON:.4f}"
    assert int(query["radius"]) == air.DEFAULT_RADIUS_M
    assert int(query["parameters_id"]) == air.PM25_PARAMETER_ID


async def test_no_key_still_asks_without_the_header():
    """OpenAQ answers some calls unauthenticated: a missing key is degraded, not broken."""

    log: list[httpx.Request] = []
    async with fake(log=log) as http:
        reading = await air.fetch_pm25(LAT, LON, http=http, now=NOON)
    assert reading is not None
    assert all("X-API-Key" not in r.headers for r in log)


async def test_radius_is_clamped_to_the_v3_maximum():
    log: list[httpx.Request] = []
    async with fake(log=log) as http:
        await air.fetch_pm25(LAT, LON, radius_m=10**9, http=http, now=NOON)
    assert int(dict(log[0].url.params)["radius"]) == 25_000


# -- the cache -------------------------------------------------------------


async def test_one_request_per_poll_window_however_often_it_is_asked():
    log: list[httpx.Request] = []
    async with fake(log=log) as http:
        first = await air.fetch_pm25(LAT, LON, poll_s=3600, http=http, now=NOON)
        for offset in (1.0, 60.0, 3599.0):
            again = await air.fetch_pm25(LAT, LON, poll_s=3600, http=http, now=NOON + offset)
            assert again == first
        assert len(log) == 2                       # locations + latest, once

        # A new hour refetches; a nearby coordinate shares the key, a far one does not.
        await air.fetch_pm25(LAT, LON, poll_s=3600, http=http, now=NOON + 3600.0)
        assert len(log) == 4
        await air.fetch_pm25(LAT + 1e-5, LON, poll_s=3600, http=http, now=NOON + 3600.0)
        assert len(log) == 4                       # rounds to the same 3 dp
        await air.fetch_pm25(LAT + 1.0, LON, poll_s=3600, http=http, now=NOON + 3600.0)
        assert len(log) == 6


async def test_a_failure_is_cached_too_so_a_dead_api_is_not_retried_per_poll():
    log: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await air.fetch_pm25(LAT, LON, http=http, now=NOON) is None
        assert await air.fetch_pm25(LAT, LON, http=http, now=NOON + 30.0) is None
    assert len(log) == 1


async def test_clear_cache_drops_the_hour_and_the_last_reading():
    async with fake() as http:
        await air.fetch_pm25(LAT, LON, http=http, now=NOON)
    assert air.last_reading() is not None
    air.clear_cache()
    assert air.last_reading() is None


async def test_last_reading_keeps_the_newest_success_past_a_failure():
    """``/api/air/status`` must still show the last real number after an outage."""

    async with fake() as http:
        good = await air.fetch_pm25(LAT, LON, http=http, now=NOON)
    async with boom(httpx.ReadTimeout("slow")) as http:
        assert await air.fetch_pm25(LAT, LON, http=http, now=NOON + 3600.0) is None
    last = air.last_reading()
    assert last is not None
    assert last[0] == good
    assert last[1] == pytest.approx(NOON)


# -- every failure answers None, never a number ---------------------------


@pytest.mark.parametrize("kwargs", [
    {"locations": {"results": []}},                                  # nothing in range
    {"locations": {"results": [{"id": 1, "sensors": []}]}},          # no PM2.5 sensor
    {"locations": {"results": [{"id": 1, "sensors": [               # sensor, wrong parameter
        {"id": 9, "parameter": {"id": 1, "name": "pm10"}}]}]}},
    {"locations": {}},                                               # malformed body
    {"locations": {"results": [{"id": "not-an-int", "sensors": [
        {"id": 88, "parameter": {"id": 2, "name": "pm25"}}]}]}},     # unusable station id
    {"latest": {"results": []}},                                     # station reports nothing
    {"latest": {"results": [{"sensorsId": 88, "value": None}]}},      # null value
    {"latest": {"results": [{"sensorsId": 88, "value": "n/a"}]}},     # unparseable value
    {"latest": {"results": [{"sensorsId": 88, "value": -3.0}]}},      # negative PM2.5
    {"latest": {"results": [{"sensorsId": 88, "value": float("inf")}]}},
    {"latest": {"results": [{"sensorsId": 77, "value": 9.0}]}},       # only the PM10 sensor
    {"latest": {"results": ["not a dict"]}},
    {"latest": {"results": [{"sensorsId": 88, "value": 9.0,
                             "parameter": {"name": "no2"}}]}},        # row says it is NO2
    {"status": 401},
    {"status": 429},
    {"status": 500},
])
async def test_unusable_upstream_answers_none_and_never_raises(kwargs):
    async with fake(**kwargs) as http:
        assert await air.fetch_pm25(LAT, LON, http=http, now=NOON) is None


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.TooManyRedirects("loop"),
])
async def test_transport_failures_answer_none_and_never_raise(exc):
    async with boom(exc) as http:
        assert await air.fetch_pm25(LAT, LON, http=http, now=NOON) is None


async def test_a_body_that_is_not_json_answers_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>maintenance</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await air.fetch_pm25(LAT, LON, http=http, now=NOON) is None


# -- storage ---------------------------------------------------------------


async def test_poll_writes_one_row_the_adapter_can_read(db):
    async with fake() as http:
        reading = await air.poll_pm25(db, LAT, LON, api_key="k", now=NOON, http=http)
    assert reading is not None
    assert rows(db) == {"pm25": pytest.approx(12.6)}
    row = db.list_seeded(DAY, DAY)[0]
    assert (row.metric, row.unit, row.source) == ("pm25", air.PM25_UNIT, air.PM25_SOURCE)
    assert (air.PM25_UNIT, air.PM25_SOURCE) == ("ug/m3", "openaq")


async def test_poll_is_idempotent_within_the_hour(db):
    async with fake() as http:
        await air.poll_pm25(db, LAT, LON, now=NOON, http=http)
        await air.poll_pm25(db, LAT, LON, now=NOON + 5.0, http=http)
    assert len(db.list_seeded(DAY, DAY)) == 1


async def test_unset_coordinates_write_nothing_and_make_no_request(db):
    log: list[httpx.Request] = []
    async with fake(log=log) as http:
        for lat, lon in ((None, None), (LAT, None), (None, LON)):
            assert await air.poll_pm25(db, lat, lon, now=NOON, http=http) is None
    assert log == []
    assert db.list_seeded(DAY, DAY) == []


async def test_a_failed_poll_writes_no_row(db):
    async with boom(httpx.ConnectError("down")) as http:
        assert await air.poll_pm25(db, LAT, LON, now=NOON, http=http) is None
    assert db.list_seeded(DAY, DAY) == []


async def test_store_pm25_is_idempotent_on_the_day(db):
    air.store_pm25(db, DAY, air.AirReading(11.0, "A", 1, None))
    air.store_pm25(db, DAY, air.AirReading(13.5, "A", 1, None))
    assert rows(db) == {"pm25": pytest.approx(13.5)}
    assert len(db.list_seeded(DAY, DAY)) == 1
