"""Deterministic intraday wearable series on the simulation tick clock.

SPEC §14.2: hardcoded fixtures, no randomness, ``source`` set to the device
that would really have measured the number. These rows are written with
``origin='seed'``; a real wearable pushing into ``/api/wearables/ingest`` writes
``origin='live'`` and wins for any window it covers, so this module is the
default the demo falls back to rather than a thing to be deleted later.

Two clocks are in play. The heart-rate spike and everything keyed to it are
one-shot, at fixed scenario seconds. The metrics that follow what the wearer is
*doing* -- steps, active energy, ambient sound -- key off the scenario phase
(``elapsed % 390``), because the sim loops :data:`DEFAULT_SCENARIO`, so the
series keeps matching the frames for as long as the run lasts.
"""

from __future__ import annotations

import math

from ..db import Database

DAY_SECONDS = 24 * 3600
SPIKE_START = 135
SPIKE_END = 235

#: DEFAULT_SCENARIO's own clock, in scenario seconds (``sim/scenario.py``).
SCENARIO_TOTAL_S = 390
LUNCH_START, LUNCH_END = 135, 195
#: walk_street (195-225) then park (225-285).
WALK_START, WALK_END = 195, 285

#: The one benign SpO2 dip, so the dashboard has a non-flat line without the
#: gate having anything to say about it.
BENIGN_SPO2_T = 300

_HOUR = 3600
_FIVE_MIN = 300
_QUARTER_HOUR = 900

Row = tuple[float, str, float, str]


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
) -> list[Row]:
    """Return minute samples, augmented with one-second lunch-spike samples."""

    elapsed = set(range(0, duration_s, 60))
    elapsed.update(range(SPIKE_START, min(SPIKE_END, duration_s) + 1))
    return [
        (start_t + second, "heart_rate", round(_heart_rate(resting_hr, second), 2),
         "apple_watch")
        for second in sorted(elapsed)
    ]


# -- the rest of the metric set (SPEC §14.1) ------------------------------


def _stamps(duration_s: int, step: int, extra: set[int] | None = None) -> list[int]:
    seconds = set(range(0, duration_s, step))
    if extra:
        seconds.update(s for s in extra if s < duration_s)
    return sorted(seconds)


def _in_spike(elapsed: int) -> bool:
    return SPIKE_START <= elapsed <= SPIKE_END


def _phase(elapsed: int) -> int:
    return elapsed % SCENARIO_TOTAL_S


def _walking(elapsed: int) -> bool:
    return WALK_START <= _phase(elapsed) < WALK_END


def _at_lunch(elapsed: int) -> bool:
    return LUNCH_START <= _phase(elapsed) < LUNCH_END


def _hrv(duration_s: int) -> list[tuple[int, float, str]]:
    """Hourly RMSSD around 55 ms, dipping in the hour that holds the spike."""

    spike_hour = SPIKE_START // _HOUR
    out = []
    for second in _stamps(duration_s, _HOUR):
        hour = second // _HOUR
        value = 40.0 if hour == spike_hour else round(
            55.0 + 1.5 * math.sin(2.0 * math.pi * hour / 24.0), 2)
        out.append((second, value, "whoop"))
    return out


def _spo2(duration_s: int) -> list[tuple[int, float, str]]:
    """97-98 % every five minutes, with one benign 95."""

    out = []
    for second in _stamps(duration_s, _FIVE_MIN):
        if second == BENIGN_SPO2_T:
            value = 95.0
        else:
            value = 98.0 if (second // _FIVE_MIN) % 3 == 1 else 97.0
        out.append((second, value, "apple_watch"))
    return out


def _respiratory_rate(duration_s: int) -> list[tuple[int, float, str]]:
    """~14 brpm, lifted to 17 across the spike.

    The five-minute cadence would step straight over a 100 s spike, so the
    spike window gets its own minute samples -- the same trick the HR series
    already plays, for the same reason.
    """

    extra = set(range(SPIKE_START, min(SPIKE_END, duration_s) + 1, 60))
    out = []
    for second in _stamps(duration_s, _FIVE_MIN, extra):
        value = 17.0 if _in_spike(second) else round(
            14.0 + 0.4 * math.sin(2.0 * math.pi * second / DAY_SECONDS), 2)
        out.append((second, value, "apple_watch"))
    return out


def _wrist_temp(duration_s: int) -> list[tuple[int, float, str]]:
    """Hourly deviation from the wearer's baseline, -0.1 .. +0.2 degC."""

    out = []
    for second in _stamps(duration_s, _HOUR):
        hour = second // _HOUR
        value = round(0.05 + 0.15 * math.sin(2.0 * math.pi * hour / 24.0 - math.pi / 2.0), 2)
        out.append((second, value, "whoop"))
    return out


def _steps(duration_s: int) -> list[tuple[int, float, str]]:
    """Per-minute step delta: 0 seated, ~90 walking, a trickle over lunch."""

    out = []
    for second in _stamps(duration_s, 60):
        if _walking(second):
            value = 90.0 + 4.0 * math.sin(2.0 * math.pi * second / 180.0)
        elif _at_lunch(second):
            value = 6.0
        else:
            value = 0.0
        out.append((second, round(value), "apple_watch"))
    return out


def _active_energy(duration_s: int) -> list[tuple[int, float, str]]:
    """Per-minute kcal, tracking the same activity shape as the steps."""

    out = []
    for second in _stamps(duration_s, 60):
        if _walking(second):
            value = 5.2
        elif _at_lunch(second):
            value = 1.1
        else:
            value = 0.3
        out.append((second, value, "apple_watch"))
    return out


def _strain(duration_s: int) -> list[tuple[int, float, str]]:
    """WHOOP strain, cumulative and monotonic, reaching ~9.5 by day end.

    The demo starts mid-day (the scenario opens at the office and hits lunch
    100 s in), so the curve starts part-way up rather than at zero.
    """

    span = max(1, duration_s)
    out = []
    for second in _stamps(duration_s, _QUARTER_HOUR):
        value = 5.5 + 4.0 * (second / span) ** 0.8
        out.append((second, round(min(9.5, value), 2), "whoop"))
    return out


def _sound(duration_s: int) -> list[tuple[int, float, str]]:
    """Ambient dBA: quiet office, loud cafe over lunch, street between.

    Minute samples are added across the lunch window for the same reason the
    respiratory rate gets them -- a five-minute cadence would miss a 60 s
    segment entirely.
    """

    extra = set(range(LUNCH_START, min(LUNCH_END, duration_s), 60))
    out = []
    for second in _stamps(duration_s, _FIVE_MIN, extra):
        if _at_lunch(second):
            value = 62.0
        elif _walking(second):
            value = 55.0
        else:
            value = 45.0
        out.append((second, value, "apple_watch"))
    return out


_EXTRA_METRICS = {
    "hrv_rmssd": _hrv,
    "spo2": _spo2,
    "respiratory_rate": _respiratory_rate,
    "wrist_temp_dev": _wrist_temp,
    "steps_delta": _steps,
    "active_energy": _active_energy,
    "strain": _strain,
    "env_sound_db": _sound,
}


def extra_series(start_t: float, duration_s: int = DAY_SECONDS) -> list[Row]:
    """Every seeded metric except heart rate, in ``(t, metric, value, source)``."""

    rows: list[Row] = []
    for metric, build in _EXTRA_METRICS.items():
        rows.extend(
            (start_t + second, metric, value, source)
            for second, value, source in build(duration_s)
        )
    return rows


def demo_series(
    start_t: float, duration_s: int = DAY_SECONDS, resting_hr: float = 58.0
) -> list[Row]:
    """The whole seeded wearable day: heart rate plus the SPEC §14.1 metric set."""

    return heart_rate_series(start_t, duration_s, resting_hr) + extra_series(
        start_t, duration_s)


def seed_biometric_series(
    db: Database, start_t: float, force: bool = False, duration_s: int = DAY_SECONDS
) -> int:
    """Seed the demo-day wearable series once unless forced."""

    if not force and db.biometric_series(
        "heart_rate", start_t, start_t + duration_s, origin="seed"
    ):
        return 0
    return db.insert_biometric_series(
        demo_series(start_t, duration_s), origin="seed")
