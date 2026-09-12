"""Built-in stateful trigger predicates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from ..config import Timings
from ..models import EpisodeKind, Tick

__all__ = [
    "BiometricFeed",
    "CallableBiometricFeed",
    "Trigger",
    "biometric_anomaly_trigger",
    "default_triggers",
]

#: Activities that explain a high heart rate on their own (SPEC §14.3).
_EXERTION = {"exercising", "walking"}

#: Fraction of the window the HR series must actually cover.
_MIN_SPAN_FRACTION = 0.8

#: Samples needed before a series is worth believing.
_MIN_SAMPLES = 5

#: Fraction of those samples that must sit above resting x ratio.
_MIN_ELEVATED_FRACTION = 0.9

#: At most this many points are rendered into the escalation's extra line.
_HR_LINE_POINTS = 12

_BIOMETRIC_REASON = "Heart rate {hr:.0f} vs resting {rest:.0f} while not exercising"


@runtime_checkable
class BiometricFeed(Protocol):
    """Read-only view of the seeded intraday wearable series (SPEC §14.2).

    Both methods are synchronous and cheap -- the gate runs on every tick and
    never awaits. ``hr_series`` returns ``(t, bpm)`` pairs on the tick clock.
    """

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]: ...

    def resting_hr(self) -> float: ...


@dataclass(frozen=True, slots=True)
class CallableBiometricFeed:
    """A :class:`BiometricFeed` over two injected callables.

    Keeps the gate free of any dependency on the seeded store: wiring passes
    ``db.biometric_series``-shaped functions in, nothing here imports them.
    ``resting`` is re-read per call so an overnight update is picked up.
    """

    series: Callable[[float, float], list[tuple[float, float]]]
    resting: Callable[[], float]
    metric: str = "heart_rate"

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]:
        try:
            return list(self.series(t0, t1))
        except Exception:  # pragma: no cover - a feed read must never break a tick
            return []

    def resting_hr(self) -> float:
        try:
            return float(self.resting())
        except Exception:  # pragma: no cover - defensive
            return 0.0


@dataclass(frozen=True, slots=True)
class Trigger:
    name: str
    predicate: Callable[[list[Tick]], bool]
    cooldown_s: float
    episode_kind: EpisodeKind | None
    reason: str
    #: Optional hook run once, at fire time, on the same window the predicate
    #: saw. Returns ``(reason, extra_text)`` -- the rendered reason and any
    #: extra context lines for the envelope (SPEC §14.3). Synchronous, and
    #: allowed to return empties; the gate falls back to ``reason``.
    enrich: Callable[[list[Tick]], tuple[str, list[str]]] | None = None


def _recent(window: list[Tick], seconds: float) -> list[Tick]:
    if not window:
        return []
    cutoff = window[-1].t - seconds
    return [tick for tick in window if tick.t >= cutoff]


def _flag_hits(name: str, seconds: float, minimum: int) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        ticks = _recent(window, seconds)
        known = [tick.flag(name) for tick in ticks if tick.flag(name) is not None]
        return bool(known) and known[-1] is True and sum(value is True for value in known) >= minimum

    return predicate


def _outdoor_hits(seconds: float, minimum: int) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        observations: list[bool] = []
        for tick in _recent(window, seconds):
            scene = tick.enum("scene")
            vegetation = tick.flag("vegetation_visible")
            if scene is None and vegetation is None:
                continue
            observations.append(scene in {"park", "trail", "street"} or vegetation is True)
        return bool(observations) and observations[-1] and sum(observations) >= minimum

    return predicate


def _stillness(seconds: float) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        ticks = _recent(window, seconds)
        if len(ticks) < 2 or ticks[-1].t - ticks[0].t < seconds:
            return False
        measured = [tick.sensor.frame_delta for tick in ticks if tick.sensor.frame_delta is not None]
        return bool(measured) and sum(value < 0.03 for value in measured) / len(measured) >= 0.9

    return predicate


def _hr_stats(
    series: list[tuple[float, float]], rest: float, ratio: float
) -> tuple[float, float, int]:
    """Peak bpm, elevated fraction, and sample count for one series."""

    if not series:
        return 0.0, 0.0, 0
    threshold = rest * ratio
    elevated = sum(1 for _, bpm in series if bpm > threshold)
    return max(bpm for _, bpm in series), elevated / len(series), len(series)


def _subsample(series: list[tuple[float, float]], k: int) -> list[tuple[float, float]]:
    """Evenly spaced ``k`` points, always keeping the first and the last."""

    if len(series) <= k or k <= 1:
        return list(series)
    step = (len(series) - 1) / (k - 1)
    picked = {min(len(series) - 1, round(i * step)) for i in range(k)}
    return [series[i] for i in sorted(picked)]


def hr_context_line(
    series: list[tuple[float, float]], rest: float, origin: float, window_s: float
) -> str:
    """The one extra envelope line carrying the HR series (SPEC §14.3).

    T1 is asked what was happening, not whether HR was high, so the numbers go
    in as a compact series with offsets on the same ``t-Ns`` scale as the tick
    table and the frame labels.
    """

    points = ", ".join(
        f"t-{max(0, int(round(origin - t)))}s {bpm:.0f}"
        for t, bpm in _subsample(sorted(series), _HR_LINE_POINTS)
    )
    return (
        f"Heart rate (wearable, bpm) over the last {window_s:.0f}s, "
        f"resting {rest:.0f}: {points}"
    )


def biometric_anomaly_trigger(timings: Timings, feed: BiometricFeed) -> Trigger:
    """HR above ``resting x ratio`` for a sustained window while not exerting.

    The only trigger that fires on something the camera cannot see, so it is
    also the only one that carries an extra text line into the envelope: the
    wearable supplies the number, the frames supply the cause (SPEC §14.3).
    """

    window_s = timings.biometric_window
    ratio = timings.biometric_hr_ratio

    def _exerting(window: list[Tick], t0: float) -> bool:
        # Unknown activity counts neither way; a window with no known activity
        # at all still fires -- the wearable is saying something is up.
        return any(
            tick.enum("activity") in _EXERTION for tick in window if tick.t >= t0
        )

    def predicate(window: list[Tick]) -> bool:
        if not window:
            return False
        t1 = window[-1].t
        t0 = t1 - window_s
        series = feed.hr_series(t0, t1)
        if len(series) < _MIN_SAMPLES:
            return False
        stamps = [t for t, _ in series]
        if max(stamps) - min(stamps) < _MIN_SPAN_FRACTION * window_s:
            return False
        _, elevated, _ = _hr_stats(series, feed.resting_hr(), ratio)
        if elevated < _MIN_ELEVATED_FRACTION:
            return False
        return not _exerting(window, t0)

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        if not window:
            return _BIOMETRIC_REASON, []
        t1 = window[-1].t
        series = feed.hr_series(t1 - window_s, t1)
        rest = feed.resting_hr()
        peak, _, count = _hr_stats(series, rest, ratio)
        if not count:
            return "", []
        return (
            _BIOMETRIC_REASON.format(hr=peak, rest=rest),
            [hr_context_line(series, rest, t1, window_s)],
        )

    return Trigger(
        "biometric_anomaly",
        predicate,
        timings.biometric_cooldown,
        None,
        _BIOMETRIC_REASON,
        enrich,
    )


def default_triggers(
    timings: Timings, demo_mode: bool, feed: BiometricFeed | None = None
) -> list[Trigger]:
    """Return shipped triggers in deterministic priority order."""

    # Kept as a named mapping so deployments can trivially override individual
    # entries while every unspecified trigger uses the configured fallback.
    cooldowns: dict[str, float] = {}
    cooldown = lambda name: cooldowns.get(name, timings.trigger_cooldown_default)
    specs = [
        ("food_in_frame", _flag_hits("food_present", timings.food_window, timings.food_min_hits), "meal", "Food persisted in the recent frame window"),
        ("screen_sustained", _flag_hits("screen_present", timings.screen_sustained_window, timings.screen_sustained_min_hits), "screen_block", "Screen presence was sustained"),
        ("people_sustained", _flag_hits("people_present", timings.people_sustained_window, timings.people_sustained_min_hits), "conversation", "People presence was sustained"),
        ("outdoor_sustained", _outdoor_hits(timings.outdoor_sustained_window, timings.outdoor_min_hits), "outdoor_block", "Outdoor context was sustained"),
        ("caffeine_seen", _flag_hits("caffeine_visible", 10.0, 2), "caffeine_sighting", "Caffeine was seen repeatedly"),
        ("alcohol_seen", _flag_hits("alcohol_visible", 10.0, 2), "alcohol_sighting", "Alcohol was seen repeatedly"),
        ("stillness", _stillness(timings.stillness_window), None, "Low frame motion was sustained"),
    ]
    triggers = [Trigger(name, predicate, cooldown(name), kind, reason) for name, predicate, kind, reason in specs]  # type: ignore[arg-type]
    if feed is not None:
        # Last: a camera trigger that fires on the same tick explains itself,
        # and this one costs a feed read.
        triggers.append(biometric_anomaly_trigger(timings, feed))
    return triggers
