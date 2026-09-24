"""Vendor payload -> canonical samples. Pure functions, no I/O.

Two real integration paths are supported today (see ``docs/WEARABLES.md``):

* **Health Auto Export** -- the iOS app that can POST HealthKit metrics to an
  arbitrary REST endpoint every few minutes. The fastest way to get a real
  Apple Watch onto this backend without shipping an app.
* **WHOOP API v2** -- OAuth REST objects (``recovery``, ``cycle``, ``sleep``,
  ``workout``). WHOOP exposes no raw intraday HR stream, so what lands here is
  per-record summary values stamped at the record's own boundary.
* **Bryan's HealthKit sync** -- the phone app posts the canonical body with
  ``source: "healthkit"``; its once-a-day numbers become daily rows.

Everything returns :class:`~pipeline.wearables.Sample` objects in canonical
units; validation (unknown metric, stale timestamp, non-numeric value) is the
ingest's job, not the adapter's, so an adapter stays trivially testable.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable

from ..db import day_key
from ..models import SeededRow
from . import LIVE_METRICS, Sample
from .google_health import NIGHT_LOOKBACK

__all__ = [
    "WHOOP_LIVE_SOURCE",
    "WHOOP_SKIN_TEMP_BASELINE_C",
    "HEALTH_AUTO_EXPORT_METRICS",
    "HEALTHKIT_DAILY_METRICS",
    "HEALTHKIT_SOURCE",
    "health_auto_export_to_samples",
    "healthkit_seeded_rows",
    "healthkit_to_samples",
    "is_healthkit",
    "whoop_to_samples",
    "whoop_seeded_rows",
    "dedupe",
    "parse_health_auto_export_date",
]

#: WHOOP reports absolute skin temperature; this backend stores a deviation
#: (SPEC §14.1 "skin temperature deviation, °C from baseline"). WHOOP does not
#: publish the wearer's own baseline through the API, so a fixed 33.0 °C
#: wrist-skin baseline is subtracted. The number is a constant, not a
#: measurement -- treat the resulting deviation as coarse.
WHOOP_SKIN_TEMP_BASELINE_C = 33.0

#: Health Auto Export metric name -> this backend's metric name. Names the app
#: does not emit, or that this backend does not accept, are simply absent.
HEALTH_AUTO_EXPORT_METRICS: dict[str, str] = {
    "heart_rate": "heart_rate",
    "heart_rate_variability": "hrv_rmssd",
    "blood_oxygen_saturation": "spo2",
    "respiratory_rate": "respiratory_rate",
    "apple_sleeping_wrist_temperature": "wrist_temp_dev",
    "step_count": "steps_delta",
    "active_energy": "active_energy",
    "walking_heart_rate_average": "walking_hr_avg",
    "environmental_audio_exposure": "env_sound_db",
}

_HAE_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _unit_for(metric: str) -> str:
    info = LIVE_METRICS.get(metric)
    return info.unit if info is not None else ""


def parse_health_auto_export_date(raw: Any) -> float | None:
    """``"2026-09-12 14:02:00 -0500"`` -> epoch seconds, offset respected.

    The app stamps every point in the phone's local zone *with* the offset, so
    the parse must not fall back to the server's zone. A naive string (no
    offset, seen on some older builds) is read as local time.
    """

    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    for fmt in _HAE_DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.timestamp()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def health_auto_export_to_samples(payload: dict[str, Any]) -> list[Sample]:
    """Health Auto Export REST JSON -> samples, in payload order.

    Accepts both point shapes the app emits: ``qty`` for a plain reading and
    ``Min``/``Max``/``Avg`` for an aggregated one, where ``Avg`` is taken.
    """

    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    metrics = data.get("metrics") if isinstance(data, dict) else payload.get("metrics")
    if not isinstance(metrics, list):
        return []

    samples: list[Sample] = []
    for block in metrics:
        if not isinstance(block, dict):
            continue
        metric = HEALTH_AUTO_EXPORT_METRICS.get(str(block.get("name", "")).strip())
        if metric is None:
            continue
        points = block.get("data")
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict):
                continue
            t = parse_health_auto_export_date(point.get("date"))
            if t is None:
                continue
            value = _as_float(point.get("qty"))
            if value is None:
                value = _as_float(point.get("Avg", point.get("avg")))
            if value is None:
                continue
            samples.append(
                Sample(t=t, metric=metric, value=value,
                       unit=_unit_for(metric), device="apple_watch")
            )
    return samples


# -- Bryan's own HealthKit sync (ios Sources/Health) ----------------------

#: ``source``/``device`` the phone's HealthKit sync posts under, and the
#: ``seeded``-table source its daily rows are written with.
HEALTHKIT_SOURCE = "healthkit"

#: Phone metric name -> (daily ``seeded`` metric, unit). The phone posts the
#: canonical body (STATE.md §5) with once-a-day numbers, which belong beside the
#: Fitbit poller's daily rows, not in the intraday series.
HEALTHKIT_DAILY_METRICS: dict[str, tuple[str, str]] = {
    "sleep_hours": ("sleep_hours", "hours"),
    "sleep": ("sleep_hours", "hours"),
    "resting_hr": ("resting_hr", "bpm"),
    "resting_heart_rate": ("resting_hr", "bpm"),
    "hrv_sdnn": ("hrv_rmssd_ms", "ms"),
    "heart_rate_variability": ("hrv_rmssd_ms", "ms"),
    "hrv_rmssd": ("hrv_rmssd_ms", "ms"),
    "steps": ("steps", "steps"),
    "step_count": ("steps", "steps"),
}

#: Daily names that are also one reading with its own timestamp, so they land in
#: the intraday series too. HealthKit HRV is SDNN, stored as ``hrv_rmssd`` exactly
#: as the Health Auto Export path does (see the catalogue note).
_HEALTHKIT_INTRADAY = {"hrv_sdnn": "hrv_rmssd", "heart_rate_variability": "hrv_rmssd",
                       "hrv_rmssd": "hrv_rmssd"}

#: Sleep totals may arrive in minutes or seconds; the row is in hours.
_SLEEP_TO_HOURS = {"min": 1 / 60, "minutes": 1 / 60, "s": 1 / 3600, "sec": 1 / 3600}


def is_healthkit(payload: Any) -> bool:
    """True when a canonical body says it came from the phone's HealthKit sync."""

    if not isinstance(payload, dict):
        return False
    return any(str(payload.get(key, "")).strip().lower() == HEALTHKIT_SOURCE
               for key in ("source", "device"))


def healthkit_to_samples(samples: Iterable[Sample]) -> list[Sample]:
    """Parsed canonical samples -> intraday samples, all under ``healthkit``.

    Daily-only names (sleep, resting HR, steps) are left to
    :func:`healthkit_seeded_rows`; HRV is kept as a reading. Anything else
    passes through so the ingest accepts a catalogue metric and rejects an
    unknown one with its reason, as the canonical path does.
    """

    out: list[Sample] = []
    for sample in samples:
        metric = _HEALTHKIT_INTRADAY.get(sample.metric)
        if metric is None and sample.metric in HEALTHKIT_DAILY_METRICS:
            continue
        metric = metric or sample.metric
        unit = _unit_for(metric) if metric != sample.metric else sample.unit
        out.append(Sample(t=sample.t, metric=metric, value=sample.value,
                          unit=unit, device=HEALTHKIT_SOURCE))
    return out


def healthkit_seeded_rows(samples: Iterable[Sample]) -> list[SeededRow]:
    """The daily rows a HealthKit body carries, ``source="healthkit"``.

    Sleep is filed under the evening the night began (``t`` walked back
    :data:`~.google_health.NIGHT_LOOKBACK`, the Fitbit convention); resting HR,
    HRV and steps under the local day of ``t``. A value or time that cannot be
    dated is dropped, never raised on.
    """

    rows: list[SeededRow] = []
    for sample in samples:
        mapped = HEALTHKIT_DAILY_METRICS.get(sample.metric)
        if mapped is None:
            continue
        metric, unit = mapped
        value = sample.value
        if metric == "sleep_hours":
            value *= _SLEEP_TO_HOURS.get(sample.unit.strip().lower(), 1.0)
        at = sample.t - NIGHT_LOOKBACK.total_seconds() if metric == "sleep_hours" else sample.t
        if not (math.isfinite(value) and math.isfinite(at)):
            continue
        try:
            day = day_key(at)
        except (OverflowError, OSError, ValueError):
            continue
        rows.append(SeededRow(day=day, metric=metric, value=value, unit=unit,
                              source=HEALTHKIT_SOURCE))
    return rows


# -- WHOOP API v2 ---------------------------------------------------------

#: ``seeded``-table source of the daily rows a real WHOOP push writes. Not
#: ``whoop``: the SPEC §6 demo seed writes that, and the scorer must tell the
#: two apart (``scoring.scorer.LIVE_DAILY_SOURCES``). Intraday samples keep
#: device ``whoop``; their ``origin='live'`` already marks them real.
WHOOP_LIVE_SOURCE = "whoop_live"


def _whoop_time(raw: Any) -> float | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _whoop_records(payload: Any) -> list[tuple[str | None, dict[str, Any]]]:
    """Flatten whatever WHOOP shape arrived into ``(kind_hint, record)`` pairs.

    Accepts one record, ``{"records": [...]}`` as returned by the collection
    endpoints, and ``{"type": "recovery", "record"/"records": ...}`` for a
    caller that already knows which endpoint it polled.
    """

    if isinstance(payload, list):
        return [(None, r) for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    hint = payload.get("type") or payload.get("kind")
    hint = str(hint).lower() if isinstance(hint, str) else None
    for key in ("records", "data"):
        inner = payload.get(key)
        if isinstance(inner, list):
            return [(hint, r) for r in inner if isinstance(r, dict)]
    record = payload.get("record")
    if isinstance(record, dict):
        return [(hint, record)]
    return [(hint, payload)]


def _whoop_kind(record: dict[str, Any], hint: str | None) -> str | None:
    """Which v2 object this is, from an explicit hint or its own shape."""

    if hint in {"recovery", "cycle", "sleep", "workout"}:
        return hint
    score = record.get("score") if isinstance(record.get("score"), dict) else {}
    if "sport_id" in record or "sport_name" in record:
        return "workout"
    if "recovery_score" in score or ("cycle_id" in record and "sleep_id" in record):
        return "recovery"
    if "nap" in record or "sleep_performance_percentage" in score or "respiratory_rate" in score:
        return "sleep"
    if "strain" in score and "kilojoule" in score:
        return "cycle"
    if "strain" in score:
        return "cycle"
    return None


def _sample(t: float | None, metric: str, value: Any) -> Sample | None:
    number = _as_float(value)
    if t is None or number is None:
        return None
    return Sample(t=t, metric=metric, value=number,
                  unit=_unit_for(metric), device="whoop")


def whoop_to_samples(payload: Any) -> list[Sample]:
    """WHOOP v2 objects -> intraday samples.

    WHOOP publishes no raw HR stream, so heart rate arrives only as the average
    over a cycle or a workout. Each value is stamped at the boundary it
    actually describes: a cycle's numbers at cycle end, a workout's average at
    its start and its max at its end, a sleep's respiratory rate at wake.
    """

    samples: list[Sample] = []
    for hint, record in _whoop_records(payload):
        kind = _whoop_kind(record, hint)
        if kind is None:
            continue
        score = record.get("score")
        score = score if isinstance(score, dict) else {}
        start = _whoop_time(record.get("start") or record.get("created_at"))
        end = _whoop_time(record.get("end") or record.get("updated_at")) or start

        if kind == "recovery":
            at = _whoop_time(record.get("updated_at") or record.get("created_at"))
            candidates = [
                _sample(at, "hrv_rmssd", score.get("hrv_rmssd_milli")),
                _sample(at, "spo2", score.get("spo2_percentage")),
            ]
            skin = _as_float(score.get("skin_temp_celsius"))
            if skin is not None and at is not None:
                candidates.append(Sample(
                    t=at, metric="wrist_temp_dev",
                    value=round(skin - WHOOP_SKIN_TEMP_BASELINE_C, 3),
                    unit=_unit_for("wrist_temp_dev"), device="whoop",
                ))
            samples.extend(s for s in candidates if s is not None)
        elif kind == "cycle":
            candidates = [
                _sample(end, "strain", score.get("strain")),
                _sample(end, "heart_rate", score.get("average_heart_rate")),
            ]
            samples.extend(s for s in candidates if s is not None)
        elif kind == "sleep":
            sample = _sample(end, "respiratory_rate", score.get("respiratory_rate"))
            if sample is not None:
                samples.append(sample)
        elif kind == "workout":
            candidates = [
                _sample(start, "heart_rate", score.get("average_heart_rate")),
                _sample(end, "heart_rate", score.get("max_heart_rate")),
            ]
            samples.extend(s for s in candidates if s is not None)
    return samples


def whoop_seeded_rows(payload: Any) -> list[SeededRow]:
    """The daily rows a WHOOP payload carries (SPEC §14.2 ``seeded`` table).

    Only resting heart rate today: it is a once-a-night number, so it belongs
    with the other per-day rows the scorer reads, not in the intraday series.
    Written under :data:`WHOOP_LIVE_SOURCE`, never ``whoop``: that is the SPEC
    §6 demo seed's source, and a real night must not read as seed data.
    """

    rows: list[SeededRow] = []
    for hint, record in _whoop_records(payload):
        if _whoop_kind(record, hint) != "recovery":
            continue
        score = record.get("score")
        score = score if isinstance(score, dict) else {}
        resting = _as_float(score.get("resting_heart_rate"))
        at = _whoop_time(record.get("updated_at") or record.get("created_at"))
        if resting is None or at is None:
            continue
        rows.append(SeededRow(day=day_key(at), metric="resting_hr",
                              value=resting, unit="bpm", source=WHOOP_LIVE_SOURCE))
    return rows


def dedupe(samples: Iterable[Sample]) -> list[Sample]:
    """Last write wins per ``(t, metric)`` -- the storage primary key."""

    by_key: dict[tuple[float, str], Sample] = {}
    for sample in samples:
        by_key[(sample.t, sample.metric)] = sample
    return [by_key[key] for key in sorted(by_key)]
