"""Built-in stateful trigger predicates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import Timings
from ..models import EpisodeKind, Tick


@dataclass(frozen=True, slots=True)
class Trigger:
    name: str
    predicate: Callable[[list[Tick]], bool]
    cooldown_s: float
    episode_kind: EpisodeKind | None
    reason: str


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


def default_triggers(timings: Timings, demo_mode: bool) -> list[Trigger]:
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
    return [Trigger(name, predicate, cooldown(name), kind, reason) for name, predicate, kind, reason in specs]  # type: ignore[arg-type]
