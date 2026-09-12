"""Validate and store samples pushed in by a real wearable.

The seeded demo series (SPEC §14.2) stays the default: it is written with
``origin='seed'`` and is what the gate and the dashboard read when nothing is
connected. Anything that arrives here is written with ``origin='live'`` and
wins over the seeded rows for the same metric and window -- so plugging a watch
in mid-demo swaps the numbers without touching a line of downstream code.

Validation is deliberately loud-but-lenient: a bad sample is dropped with a
reason and the rest of the batch still lands, because a wearable bridge posting
every minute must never be able to fail the whole batch on one odd reading.
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from ..db import Database
from . import DEVICES, LIVE_METRICS, Sample
from .adapters import dedupe

__all__ = ["MAX_CLOCK_SKEW_S", "MAX_SAMPLES", "ingest", "ingest_samples", "parse_payload"]

#: How far from "now" a sample's timestamp may sit. Wide enough for a phone
#: that has been offline overnight, narrow enough that a garbled epoch (ms
#: instead of seconds, say) is rejected rather than stored in the year 56000.
MAX_CLOCK_SKEW_S = 48 * 3600

#: A single POST may not carry more than this; a bridge catching up after an
#: outage should page rather than send one enormous body.
MAX_SAMPLES = 20_000


def _empty_result() -> dict[str, Any]:
    return {"accepted": 0, "rejected": 0, "reasons": {}}


def _reject(result: dict[str, Any], reason: str, count: int = 1) -> None:
    result["rejected"] += count
    result["reasons"][reason] = result["reasons"].get(reason, 0) + count


def parse_payload(payload: Any) -> tuple[list[Sample], dict[str, Any]]:
    """Canonical payload -> ``(samples, result)`` with malformed entries counted.

    Canonical shape::

        {"device": "apple_watch",
         "samples": [{"t": 1757700000, "metric": "heart_rate",
                      "value": 72, "unit": "bpm"}, ...]}

    A sample may override ``device`` individually, which is how a bridge
    forwarding both a watch and a ring in one POST stays one request.
    """

    result = _empty_result()
    if not isinstance(payload, dict):
        _reject(result, "payload is not an object")
        return [], result
    default_device = str(payload.get("device", "")).strip() or "sim"
    raw = payload.get("samples")
    if not isinstance(raw, list):
        _reject(result, "samples must be a list")
        return [], result
    if len(raw) > MAX_SAMPLES:
        _reject(result, f"batch larger than {MAX_SAMPLES} samples", len(raw))
        return [], result

    samples: list[Sample] = []
    for entry in raw:
        if not isinstance(entry, dict):
            _reject(result, "sample is not an object")
            continue
        metric = str(entry.get("metric", "")).strip()
        device = str(entry.get("device", "") or default_device).strip()
        t = entry.get("t", entry.get("time"))
        value = entry.get("value")
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            _reject(result, "t must be epoch seconds")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _reject(result, "value must be a number")
            continue
        samples.append(Sample(t=float(t), metric=metric, value=float(value),
                              unit=str(entry.get("unit", "")), device=device))
    return samples, result


def ingest_samples(
    db: Database,
    samples: Iterable[Sample],
    *,
    now: float | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and store already-parsed samples. Idempotent on ``(t, metric)``.

    The store's primary key is ``(t, metric)`` and the write is an
    ``INSERT OR REPLACE``, so re-posting the same window -- which every polling
    bridge does at least once -- overwrites rather than duplicates.
    """

    out = result if result is not None else _empty_result()
    stamp = time.time() if now is None else now
    accepted: list[Sample] = []
    for sample in samples:
        if sample.metric not in LIVE_METRICS:
            _reject(out, f"unknown metric {sample.metric!r}")
            continue
        if sample.device not in DEVICES:
            _reject(out, f"unknown device {sample.device!r}")
            continue
        if not isinstance(sample.value, (int, float)) or sample.value != sample.value:
            _reject(out, "value must be a number")
            continue
        if abs(sample.t - stamp) > MAX_CLOCK_SKEW_S:
            _reject(out, "t outside the +/-48h window")
            continue
        accepted.append(sample)

    db.insert_biometric_series(
        [(s.t, s.metric, float(s.value), s.device) for s in dedupe(accepted)],
        origin="live",
    )
    out["accepted"] += len(accepted)
    return out


def ingest(db: Database, payload: Any, *, now: float | None = None) -> dict[str, Any]:
    """Canonical ingest: parse, validate, store. Never raises on bad input."""

    samples, result = parse_payload(payload)
    return ingest_samples(db, samples, now=now, result=result)
