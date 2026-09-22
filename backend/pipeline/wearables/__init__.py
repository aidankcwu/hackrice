"""Live wearable ingest: the metric catalogue every path agrees on.

SPEC §14 names three devices because each has a public API and each is the
strongest source for something the others are not. This module is the one place
that says *which* metrics this backend accepts, what unit each arrives in, which
devices can supply it, and how often a sample is expected. Everything else --
the HTTP routes, the vendor adapters, the seeded demo series, the "wearable now"
context line -- reads the catalogue rather than hardcoding a metric list.

Nothing here imports the database or FastAPI, so adapters and the trigger gate
can both depend on it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DEVICES",
    "LIVE_METRICS",
    "MetricInfo",
    "Sample",
    "known_device",
    "known_metric",
]

#: Devices this backend accepts samples from. ``sim`` is the seeded fallback
#: used when nothing real is connected. ``healthkit`` is the phone app's own
#: Apple Health sync.
DEVICES: tuple[str, ...] = ("apple_watch", "whoop", "oura", "sim", "fitbit", "healthkit",)


@dataclass(frozen=True, slots=True)
class MetricInfo:
    """One accepted metric.

    ``cadence_s`` is the *expected* spacing between samples on the device that
    reports it most often -- a hint for the dashboard and for the freshness
    checks, never a rule the ingest enforces.
    """

    unit: str
    devices: tuple[str, ...]
    cadence_s: int
    label: str
    note: str = ""


LIVE_METRICS: dict[str, MetricInfo] = {
    "heart_rate": MetricInfo(
        "bpm", ("apple_watch", "whoop", "oura"), 60, "HR",
    ),
    "hrv_rmssd": MetricInfo(
        "ms", ("apple_watch", "whoop"), 3600, "HRV",
        note="Apple Watch reports SDNN, not RMSSD; stored as-is and labelled "
             "HRV -- the two are correlated but not interchangeable.",
    ),
    "spo2": MetricInfo(
        "%", ("apple_watch", "whoop", "oura"), 300, "SpO2",
    ),
    "respiratory_rate": MetricInfo(
        "brpm", ("apple_watch", "whoop", "oura"), 300, "RR",
    ),
    "wrist_temp_dev": MetricInfo(
        "degC", ("apple_watch", "oura", "whoop"), 3600, "wrist temp",
        note="Deviation from the wearer's own baseline, not an absolute "
             "temperature. WHOOP reports absolute skin temp; the adapter "
             "subtracts a fixed 33.0 degC baseline.",
    ),
    "steps_delta": MetricInfo(
        "steps", ("apple_watch",), 60, "steps",
        note="Steps taken since the previous sample, not a running total.",
    ),
    "active_energy": MetricInfo(
        "kcal", ("apple_watch",), 60, "active energy",
        note="Energy burned since the previous sample, not a running total.",
    ),
    "strain": MetricInfo(
        "0-21", ("whoop",), 900, "strain",
        note="Cumulative across the WHOOP cycle, so it only ever rises "
             "until the cycle rolls over.",
    ),
    "walking_hr_avg": MetricInfo(
        "bpm", ("apple_watch",), 3600, "walking HR",
    ),
    "env_sound_db": MetricInfo(
        "dBA", ("apple_watch",), 300, "sound",
    ),
}


@dataclass(frozen=True, slots=True)
class Sample:
    """One accepted reading, already in canonical units.

    Adapters are pure functions from a vendor payload to a list of these; the
    ingest is the only thing that talks to the database.
    """

    t: float
    metric: str
    value: float
    unit: str = ""
    device: str = "sim"


def known_metric(metric: str) -> bool:
    return metric in LIVE_METRICS


def known_device(device: str) -> bool:
    return device in DEVICES
