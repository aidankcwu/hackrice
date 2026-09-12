"""Fitbit Web API client and canonical wearable payload conversion.

Fitbit data appears only after the tracker syncs through the phone app, normally
with several minutes of lag.  Thus ``sync_once`` is near-live, not streaming.
This module deliberately knows nothing about the database: callers inject a sink.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

AUTH_URL = "https://www.fitbit.com/oauth2/authorize"
API_URL = "https://api.fitbit.com"
SCOPES = "heartrate sleep oxygen_saturation respiratory_rate temperature cardio_fitness activity profile settings"


@dataclass(slots=True)
class FitbitConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:8010/api/wearables/fitbit/callback"
    token_path: str = "./data/fitbit_token.json"
    poll_s: float = 300

    @classmethod
    def from_env(cls) -> "FitbitConfig":
        # Works from the repo root or from backend/ (Settings reads ./.env).
        load_dotenv(".env"); load_dotenv("backend/.env")
        defaults = cls()
        return cls(os.getenv("FITBIT_CLIENT_ID", ""), os.getenv("FITBIT_CLIENT_SECRET", ""),
                   os.getenv("FITBIT_REDIRECT_URI", defaults.redirect_uri),
                   os.getenv("FITBIT_TOKEN_PATH", defaults.token_path),
                   float(os.getenv("FITBIT_POLL_S", "300")))


class TokenStore:
    def __init__(self, path: str | Path): self.path = Path(path).expanduser()
    def load(self) -> dict[str, Any] | None:
        try: return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError): return None
    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(token, separators=(",", ":")))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
    def is_configured(self) -> bool: return bool(self.load())


def code_verifier() -> str: return secrets.token_urlsafe(64)[:96]
def code_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class RateLimitError(RuntimeError):
    def __init__(self, retry_after: float):
        super().__init__("Fitbit rate limit reached")
        self.retry_after = retry_after


class FitbitClient:
    def __init__(self, config: FitbitConfig, store: TokenStore, http: httpx.AsyncClient | None = None):
        self.config, self.store = config, store
        self.http = http or httpx.AsyncClient(base_url=API_URL, timeout=20)
        self._timezone: str | None = None
        self._requests: deque[float] = deque()

    @property
    def requests_last_hour(self) -> int:
        now = time.time()
        while self._requests and self._requests[0] < now - 3600: self._requests.popleft()
        return len(self._requests)

    def authorize_url(self, state: str) -> tuple[str, str]:
        verifier = code_verifier()
        query = urlencode({"response_type": "code", "client_id": self.config.client_id,
                           "redirect_uri": self.config.redirect_uri, "scope": SCOPES,
                           "code_challenge": code_challenge(verifier),
                           "code_challenge_method": "S256", "state": state})
        return f"{AUTH_URL}?{query}", verifier

    def _basic(self) -> str:
        raw = f"{self.config.client_id}:{self.config.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    async def _token(self, form: dict[str, str]) -> dict[str, Any]:
        form = {"client_id": self.config.client_id, **form}
        response = await self.http.post(f"{API_URL}/oauth2/token", data=form,
                                        headers={"Authorization": self._basic()})
        response.raise_for_status()
        token = response.json()
        token["expires_at"] = time.time() + float(token.get("expires_in", 28800))
        self.store.save(token)
        return token

    async def exchange_code(self, code: str, verifier: str) -> dict[str, Any]:
        return await self._token({"grant_type": "authorization_code", "code": code,
                                  "code_verifier": verifier, "redirect_uri": self.config.redirect_uri})

    async def refresh_if_needed(self, *, force: bool = False) -> dict[str, Any]:
        token = self.store.load()
        if not token: raise RuntimeError("Fitbit is not connected")
        if force or float(token.get("expires_at", 0)) < time.time() + 600:
            refresh = token.get("refresh_token")
            if not refresh: raise RuntimeError("Fitbit refresh token is missing")
            token = await self._token({"grant_type": "refresh_token", "refresh_token": refresh})
        return token

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self.refresh_if_needed()
        for attempt in range(2):
            self._requests.append(time.time())
            response = await self.http.get(f"{API_URL}{path}", params=params,
                                           headers={"Authorization": f"Bearer {token['access_token']}"})
            if response.status_code == 401 and attempt == 0:
                token = await self.refresh_if_needed(force=True)
                continue
            if response.status_code == 429:
                raise RateLimitError(float(response.headers.get("Retry-After", "60")))
            response.raise_for_status()
            return response.json()
        raise RuntimeError("Fitbit authorization failed after token refresh")

    async def profile_timezone(self) -> str:
        if self._timezone is None:
            self._timezone = (await self.get("/1/user/-/profile.json")).get("user", {}).get("timezone", "UTC")
        return self._timezone


def _timestamp(value: str, tz: str, day: str | None = None) -> float:
    text = f"{day}T{value}" if day and "T" not in value else value
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.timestamp()

def _sample(t: float, metric: str, value: Any, unit: str) -> dict[str, Any]:
    return {"t": t, "metric": metric, "value": value, "unit": unit}
def _daily(day: str, metric: str, value: Any, unit: str) -> dict[str, Any]:
    return {"day": day, "metric": metric, "value": value, "unit": unit, "source": "fitbit"}

def heart_intraday_to_samples(day: str, payload: dict, tz: str) -> dict:
    rows = [_sample(_timestamp(x["time"], tz, day), "heart_rate", x["value"], "bpm")
            for x in payload.get("activities-heart-intraday", {}).get("dataset", [])]
    daily = []
    for x in payload.get("activities-heart", []):
        value = x.get("value", {}).get("restingHeartRate")
        if value is not None: daily.append(_daily(x.get("dateTime", day), "resting_hr", value, "bpm"))
    return {"device": "fitbit", "samples": rows, "daily": daily}

def hrv_to_samples(day: str, payload: dict, tz: str) -> dict:
    samples, daily = [], []
    for row in payload.get("hrv", []):
        for minute in row.get("minutes", []):
            samples.append(_sample(_timestamp(minute["minute"], tz), "hrv_rmssd", minute["value"]["rmssd"], "ms"))
        val = row.get("value", {}).get("dailyRmssd")
        if val is not None: daily.append(_daily(row.get("dateTime", day), "hrv_rmssd_ms", val, "ms"))
    return {"device": "fitbit", "samples": samples, "daily": daily}

def spo2_to_samples(day: str, payload: dict, tz: str) -> dict:
    samples = [_sample(_timestamp(x["minute"], tz), "spo2", x["value"], "%") for x in payload.get("minutes", [])]
    val = payload.get("value", {}).get("avg")
    daily = [_daily(payload.get("dateTime", day), "spo2", val, "%")] if val is not None else []
    return {"device": "fitbit", "samples": samples, "daily": daily}

def br_to_daily(day: str, payload: dict, tz: str = "UTC") -> dict:
    out = []
    for row in payload.get("br", []):
        val = row.get("value", {}).get("fullSleepSummary", {}).get("breathingRate")
        if val is not None: out.append(_daily(row.get("dateTime", day), "respiratory_rate", val, "breaths/min"))
    return {"device": "fitbit", "samples": [], "daily": out}

def temp_to_daily(day: str, payload: dict, tz: str = "UTC") -> dict:
    out = [_daily(x.get("dateTime", day), "skin_temp_dev", x["value"]["nightlyRelative"], "°C")
           for x in payload.get("tempSkin", []) if x.get("value", {}).get("nightlyRelative") is not None]
    return {"device": "fitbit", "samples": [], "daily": out}

def steps_to_samples(day: str, payload: dict, tz: str, metric: str = "steps_delta") -> dict:
    key = "activities-calories-intraday" if metric == "active_energy" else "activities-steps-intraday"
    unit = "kcal" if metric == "active_energy" else "steps"
    return {"device": "fitbit", "samples": [_sample(_timestamp(x["time"], tz, day), metric, x["value"], unit)
            for x in payload.get(key, {}).get("dataset", [])], "daily": []}

def sleep_to_daily(day: str, payload: dict, tz: str) -> dict:
    sleeps = payload.get("sleep", [])
    main = next((x for x in sleeps if x.get("isMainSleep")), None)
    if not main: return {"device": "fitbit", "samples": [], "daily": []}
    d = str(main.get("dateOfSleep", day)); summary = main.get("levels", {}).get("summary", {})
    vals = [("sleep_hours", main.get("minutesAsleep", 0) / 60, "hours"),
            ("deep_min", summary.get("deep", {}).get("minutes", 0), "min"),
            ("rem_min", summary.get("rem", {}).get("minutes", 0), "min"),
            ("light_min", summary.get("light", {}).get("minutes", 0), "min"),
            ("awake_min", summary.get("wake", {}).get("minutes", 0), "min"),
            ("sleep_efficiency", main.get("efficiency"), "%")]
    for metric, field in (("bed_time", "startTime"), ("wake_time", "endTime")):
        dt = datetime.fromisoformat(main[field].replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo(tz))
        dt = dt.astimezone(ZoneInfo(tz)); vals.append((metric, dt.hour + dt.minute / 60 + dt.second / 3600, "hour"))
    return {"device": "fitbit", "samples": [], "daily": [_daily(d, *v) for v in vals if v[1] is not None]}

def activities_to_daily(day: str, payload: dict, tz: str = "UTC") -> dict:
    acts = payload.get("activities", [])
    daily = []
    if acts:
        daily.extend([_daily(day, "steps", sum(float(a.get("steps", 0)) for a in acts), "steps"),
                      _daily(day, "active_minutes", sum(float(a.get("duration", 0)) for a in acts) / 60000, "min")])
        last = acts[-1]
        for metric, value, unit in (("workout_km", last.get("distance"), "km"),
                                    ("workout_avg_hr", last.get("averageHeartRate"), "bpm"),
                                    ("workout_minutes", float(last.get("duration", 0)) / 60000, "min")):
            if value is not None: daily.append(_daily(day, metric, value, unit))
    return {"device": "fitbit", "samples": [], "daily": daily}

def cardio_to_daily(day: str, payload: dict, tz: str = "UTC") -> dict:
    out = []
    for row in payload.get("cardioScore", []):
        raw = row.get("value", {}).get("vo2Max")
        if raw is not None:
            parts = [float(v) for v in str(raw).split("-")]
            out.append(_daily(row.get("dateTime", day), "vo2_max", sum(parts) / len(parts), "mL/kg/min"))
    return {"device": "fitbit", "samples": [], "daily": out}

def merge(payloads: list[dict]) -> dict:
    return {"device": "fitbit", "samples": [x for p in payloads for x in p.get("samples", [])],
            "daily": [x for p in payloads for x in p.get("daily", [])]}


class FitbitSync:
    def __init__(self, client: FitbitClient, sink: Callable[[dict], Any], interval_s: float = 300):
        self.client, self.sink, self.interval_s = client, sink, interval_s
        self.last_sync_t: float | None = None
        self.last_error: str | None = None
        self.connected = client.store.is_configured()

    @property
    def requests_last_hour(self) -> int: return self.client.requests_last_hour

    async def sync_once(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        tz_name = await self.client.profile_timezone()
        local = now.astimezone(ZoneInfo(tz_name)); today = local.date().isoformat()
        start_dt = datetime.fromtimestamp(max(self.last_sync_t or local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),
                                             local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()), ZoneInfo(tz_name)) - timedelta(seconds=120)
        start = max(start_dt, local.replace(hour=0, minute=0, second=0, microsecond=0)).strftime("%H:%M")
        end = local.strftime("%H:%M")
        yesterday = (local.date() - timedelta(days=1)).isoformat()
        specs = [
            (f"/1/user/-/activities/heart/date/{today}/1d/1sec/time/{start}/{end}.json", heart_intraday_to_samples, today),
            (f"/1/user/-/hrv/date/{yesterday}/all.json", hrv_to_samples, yesterday),
            (f"/1/user/-/spo2/date/{yesterday}/all.json", spo2_to_samples, yesterday),
            (f"/1/user/-/br/date/{yesterday}/all.json", br_to_daily, yesterday),
            (f"/1/user/-/activities/steps/date/{today}/1d/1min/time/{start}/{end}.json", steps_to_samples, today),
            (f"/1.2/user/-/sleep/date/{yesterday}.json", sleep_to_daily, yesterday),
            (f"/1/user/-/temp/skin/date/{yesterday}.json", temp_to_daily, yesterday),
        ]
        before = self.client.requests_last_hour
        payloads = []
        try:
            for path, converter, day in specs:
                data = await self.client.get(path)
                payloads.append(converter(day, data, tz_name))
            result = merge(payloads)
            sent = self.sink(result)
            if inspect.isawaitable(sent): await sent
            self.last_sync_t, self.last_error, self.connected = now.timestamp(), None, True
            return {"samples": len(result["samples"]), "daily": len(result["daily"]),
                    "requests_used": self.client.requests_last_hour - before}
        except Exception as exc:
            self.last_error = str(exc); raise

    async def run_forever(self) -> None:
        backoff = self.interval_s
        while True:
            try:
                await self.sync_once(); backoff = self.interval_s
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError: raise
            except RateLimitError as exc:
                await asyncio.sleep(max(exc.retry_after, self.interval_s))
            except Exception:
                await asyncio.sleep(backoff); backoff = min(backoff * 2, 1800)
