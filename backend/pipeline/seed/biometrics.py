"""Deterministic intraday wearable series on the simulation tick clock."""

from __future__ import annotations

import math

from ..db import Database

DAY_SECONDS = 24 * 3600
SPIKE_START = 135
SPIKE_END = 235


def _baseline(resting_hr: float, elapsed: float) -> float:
    diurnal = 2.0 * math.sin(2.0 * math.pi * elapsed / DAY_SECONDS - math.pi / 2.0)
    wobble = 0.8 * math.sin(2.0 * math.pi * elapsed / 17.0)
    return resting_hr + diurnal + wobble


def _heart_rate(resting_hr: float, elapsed: float) -> float:
    base = _baseline(resting_hr, elapsed)
    peak = resting_hr * 1.65
    if 140 <= elapsed < 150:
        return base + (peak - base) * (elapsed - 140) / 10.0
    if 150 <= elapsed <= 200:
        return peak + 0.8 * math.sin(2.0 * math.pi * elapsed / 9.0)
    if 200 < elapsed <= 230:
        return peak + (base - peak) * (elapsed - 200) / 30.0
    return base


def heart_rate_series(
    start_t: float, duration_s: int = DAY_SECONDS, resting_hr: float = 58.0
) -> list[tuple[float, str, float, str]]:
    """Return minute samples, augmented with one-second lunch-spike samples."""

    elapsed = set(range(0, duration_s, 60))
    elapsed.update(range(SPIKE_START, min(SPIKE_END, duration_s) + 1))
    return [
        (start_t + second, "heart_rate", round(_heart_rate(resting_hr, second), 2),
         "apple_watch")
        for second in sorted(elapsed)
    ]


def seed_biometric_series(
    db: Database, start_t: float, force: bool = False, duration_s: int = DAY_SECONDS
) -> int:
    """Seed the demo-day heart-rate series once unless forced."""

    if not force and db.biometric_series("heart_rate", start_t, start_t + duration_s):
        return 0
    return db.insert_biometric_series(heart_rate_series(start_t, duration_s))
