"""Google Health API OAuth client, converters, and bounded poller."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://health.googleapis.com/v4"
SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
)
log = logging.getLogger(__name__)
_probed: set[str] = set()


@dataclass(slots=True)
class GoogleHealthConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8010/api/wearables/google-health/callback"
    token_path: str | Path = Path("./data/google_health_token.json")
    poll_s: float = 300

    @classmethod
    def from_env(cls) -> "GoogleHealthConfig":
        load_dotenv(".env"); load_dotenv("backend/.env")
        d = cls()
        return cls(os.getenv("GOOGLE_HEALTH_CLIENT_ID", ""), os.getenv("GOOGLE_HEALTH_CLIENT_SECRET", ""),
                   os.getenv("GOOGLE_HEALTH_REDIRECT_URI", d.redirect_uri),
                   Path(os.getenv("GOOGLE_HEALTH_TOKEN_PATH", str(d.token_path))),
                   float(os.getenv("GOOGLE_HEALTH_POLL_S", "300")))


class TokenStore:
    def __init__(self, path: str | Path): self.path = Path(path).expanduser()
    def load(self) -> dict[str, Any] | None:
        try: return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError): return None
    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(token, separators=(",", ":")))
        os.chmod(tmp, 0o600); os.replace(tmp, self.path); os.chmod(self.path, 0o600)
    def is_configured(self) -> bool: return bool(self.load())


def code_verifier() -> str: return secrets.token_urlsafe(64)[:96]
def code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class RateLimitError(RuntimeError):
    def __init__(self, retry_after: float):
        super().__init__("Google Health rate limit reached"); self.retry_after = retry_after


class GoogleHealthClient:
    def __init__(self, config: GoogleHealthConfig, store: TokenStore, http: httpx.AsyncClient | None = None):
        self.config, self.store = config, store
        self.http = http or httpx.AsyncClient(timeout=20)
        self._requests: deque[float] = deque()

    @property
    def requests_last_hour(self) -> int:
        now = time.time()
        while self._requests and self._requests[0] < now - 3600: self._requests.popleft()
        return len(self._requests)

    def authorize_url(self, state: str) -> tuple[str, str]:
        verifier = code_verifier()
        query = urlencode({"client_id": self.config.client_id, "redirect_uri": self.config.redirect_uri,
                           "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline",
                           "prompt": "consent", "state": state, "code_challenge": code_challenge(verifier),
                           "code_challenge_method": "S256"})
        return f"{AUTH_URL}?{query}", verifier

    async def _token(self, form: dict[str, str], old_refresh: str | None = None) -> dict[str, Any]:
        response = await self.http.post(TOKEN_URL, data=form); response.raise_for_status()
        token = response.json()
        if old_refresh and not token.get("refresh_token"): token["refresh_token"] = old_refresh
        token["expires_at"] = time.time() + float(token.get("expires_in", 3600))
        self.store.save(token); return token

    async def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        return await self._token({"code": code, "client_id": self.config.client_id,
            "client_secret": self.config.client_secret, "redirect_uri": self.config.redirect_uri,
            "grant_type": "authorization_code", "code_verifier": verifier})

    async def refresh_if_needed(self, *, force: bool = False) -> dict[str, Any]:
        token = self.store.load()
        if not token: raise RuntimeError("Google Health is not connected")
        if force or float(token.get("expires_at", 0)) < time.time() + 300:
            refresh = token.get("refresh_token")
            if not refresh: raise RuntimeError("Google Health refresh token is missing")
            token = await self._token({"grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": self.config.client_id, "client_secret": self.config.client_secret}, refresh)
        return token

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self.refresh_if_needed()
        for attempt in range(2):
            self._requests.append(time.time())
            response = await self.http.get(f"{API_URL}{path}", params=params,
                                           headers={"Authorization": f"Bearer {token['access_token']}"})
            if response.status_code == 401 and attempt == 0:
                token = await self.refresh_if_needed(force=True); continue
            if response.status_code == 429:
                try: delay = float(response.headers.get("Retry-After", "60"))
                except ValueError: delay = 60
                raise RateLimitError(delay)
            response.raise_for_status(); return response.json()
        raise RuntimeError("Google Health authorization failed after token refresh")

    async def paginate(self, data_type: str, start: datetime | str, end: datetime | str) -> AsyncIterator[dict[str, Any]]:
        fmt = lambda value: value if isinstance(value, str) else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        # The list method takes the time range as an AIP-160 ``filter``; bare
        # startTime/endTime query params are rejected with 400 (verified live).
        # Field prefix is the data type in snake_case; the time field depends
        # on the data type's shape: sample / interval / daily summary.
        snake = data_type.replace("-", "_")
        if data_type.startswith("daily-"):
            day = lambda v: fmt(v)[:10]
            end_dt = end if isinstance(end, datetime) else datetime.fromisoformat(end.replace("Z", "+00:00"))
            next_day = (end_dt.astimezone(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
            # Only >= and < are accepted on date fields (<= is a 400, verified live).
            flt = f'{snake}.date >= "{day(start)}" AND {snake}.date < "{next_day}"'
        elif data_type in {"sleep", "exercise"}:
            # These reject every time filter member (verified live); the
            # unfiltered list returns the most recent records, newest first.
            # Callers filter by interval.startTime client-side.
            flt = None
        elif data_type in {"steps", "sedentary-period", "active-minutes"}:
            flt = f'{snake}.interval.start_time >= "{fmt(start)}" AND {snake}.interval.start_time < "{fmt(end)}"'
        else:
            flt = f'{snake}.sample_time.physical_time >= "{fmt(start)}" AND {snake}.sample_time.physical_time < "{fmt(end)}"'
        params: dict[str, Any] = {"pageSize": 25 if data_type in {"sleep", "exercise"} else 1000}
        if flt:
            params["filter"] = flt
        while True:
            payload = await self.get(f"/users/me/dataTypes/{data_type}/dataPoints", params)
            for point in payload.get("dataPoints", []): yield point
            token = payload.get("nextPageToken")
            if not token or flt is None: break  # unfiltered kinds: one page (newest 25) is enough
            params["pageToken"] = token


def _sample(t: float, metric: str, value: Any, unit: str, model: str | None = None) -> dict[str, Any]:
    row = {"t": t, "metric": metric, "value": value, "unit": unit}
    if model: row["source_model"] = model
    return row
def _daily(day: str, metric: str, value: Any, unit: str, model: str | None = None) -> dict[str, Any]:
    row = {"day": day, "metric": metric, "value": value, "unit": unit, "source": "fitbit"}
    if model: row["source_model"] = model
    return row
def _model(point: dict) -> str | None: return point.get("dataSource", {}).get("device", {}).get("model")
def _ts(value: str) -> float: return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
def _day(value: dict) -> str | None:
    try: return date(int(value["year"]), int(value["month"]), int(value["day"])).isoformat()
    except (KeyError, TypeError, ValueError): return None
def _number(obj: Any, hints: tuple[str, ...]) -> float | None:
    if not isinstance(obj, dict): return None
    for hint in hints:
        for key, val in obj.items():
            if hint.lower() in key.lower() and isinstance(val, (int, float, str)):
                try: return float(val)
                except ValueError: pass
    for val in obj.values():
        found = _number(val, hints)
        if found is not None: return found
    return None
def _probe(kind: str, obj: dict, hints: tuple[str, ...]) -> float | None:
    if kind not in _probed:
        log.debug("Google Health %s fields: %s", kind, sorted(obj)); _probed.add(kind)
    return _number(obj, hints)


def heart_rate_to_payload(point: dict) -> dict:
    value = point.get("heartRate", {}); stamp = value.get("sampleTime", {}).get("physicalTime")
    rows = [_sample(_ts(stamp), "heart_rate", int(value["beatsPerMinute"]), "bpm", _model(point))] if stamp and value.get("beatsPerMinute") is not None else []
    return {"device": "fitbit", "samples": rows, "daily": []}
def hrv_to_payload(point: dict) -> dict:
    value = point.get("heartRateVariability", {}); stamp = value.get("sampleTime", {}).get("physicalTime")
    val = value.get("rootMeanSquareOfSuccessiveDifferencesMilliseconds")
    return {"device": "fitbit", "samples": [_sample(_ts(stamp), "hrv_rmssd", val, "ms", _model(point))] if stamp and val is not None else [], "daily": []}
def oxygen_saturation_to_payload(point: dict) -> dict:
    value = point.get("oxygenSaturation", {}); stamp = value.get("sampleTime", {}).get("physicalTime")
    return {"device": "fitbit", "samples": [_sample(_ts(stamp), "spo2", value["percentage"], "%", _model(point))] if stamp and value.get("percentage") is not None else [], "daily": []}
def steps_to_payload(point: dict) -> dict:
    value = point.get("steps", {}); stamp = value.get("interval", {}).get("startTime")
    return {"device": "fitbit", "samples": [_sample(_ts(stamp), "steps_delta", int(value["count"]), "steps", _model(point))] if stamp and value.get("count") is not None else [], "daily": []}


_DAILY = {
    "daily-resting-heart-rate": ("dailyRestingHeartRate", "resting_hr", "bpm", ("beatsPerMinute",)),
    "daily-heart-rate-variability": ("dailyHeartRateVariability", "hrv_rmssd_ms", "ms", ("rootMeanSquare", "rmssd")),
    "daily-respiratory-rate": ("dailyRespiratoryRate", "respiratory_rate", "breaths/min", ("breathsPerMinute",)),
    "daily-sleep-temperature-derivations": ("dailySleepTemperatureDerivations", "skin_temp_c", "°C", ("nightlyTemperatureCelsius",)),
    "daily-oxygen-saturation": ("dailyOxygenSaturation", "spo2", "%", ("average", "percentage")),
}
def daily_to_payload(data_type: str, point: dict) -> dict:
    field, metric, unit, hints = _DAILY[data_type]; obj = point.get(field, {}); day = _day(obj.get("date", {}))
    value = _probe(data_type, obj, hints)
    return {"device": "fitbit", "samples": [], "daily": [_daily(day, metric, value, unit, _model(point))] if day and value is not None else []}


def _offset_dt(text: str, offset: str | None) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if offset:
        sign = -1 if offset.startswith("-") else 1; hh, mm = map(int, offset[1:].split(":"))
        dt = dt.astimezone(timezone(sign * timedelta(hours=hh, minutes=mm)))
    return dt
def sleep_to_payload(points: list[dict] | dict) -> dict:
    if isinstance(points, dict): points = [points]
    choices = []
    for point in points:
        obj = point.get("sleep", {}); interval = obj.get("interval", {})
        if interval.get("startTime") and interval.get("endTime"):
            choices.append(( _ts(interval["endTime"]) - _ts(interval["startTime"]), point, obj, interval))
    if not choices: return {"device": "fitbit", "samples": [], "daily": []}
    duration, point, obj, interval = max(choices, key=lambda x: x[0])
    start = _offset_dt(interval["startTime"], interval.get("startUtcOffset")); end = _offset_dt(interval["endTime"], interval.get("endUtcOffset"))
    totals = {"deep_min": 0.0, "rem_min": 0.0, "light_min": 0.0, "awake_min": 0.0}
    for stage in obj.get("stages", []):
        name = str(stage.get("type", "")).lower()
        metric = next((m for needle, m in (("deep", "deep_min"), ("rem", "rem_min"), ("light", "light_min"), ("awake", "awake_min"), ("wake", "awake_min")) if needle in name), None)
        if metric and stage.get("startTime") and stage.get("endTime"):
            totals[metric] += (_ts(stage["endTime"]) - _ts(stage["startTime"])) / 60
    day = end.date().isoformat(); model = _model(point)
    vals = [("sleep_hours", duration / 3600, "hours"), *[(k, v, "min") for k, v in totals.items()],
            ("bed_time", start.hour + start.minute / 60 + start.second / 3600, "hour"),
            ("wake_time", end.hour + end.minute / 60 + end.second / 3600, "hour")]
    return {"device": "fitbit", "samples": [], "daily": [_daily(day, *v, model) for v in vals]}


def exercise_to_payload(points: list[dict] | dict) -> dict:
    if isinstance(points, dict): points = [points]
    by_day: dict[str, tuple[dict, dict]] = {}
    for point in points:
        obj = point.get("exercise", {}); start = obj.get("interval", {}).get("startTime")
        if start: by_day[datetime.fromisoformat(start.replace("Z", "+00:00")).date().isoformat()] = (point, obj)
    daily = []
    for day, (point, obj) in by_day.items():
        interval, summary, model = obj.get("interval", {}), obj.get("metricsSummary", {}), _model(point)
        distance = summary.get("distanceMillimeters"); hr = summary.get("averageHeartRateBeatsPerMinute")
        duration = (_ts(interval["endTime"]) - _ts(interval["startTime"])) / 60 if interval.get("endTime") else None
        for metric, val, unit in (("workout_km", float(distance) / 1_000_000 if distance is not None else None, "km"),
                                  ("workout_avg_hr", float(hr) if hr is not None else None, "bpm"), ("workout_minutes", duration, "min")):
            if val is not None: daily.append(_daily(day, metric, val, unit, model))
    return {"device": "fitbit", "samples": [], "daily": daily}


def merge(payloads: list[dict]) -> dict:
    return {"device": "fitbit", "samples": [r for p in payloads for r in p.get("samples", [])],
            "daily": [r for p in payloads for r in p.get("daily", [])]}


class GoogleHealthSync:
    def __init__(self, client: GoogleHealthClient, sink: Callable[[dict], Any], interval_s: float = 300):
        self.client, self.sink, self.interval_s = client, sink, interval_s
        self.last_sync_t: float | None = None; self.last_error: str | None = None
        self.connected = client.store.is_configured()
    @property
    def requests_last_hour(self) -> int: return self.client.requests_last_hour

    async def sync_once(self, now: datetime | None = None) -> dict[str, int]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = datetime.fromtimestamp(self.last_sync_t, timezone.utc) - timedelta(seconds=120) if self.last_sync_t else now - timedelta(hours=6)
        days_start = datetime.combine(now.date() - timedelta(days=1), datetime.min.time(), timezone.utc)
        payloads: list[dict] = []; before = self.client.requests_last_hour
        try:
            failures: list[str] = []

            async def pull(kind: str, since: datetime) -> list[dict[str, Any]]:
                # One data type failing (a filter the API rejects, a scope not
                # granted) must not abort the whole sync.
                try:
                    return [p async for p in self.client.paginate(kind, since, now)]
                except RateLimitError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{kind}: {str(exc)[:160]}")
                    log.warning("google health: %s failed: %s", kind, str(exc)[:200])
                    return []

            for kind, converter in (("heart-rate", heart_rate_to_payload), ("heart-rate-variability", hrv_to_payload),
                                    ("oxygen-saturation", oxygen_saturation_to_payload), ("steps", steps_to_payload)):
                payloads.extend(converter(p) for p in await pull(kind, start))
            for kind in _DAILY:
                payloads.extend(daily_to_payload(kind, p) for p in await pull(kind, days_start))
            def _since(points: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
                keep = []
                for p in points:
                    st = (p.get(key) or {}).get("interval", {}).get("startTime")
                    try:
                        if st and _ts(st) >= days_start.timestamp():
                            keep.append(p)
                    except Exception:  # noqa: BLE001
                        continue
                return keep

            sleeps = _since(await pull("sleep", days_start), "sleep")
            exercises = _since(await pull("exercise", days_start), "exercise")
            # Longest sleep over the two-night window; exercises retain the last item per day.
            payloads.extend((sleep_to_payload(sleeps), exercise_to_payload(exercises)))
            result = merge(payloads); sent = self.sink(result)
            if inspect.isawaitable(sent): await sent
            self.last_sync_t, self.connected = now.timestamp(), True
            self.last_error = ("partial: " + "; ".join(failures)) if failures else None
            return {"samples": len(result["samples"]), "daily": len(result["daily"]),
                    "requests_used": self.client.requests_last_hour - before}
        except Exception as exc:
            self.last_error = str(exc); raise

    async def run_forever(self) -> None:
        backoff = self.interval_s
        while True:
            try:
                await self.sync_once(); backoff = self.interval_s; await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError: raise
            except RateLimitError as exc: await asyncio.sleep(max(exc.retry_after, self.interval_s))
            except Exception:
                await asyncio.sleep(backoff); backoff = min(backoff * 2, 1800)
