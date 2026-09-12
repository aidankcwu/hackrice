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
    "wearable_now_line",
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
    """Read-only view of the intraday wearable series (SPEC §14.2).

    Every method is synchronous and cheap -- the gate runs on every tick and
    never awaits. ``series`` returns ``(t, value)`` pairs on the tick clock for
    any metric in :data:`pipeline.wearables.LIVE_METRICS`; ``hr_series`` is the
    heart-rate special case the ``biometric_anomaly`` trigger was built around
    and is kept as its own name because that trigger reads it every tick.

    The feed does not care whether the numbers are seeded or came off a real
    watch -- that is the store's business (live rows win for any window they
    cover), which is what lets a wearable be plugged in mid-run.
    """

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]: ...

    def resting_hr(self) -> float: ...

    def series(self, metric: str, t0: float, t1: float) -> list[tuple[float, float]]: ...

    def latest(self, metric: str) -> tuple[float, float] | None: ...


@dataclass(frozen=True, slots=True)
class CallableBiometricFeed:
    """A :class:`BiometricFeed` over injected callables.

    Keeps the gate free of any dependency on the store: wiring passes
    ``db.biometric_series`` / ``db.latest_biometric``-shaped functions in,
    nothing here imports them. ``resting_fn`` is re-read per call so an
    overnight update is picked up.
    """

    series_fn: Callable[[str, float, float], list[tuple[float, float]]]
    resting_fn: Callable[[], float]
    #: ``db.latest_biometric``-shaped: ``(t, value, source, origin) | None``.
    latest_fn: Callable[[str], tuple[float, float, str, str] | None] | None = None
    metric: str = "heart_rate"

    def series(self, metric: str, t0: float, t1: float) -> list[tuple[float, float]]:
        try:
            return list(self.series_fn(metric, t0, t1))
        except Exception:  # pragma: no cover - a feed read must never break a tick
            return []

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]:
        return self.series(self.metric, t0, t1)

    def resting_hr(self) -> float:
        try:
            return float(self.resting_fn())
        except Exception:  # pragma: no cover - defensive
            return 0.0

    def _latest_row(self, metric: str) -> tuple[float, float, str, str] | None:
        if self.latest_fn is None:
            return None
        try:
            return self.latest_fn(metric)
        except Exception:  # pragma: no cover - defensive
            return None

    def latest(self, metric: str) -> tuple[float, float] | None:
        row = self._latest_row(metric)
        return None if row is None else (row[0], row[1])

    def latest_source(self, metric: str) -> str | None:
        """Which device reported the newest sample, for the "wearable now" line."""

        row = self._latest_row(metric)
        return None if row is None else row[2]


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


#: A sample older than this is not "now" any more and is left out of the line.
_NOW_WINDOW_S = 30 * 60

#: Steps are a rate, not a level, so they are summed over a short trailing window.
_STEPS_WINDOW_S = 10 * 60


def _fmt_hr(v: float) -> str:
    return f"HR {v:.0f} bpm"


def _fmt_hrv(v: float) -> str:
    return f"HRV {v:.0f} ms"


def _fmt_spo2(v: float) -> str:
    return f"SpO2 {v:.0f}%"


def _fmt_rr(v: float) -> str:
    return f"RR {v:.0f}"


def _fmt_temp(v: float) -> str:
    return f"wrist temp {v:+.1f}\u00b0C"


def _fmt_strain(v: float) -> str:
    return f"strain {v:.1f}"


#: Rendered in this order; anything the feed has no recent sample for is simply
#: skipped, so a wearer with only a watch gets a shorter line, not a line of
#: em-dashes.
_NOW_FIELDS: tuple[tuple[str, Callable[[float], str]], ...] = (
    ("heart_rate", _fmt_hr),
    ("hrv_rmssd", _fmt_hrv),
    ("spo2", _fmt_spo2),
    ("respiratory_rate", _fmt_rr),
    ("wrist_temp_dev", _fmt_temp),
    ("strain", _fmt_strain),
)


def wearable_now_line(feed: BiometricFeed, t: float) -> str | None:
    """One line of "what the wearable says right now", for any escalation.

    Every T1 call gets this, not just ``biometric_anomaly``: the frames show
    what the wearer was looking at and the tick table shows what the phone
    measured, but only the wearable can say whether the body was calm while it
    happened. That context is as useful on a ``food_in_frame`` as on an HR
    spike, and it costs one row read per metric.

    Returns ``None`` when nothing recent is available -- the caller attaches
    nothing rather than a line saying there is nothing.
    """

    parts: list[str] = []
    devices: list[str] = []
    source_of = getattr(feed, "latest_source", None)

    def note(metric: str) -> None:
        if not callable(source_of):
            return
        try:
            device = source_of(metric)
        except Exception:  # pragma: no cover - defensive
            return
        if device and device not in devices:
            devices.append(device)

    def recent(metric: str) -> float | None:
        """The newest value at or before ``t``, within the freshness window.

        The window read comes first and ``latest`` is only the fallback,
        because ``t`` is the *tick* clock: under ``--source sim`` the seeded
        day stretches hours past the current tick, so "the newest row in the
        table" is usually in the future and says nothing about now.
        """

        try:
            window = feed.series(metric, t - _NOW_WINDOW_S, t)
        except Exception:  # pragma: no cover - a feed read never breaks a tick
            window = []
        if window:
            return float(window[-1][1])
        try:
            latest = feed.latest(metric)
        except Exception:  # pragma: no cover - defensive
            return None
        if latest is None or abs(latest[0] - t) > _NOW_WINDOW_S:
            return None
        return float(latest[1])

    for metric, render in _NOW_FIELDS:
        value = recent(metric)
        if value is None:
            continue
        parts.append(render(value))
        note(metric)

    try:
        steps = feed.series("steps_delta", t - _STEPS_WINDOW_S, t)
    except Exception:  # pragma: no cover - defensive
        steps = []
    if steps:
        parts.append(f"steps last 10 min {sum(v for _, v in steps):.0f}")
        note("steps_delta")

    if not parts:
        return None
    who = f" ({'/'.join(devices)})" if devices else ""
    return f"Wearable now{who}: " + ", ".join(parts)


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
