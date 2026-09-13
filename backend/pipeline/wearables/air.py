"""OpenAQ v3 air-quality client: today's PM2.5 at the wearer's coordinates.

``pm25`` is the one environment factor the glasses cannot see and the wearable
does not carry, so it comes off a public network of reference monitors
(:data:`API_URL`). One fetch answers two questions in sequence:

1. ``GET /v3/locations?coordinates=lat,lon&radius=...&parameters_id=2`` -- the
   stations within :data:`DEFAULT_RADIUS_M` that actually report PM2.5, nearest
   first, and each station's ``sensors[]`` so the PM2.5 sensor id is known;
2. ``GET /v3/locations/{id}/latest`` -- that station's newest value per sensor,
   matched back by ``sensorsId``.

Honesty rules, in the same spirit as ``scoring/healthspan.py``:

* **Nothing is invented.** Every failure path -- no key, no station in range, a
  timeout, a 4xx/5xx, a malformed body, a value that is not a finite number --
  returns ``None``. The caller writes no row, the adapter reports ``pm25``
  unmeasured, and the engine imputes it at the population reference where it
  earns nothing.
* **Never raises into a request path.** :func:`fetch_pm25` catches
  ``httpx.HTTPError``, ``ValueError``, ``KeyError`` and ``TypeError`` and logs
  at warning level; a poll storm on ``/api/air/status`` cannot 500 the route.
* **One request an hour.** :class:`_Cache` is keyed by rounded coordinates and
  the wall-clock hour, so N callers inside one hour share one upstream call.
  A failure is cached too (as ``None``) -- a dead network must not be retried
  once per poll.
* **Unset coordinates are the default**, not an error: with no ``AIR_LAT`` /
  ``AIR_LON`` the product simply has no air layer.

The daily row is written through :func:`store_pm25` as seeded metric ``pm25``
(unit ``ug/m3``, source ``openaq``), so the scorer and the adapter read it the
same way as every other integration row rather than through a second path.

Async and side-effect-free apart from that one insert; the only clock read is
:func:`time.time` for the cache bucket.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..db import Database, day_key
from ..models import SeededRow

__all__ = [
    "API_URL",
    "DEFAULT_RADIUS_M",
    "PM25_PARAMETER_ID",
    "PM25_SOURCE",
    "PM25_UNIT",
    "AirReading",
    "clear_cache",
    "fetch_pm25",
    "last_reading",
    "poll_pm25",
    "store_pm25",
]

log = logging.getLogger(__name__)

#: OpenAQ v3 root. Only two GETs are ever issued against it.
API_URL = "https://api.openaq.org/v3"
#: OpenAQ's own id for PM2.5 in µg/m³ (``GET /v3/parameters``).
PM25_PARAMETER_ID = 2
#: Search radius for the nearest reporting station, metres. The v3 maximum is
#: 25 000; a station further away than 25 km is not this wearer's air.
DEFAULT_RADIUS_M = 25_000
#: Seconds before a station search or a latest read is abandoned.
TIMEOUT_S = 10.0
#: Decimal places the cache key rounds coordinates to (~11 m at the equator).
CACHE_PRECISION = 3
#: Unit and provenance of the seeded row, so the adapter can assert both.
PM25_UNIT = "ug/m3"
PM25_SOURCE = "openaq"


@dataclass(frozen=True, slots=True)
class AirReading:
    """One station's newest PM2.5 value."""

    #: µg/m³, always finite and >= 0.
    value: float
    #: OpenAQ station name, for the status route's ``source`` line.
    station: str
    #: OpenAQ ``locations_id``.
    station_id: int
    #: ISO-8601 UTC stamp the station reported, verbatim; ``None`` if absent.
    measured_at: str | None


# -- cache ------------------------------------------------------------------


@dataclass
class _Cache:
    """One slot: the last ``(key, reading)`` pair and when it was fetched.

    Deliberately a single slot rather than a dict -- one wearer has one pair of
    coordinates, and an unbounded dict keyed by client input is a leak.
    """

    key: tuple[float, float, int] | None = None
    reading: AirReading | None = None
    fetched_t: float | None = None


_cache = _Cache()
#: The newest successful reading, whatever hour it came from (``/api/air/status``).
_last: tuple[AirReading, float] | None = None


def clear_cache() -> None:
    """Drop the cached hour and the last reading. For tests and for a re-config."""

    global _last
    _cache.key = None
    _cache.reading = None
    _cache.fetched_t = None
    _last = None


def last_reading() -> tuple[AirReading, float] | None:
    """The newest successful reading and the wall time it was fetched, or ``None``."""

    return _last


def _cache_key(lat: float, lon: float, now: float, poll_s: int) -> tuple[float, float, int]:
    """Rounded coordinates plus the poll bucket ``now`` falls in."""

    bucket = int(now // max(1, poll_s))
    return (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION), bucket)


# -- parsing ----------------------------------------------------------------


def _finite(value: Any) -> float | None:
    """A finite, non-negative float, or ``None``. Strings are accepted (v3 sends both)."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _pm25_sensor_ids(location: dict[str, Any]) -> set[int]:
    """The ``sensors[].id`` values on ``location`` that measure PM2.5."""

    ids: set[int] = set()
    for sensor in location.get("sensors") or []:
        if not isinstance(sensor, dict):
            continue
        parameter = sensor.get("parameter") or {}
        name = str(parameter.get("name", "")).lower()
        if parameter.get("id") == PM25_PARAMETER_ID or name == "pm25":
            try:
                ids.add(int(sensor["id"]))
            except (KeyError, TypeError, ValueError):
                continue
    return ids


def _pick_latest(results: list[Any], sensor_ids: set[int]) -> tuple[float, str | None] | None:
    """The newest PM2.5 value among ``results``, as ``(value, measured_at)``.

    Prefers a row whose ``sensorsId`` is one of the station's PM2.5 sensors.
    When a row carries its own ``parameter`` block instead (the shape differs
    between v3 resources), a ``pm25`` name is accepted as well -- but a row
    that identifies as some other parameter is never read as PM2.5.
    """

    best: tuple[str, float, str | None] | None = None
    for row in results:
        if not isinstance(row, dict):
            continue
        parameter = row.get("parameter") or {}
        named = str(parameter.get("name", "")).lower() if isinstance(parameter, dict) else ""
        by_sensor = row.get("sensorsId") in sensor_ids
        if not by_sensor and named != "pm25":
            continue
        if named and named != "pm25":
            continue
        value = _finite(row.get("value"))
        if value is None:
            continue
        stamp = row.get("datetime")
        utc = stamp.get("utc") if isinstance(stamp, dict) else None
        measured_at = str(utc) if utc else None
        order = measured_at or ""
        if best is None or order > best[0]:
            best = (order, value, measured_at)
    return None if best is None else (best[1], best[2])


# -- fetch ------------------------------------------------------------------


async def _get(http: httpx.AsyncClient, path: str, key: str | None,
               params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"X-API-Key": key} if key else {}
    response = await http.get(f"{API_URL}{path}", params=params, headers=headers,
                              timeout=TIMEOUT_S)
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


async def _nearest_station(http: httpx.AsyncClient, lat: float, lon: float,
                           key: str | None, radius_m: int) -> tuple[dict[str, Any], set[int]] | None:
    """The nearest station reporting PM2.5 and its PM2.5 sensor ids.

    v3 orders a coordinate search by distance, so the first station with a
    PM2.5 sensor is the nearest one; ``parameters_id`` already filters the rest
    out, and the loop only guards against a station whose ``sensors`` block
    does not actually name PM2.5.
    """

    body = await _get(http, "/locations", key, {
        "coordinates": f"{lat:.4f},{lon:.4f}",
        "radius": max(1, min(25_000, int(radius_m))),
        "parameters_id": PM25_PARAMETER_ID,
        "limit": 10,
    })
    for location in body.get("results") or []:
        if not isinstance(location, dict):
            continue
        sensor_ids = _pm25_sensor_ids(location)
        if sensor_ids:
            return location, sensor_ids
    return None


async def fetch_pm25(lat: float, lon: float, *, api_key: str | None = None,
                     poll_s: int = 3600, radius_m: int = DEFAULT_RADIUS_M,
                     http: httpx.AsyncClient | None = None,
                     now: float | None = None) -> AirReading | None:
    """Today's PM2.5 at ``lat``/``lon``, or ``None`` when it cannot be measured.

    Cached per ``(rounded lat, rounded lon, poll bucket)``, failures included,
    so a dashboard polling every second makes one upstream request per
    ``poll_s``. Never raises: every upstream or parsing problem is logged and
    answered with ``None``.
    """

    global _last
    moment = time.time() if now is None else now
    key = _cache_key(lat, lon, moment, poll_s)
    if _cache.key == key:
        return _cache.reading

    reading: AirReading | None = None
    client = http or httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        found = await _nearest_station(client, lat, lon, api_key, radius_m)
        if found is None:
            log.warning("OpenAQ: no PM2.5 station within %d m of %.4f,%.4f", radius_m, lat, lon)
        else:
            station, sensor_ids = found
            station_id = int(station["id"])
            body = await _get(client, f"/locations/{station_id}/latest", api_key)
            picked = _pick_latest(list(body.get("results") or []), sensor_ids)
            if picked is None:
                log.warning("OpenAQ: station %d reported no usable PM2.5 value", station_id)
            else:
                value, measured_at = picked
                reading = AirReading(value, str(station.get("name") or f"station {station_id}"),
                                     station_id, measured_at)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        # Air is one factor of twenty; a dead public API must cost the request
        # that factor and nothing else.
        log.warning("OpenAQ PM2.5 fetch failed: %s", exc)
    finally:
        if http is None:
            await client.aclose()

    _cache.key = key
    _cache.reading = reading
    _cache.fetched_t = moment
    if reading is not None:
        _last = (reading, moment)
    return reading


# -- storage ----------------------------------------------------------------


def store_pm25(db: Database, day: str, reading: AirReading) -> int:
    """Write ``reading`` as the seeded ``pm25`` row for ``day``. Idempotent."""

    return db.insert_seeded_rows([SeededRow(
        day=day, metric="pm25", value=float(reading.value),
        unit=PM25_UNIT, source=PM25_SOURCE,
    )])


async def poll_pm25(db: Database, lat: float | None, lon: float | None, *,
                    api_key: str | None = None, poll_s: int = 3600,
                    now: float | None = None,
                    http: httpx.AsyncClient | None = None) -> AirReading | None:
    """Fetch today's PM2.5 and store it. ``None`` (and no row) when unconfigured.

    ``day`` is the local day of ``now``, the same :func:`~pipeline.db.day_key`
    convention every other daily row uses.
    """

    if lat is None or lon is None:
        return None
    moment = time.time() if now is None else now
    reading = await fetch_pm25(lat, lon, api_key=api_key, poll_s=poll_s, http=http, now=moment)
    if reading is not None:
        store_pm25(db, day_key(moment), reading)
    return reading
