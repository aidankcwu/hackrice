"""Built-in stateful trigger predicates."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
from typing import Callable, Protocol, runtime_checkable

from ..config import DEFAULT_KEYWORD_TRIGGERS, Timings
from ..models import EXERTION_ACTIVITIES, OUTDOOR_SCENES, EpisodeKind, Tick

__all__ = [
    "BiometricFeed",
    "CallableBiometricFeed",
    "Trigger",
    "biometric_anomaly_trigger",
    "change_trigger",
    "default_triggers",
    "keyword_trigger",
    "wearable_now_line",
]

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
    #: Optional hook the gate calls with the tick time only once the
    #: escalation was actually accepted (not when T1 was busy or the global
    #: gap rejected it). A trigger with its own budget spends it here.
    on_fired: Callable[[float], None] | None = None
    #: Skip the gate's global escalation gap and run before every other
    #: trigger: a fixed demo line must not queue behind a change wake-up.
    bypass_gap: bool = False


def _recent(window: list[Tick], seconds: float) -> list[Tick]:
    if not window:
        return []
    cutoff = window[-1].t - seconds
    return [tick for tick in window if tick.t >= cutoff]


def _flag_hits(
    name: str, seconds: float, minimum: int, max_age_ms: int = 3000
) -> Callable[[list[Tick]], bool]:
    """``minimum`` positive readings of ``name`` inside the last ``seconds``.

    ``minimum`` is a count of *ticks*, so the caller must already have run the
    1 Hz reference number through :meth:`Timings.scaled_hits`; ``max_age_ms``
    is the cadence-aware freshness budget (``Timings.ai_max_age_ms``).
    """

    def predicate(window: list[Tick]) -> bool:
        ticks = _recent(window, seconds)
        known = [
            value
            for value in (tick.flag(name, max_age_ms) for tick in ticks)
            if value is not None
        ]
        return bool(known) and known[-1] is True and sum(value is True for value in known) >= minimum

    return predicate


def _condition_hits(
    condition: Callable[[Tick], bool | None], seconds: float, minimum: int
) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        known = [v for t in _recent(window, seconds) if (v := condition(t)) is not None]
        return bool(known) and known[-1] is True and sum(v is True for v in known) >= minimum

    return predicate


def keyword_trigger(
    name: str,
    keywords: list[str],
    timings: Timings,
    *,
    min_hits: int = 2,
    window_s: float = 10.0,
    cooldown_s: float,
    reason: str,
    extra_line: str,
    bypass_gap: bool = False,
) -> Trigger:
    """Trigger on fresh, repeated keyword matches in captions or objects."""

    lowered = [(keyword, keyword.casefold()) for keyword in keywords]
    minimum = timings.scaled_hits(min_hits)

    def match(tick: Tick) -> tuple[str, str, str] | None:
        if not tick.ai_fresh(timings.ai_max_age_ms) or tick.ai is None:
            return None
        caption = tick.ai.caption or ""
        objects = tick.ai.objects or []
        fields = [caption, *objects]
        for keyword, folded in lowered:
            if any(folded in field.casefold() for field in fields):
                return keyword, caption, ", ".join(objects)
        return None

    def predicate(window: list[Tick]) -> bool:
        recent = _recent(window, window_s)
        return bool(recent and match(recent[-1])) and sum(match(t) is not None for t in recent) >= minimum

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        for tick in reversed(_recent(window, window_s)):
            hit = match(tick)
            if hit is not None:
                keyword, caption, objects = hit
                return reason.format(kw=keyword), [
                    extra_line.format(caption=caption, objects=objects)
                ]
        return reason, []

    return Trigger(name, predicate, cooldown_s, None, reason, enrich, bypass_gap=bypass_gap)


def change_trigger(timings: Timings) -> Trigger:
    """Wake T1 for stable, meaningful changes in fresh visual semantics."""

    required = max(2, timings.scaled_hits(2))
    fired_at: deque[float] = deque()
    unknown = {None, "", "?", "unknown"}

    def fresh(window: list[Tick], seconds: float) -> list[Tick]:
        return [
            tick for tick in _recent(window, seconds)
            if tick.ai_fresh(timings.ai_max_age_ms) and tick.ai is not None
        ]

    def value(tick: Tick, name: str) -> str | None:
        raw = getattr(tick.ai, name, None) if tick.ai is not None else None
        return None if raw in unknown else str(raw)

    def changes(window: list[Tick]) -> tuple[list[str], list[str]]:
        recent = fresh(window, 20.0)
        if len(recent) < required + 1:
            return [], []
        newest = recent[-required:]
        before12 = [t for t in recent[:-required] if t.t >= recent[-1].t - 12.0]
        reasons: list[str] = []
        transitions: list[str] = []

        for name in ("scene", "activity"):
            afters = [value(t, name) for t in newest]
            prior = next((value(t, name) for t in reversed(before12) if value(t, name)), None)
            if prior and afters[0] and len(set(afters)) == 1 and prior != afters[0]:
                reasons.append(f"{name} {prior} -> {afters[0]}")
                transitions.append(f"{name}: {prior} -> {afters[0]}")

        for name, label in (("drink", "drink"), ("food_type", "food")):
            afters = [value(t, name) for t in newest]
            raw_prior = next((value(t, name) for t in reversed(before12) if value(t, name)), None)
            valid_prior = raw_prior == "none" if name == "drink" else raw_prior is not None
            if afters[0] and len(set(afters)) == 1 and afters[0] != "none" and valid_prior and raw_prior != afters[0]:
                reasons.append(f"{label}: {afters[0]}")
                transitions.append(f"{name}: {raw_prior} -> {afters[0]}")

        current_objects = [set(t.ai.objects or []) for t in newest]  # type: ignore[union-attr]
        stable_objects = set.intersection(*current_objects) if current_objects else set()
        old_objects = {
            obj.casefold()
            for t in recent[:-required]
            for obj in (t.ai.objects or [])  # type: ignore[union-attr]
        }
        for obj in sorted(stable_objects, key=str.casefold):
            if obj.casefold() not in old_objects:
                reasons.append(f"new object: {obj}")
                transitions.append(f"objects: absent -> {obj}")

        def in_hand(tick: Tick, kind: str) -> bool:
            caption = tick.ai.caption.casefold() if tick.ai and tick.ai.caption else ""
            hand = "holding" in caption or "hand" in caption
            if kind == "food":
                return hand and tick.flag("food_present", timings.ai_max_age_ms) is True
            return hand and value(tick, "drink") not in unknown | {"none"}

        for kind in ("food", "drink"):
            if all(in_hand(t, kind) for t in newest) and not any(in_hand(t, kind) for t in before12):
                reasons.append(f"{kind} in hand")
                transitions.append(f"{kind} in hand: no -> yes")
        return reasons, transitions

    def within_limits(now: float) -> bool:
        while fired_at and fired_at[0] <= now - 60.0:
            fired_at.popleft()
        return (not fired_at or now - fired_at[-1] >= timings.change_cooldown_s) and len(fired_at) < timings.change_max_per_min

    def predicate(window: list[Tick]) -> bool:
        return bool(
            window
            and window[-1].ai_fresh(timings.ai_max_age_ms)
            and within_limits(window[-1].t)
            and changes(window)[0]
        )

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        reasons, transitions = changes(window)
        return "; ".join(reasons), ["Visual transition: " + "; ".join(transitions)] if transitions else []

    return Trigger(
        "change", predicate, timings.change_cooldown_s, None, "Meaningful visual change",
        enrich, on_fired=fired_at.append,
    )


def _outdoor_hits(
    seconds: float, minimum: int, max_age_ms: int = 3000
) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        observations: list[bool] = []
        for tick in _recent(window, seconds):
            scene = tick.enum("scene", max_age_ms)
            vegetation = tick.flag("vegetation_visible", max_age_ms)
            if scene is None and vegetation is None:
                continue
            observations.append(scene in OUTDOOR_SCENES or vegetation is True)
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
    max_age_ms = timings.ai_max_age_ms

    def _exerting(window: list[Tick], t0: float) -> bool:
        # Unknown activity counts neither way; a window with no known activity
        # at all still fires -- the wearable is saying something is up.
        return any(
            tick.enum("activity", max_age_ms) in EXERTION_ACTIVITIES
            for tick in window
            if tick.t >= t0
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
    timings: Timings, demo_mode: bool, feed: BiometricFeed | None = None,
    keyword_triggers: list[dict] | None = None,
) -> list[Trigger]:
    """Return shipped triggers in deterministic priority order."""

    # Kept as a named mapping so deployments can trivially override individual
    # entries while every unspecified trigger uses the configured fallback.
    cooldowns: dict[str, float] = {}
    cooldown = lambda name: cooldowns.get(name, timings.trigger_cooldown_default)
    # Every `*_min_hits` on Timings is a 1 Hz reference count; the stream runs
    # at `timings.tick_interval_s`, so a window holds fewer ticks than seconds
    # and the raw counts must be divided down or they become unreachable.
    hits = timings.scaled_hits
    max_age_ms = timings.ai_max_age_ms
    #: Sightings are point observations, not sustained states: two hits at
    #: 1 Hz, and at 1.5 s that floors to one, which is the intent -- a cup seen
    #: once in a 10 s window is a cup. The window itself does not scale.
    sighting_hits = max(1, hits(2))
    def meal(tick: Tick) -> bool | None:
        activity = tick.enum("activity", max_age_ms)
        food = tick.flag("food_present", max_age_ms)
        if activity == "eating":
            return True
        if food is None:
            return None
        caption = tick.ai.caption.casefold() if tick.ai and tick.ai.caption else ""
        return food and re.search(r"\b(?:eat(?:s|ing)?|bit(?:e|ing)|chew(?:s|ing)?)\b", caption) is not None

    def conversation(tick: Tick) -> bool | None:
        people = tick.flag("people_present", max_age_ms)
        if people is None:
            return None
        return people and (
            tick.flag("people_interacting", max_age_ms) is True
            or tick.enum("activity", max_age_ms) == "talking"
        )

    specs = [
        ("food_in_frame", _condition_hits(meal, timings.food_window, hits(timings.food_min_hits)), "meal", "Eating persisted in the recent frame window"),
        ("screen_sustained", _flag_hits("screen_present", timings.screen_sustained_window, hits(timings.screen_sustained_min_hits), max_age_ms), "screen_block", "Screen presence was sustained"),
        ("people_sustained", _condition_hits(conversation, timings.people_sustained_window, hits(timings.people_sustained_min_hits)), "conversation", "Social interaction was sustained"),
        ("outdoor_sustained", _outdoor_hits(timings.outdoor_sustained_window, hits(timings.outdoor_min_hits), max_age_ms), "outdoor_block", "Outdoor context was sustained"),
        ("caffeine_seen", _flag_hits("caffeine_visible", 10.0, sighting_hits, max_age_ms), "caffeine_sighting", "Caffeine was seen repeatedly"),
        ("alcohol_seen", _flag_hits("alcohol_visible", 10.0, sighting_hits, max_age_ms), "alcohol_sighting", "Alcohol was seen repeatedly"),
        ("stillness", _stillness(timings.stillness_window), None, "Low frame motion was sustained"),
    ]
    triggers = [change_trigger(timings)]
    triggers.extend(Trigger(name, predicate, cooldown(name), kind, reason) for name, predicate, kind, reason in specs)  # type: ignore[arg-type]
    entries = DEFAULT_KEYWORD_TRIGGERS if keyword_triggers is None else keyword_triggers
    for entry in entries:
        name = str(entry["name"])
        note = entry.get("note")
        context = f" Context from the wearer: {note}." if note else ""
        line = (
            f'Keyword trigger {name}: the VLM caption was "{{caption}}" with objects '
            f"[{{objects}}].{context} Decide for yourself whether this deserves speech; "
            "if so, say it in your own words, short and in the persona's voice."
        )
        triggers.append(keyword_trigger(
            name, list(entry["keywords"]), timings,
            min_hits=int(entry.get("min_hits", 2)),
            cooldown_s=float(entry.get("cooldown_s", timings.trigger_cooldown_default)),
            reason="Keyword '{kw}' seen in caption/objects", extra_line=line,
        ))
    if feed is not None:
        # Last: a camera trigger that fires on the same tick explains itself,
        # and this one costs a feed read.
        triggers.append(biometric_anomaly_trigger(timings, feed))
    return triggers
