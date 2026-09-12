"""Debounced, tri-state episode construction."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable

from ..config import Timings
from ..db import Database
from ..models import Episode, EpisodeKind, Tick


@dataclass(frozen=True, slots=True)
class EpisodeParams:
    entry_min_hits: int
    entry_window_s: float
    exit_min_misses: int
    exit_window_s: float
    unknown_grace_s: float
    entry: dict[EpisodeKind, tuple[int, float]] = field(default_factory=dict)
    sighting_min_hits: int = 2
    sighting_window_s: float = 10.0
    sighting_idle_close_s: float = 15.0
    sighting_max_s: float = 60.0

    @classmethod
    def from_timings(cls, timings: Timings, demo_mode: bool) -> "EpisodeParams":
        entry = {
            "meal": (timings.food_min_hits, timings.food_window),
            "screen_block": (
                timings.screen_sustained_min_hits,
                timings.screen_sustained_window,
            ),
            "conversation": (
                timings.people_sustained_min_hits,
                timings.people_sustained_window,
            ),
            "outdoor_block": (
                timings.outdoor_min_hits,
                timings.outdoor_sustained_window,
            ),
            "gym_session": (3, 10.0),
            "sauna_session": (3, 10.0),
            "caffeine_sighting": (2, 10.0),
            "alcohol_sighting": (2, 10.0),
        }
        if demo_mode:
            return cls(3, 6.0, 4, 6.0, 8.0, entry=entry)
        return cls(3, 10.0, 4, 10.0, 20.0, entry=entry)


Predicate = Callable[[Tick], bool | None]


@dataclass(slots=True)
class _State:
    positives: deque[float] = field(default_factory=deque)
    negatives: deque[float] = field(default_factory=deque)
    candidate_t: float | None = None
    candidate_ticks: int = 0
    episode: Episode | None = None
    last_hit_t: float | None = None
    last_fresh_ai_t: float | None = None
    modes: dict[str, Counter[str]] = field(
        default_factory=lambda: {name: Counter() for name in ("scene", "activity", "food_type")}
    )


def _flag(name: str) -> Predicate:
    return lambda tick: tick.flag(name)


def _outdoor(tick: Tick) -> bool | None:
    scene = tick.enum("scene")
    vegetation = tick.flag("vegetation_visible")
    if scene in {"park", "trail", "street"} or vegetation is True:
        return True
    if scene is None and vegetation is None:
        return None
    return False


def _scene(expected: str) -> Predicate:
    def predicate(tick: Tick) -> bool | None:
        value = tick.enum("scene")
        return None if value is None else value == expected

    return predicate


class EpisodeBuilder:
    """Collapse noisy tick tags into persisted episodes."""

    _PREDICATES: dict[EpisodeKind, Predicate] = {
        "meal": _flag("food_present"),
        "conversation": _flag("people_present"),
        "outdoor_block": _outdoor,
        "screen_block": _flag("screen_present"),
        "gym_session": _scene("gym"),
        "sauna_session": _scene("sauna"),
        "caffeine_sighting": _flag("caffeine_visible"),
        "alcohol_sighting": _flag("alcohol_visible"),
    }
    _SIGHTINGS = {"caffeine_sighting", "alcohol_sighting"}

    def __init__(self, db: Database, timings: Timings) -> None:
        self.db = db
        self.timings = timings
        self.demo_mode = timings == Timings.demo()
        self.params = EpisodeParams.from_timings(timings, self.demo_mode)
        lock = getattr(db, "_lock", None)
        if lock is None:
            count = db.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        else:
            with lock:
                count = db.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        self._counter = int(count)
        self._states = {kind: _State() for kind in self._PREDICATES}

    def open_episodes(self) -> dict[EpisodeKind, Episode]:
        return {
            kind: state.episode
            for kind, state in self._states.items()
            if state.episode is not None and state.episode.open
        }

    def _new_episode(self, kind: EpisodeKind, state: _State, tick: Tick) -> Episode:
        self._counter += 1
        start_t = state.candidate_t if state.candidate_t is not None else tick.t
        episode = Episode(
            id=f"e_{self._counter:04d}", kind=kind, start_t=start_t,
            duration_s=max(0.0, tick.t - start_t), tick_count=max(1, state.candidate_ticks),
        )
        state.episode = episode
        state.modes = {name: Counter() for name in ("scene", "activity", "food_type")}
        return episode

    @staticmethod
    def _trim(values: deque[float], cutoff: float) -> None:
        while values and values[0] < cutoff:
            values.popleft()

    def _update_dominant(self, state: _State, tick: Tick, uncertain: bool) -> None:
        episode = state.episode
        if episode is None:
            return
        for name, counts in state.modes.items():
            value = tick.enum(name)
            if value is not None:
                counts[value] += 1
            if counts:
                episode.dominant[name] = counts.most_common(1)[0][0]
        if uncertain:
            episode.dominant["uncertain"] = True
        else:
            episode.dominant.pop("uncertain", None)

    def _close(self, state: _State, tick: Tick) -> Episode:
        episode = state.episode
        assert episode is not None
        episode.open = False
        episode.end_t = tick.t
        episode.duration_s = max(0.0, tick.t - episode.start_t)
        state.episode = None
        state.positives.clear()
        state.negatives.clear()
        state.candidate_t = None
        state.candidate_ticks = 0
        state.last_hit_t = None
        state.modes = {name: Counter() for name in ("scene", "activity", "food_type")}
        return episode

    def on_tick(self, tick: Tick) -> list[Episode]:
        changed: list[Episode] = []
        p = self.params
        for kind, predicate in self._PREDICATES.items():
            state = self._states[kind]
            value = predicate(tick)
            if tick.ai_fresh():
                state.last_fresh_ai_t = tick.t
            is_sighting = kind in self._SIGHTINGS
            fallback = (
                (p.sighting_min_hits, p.sighting_window_s)
                if is_sighting
                else (p.entry_min_hits, p.entry_window_s)
            )
            entry_hits, entry_window = p.entry.get(kind, fallback)

            if state.episode is None:
                if value is True:
                    state.positives.append(tick.t)
                    self._trim(state.positives, tick.t - entry_window)
                    if state.candidate_t is None or state.candidate_t < tick.t - entry_window:
                        state.candidate_t = state.positives[0]
                        state.candidate_ticks = 1
                    else:
                        state.candidate_ticks += 1
                    state.last_hit_t = tick.t
                    if len(state.positives) >= entry_hits:
                        episode = self._new_episode(kind, state, tick)
                        self._update_dominant(state, tick, False)
                        self.db.upsert_episode(episode)
                        changed.append(episode)
                elif value is False:
                    state.positives.clear()
                    state.candidate_t = None
                    state.candidate_ticks = 0
                elif state.candidate_t is not None:
                    # Unknown observations do not affect debounce evidence, but
                    # they are still ticks within the episode once it opens.
                    state.candidate_ticks += 1
                continue

            episode = state.episode
            episode.tick_count += 1
            episode.duration_s = max(0.0, tick.t - episode.start_t)
            if value is True:
                state.last_hit_t = tick.t
                state.negatives.clear()
            elif value is False:
                state.negatives.append(tick.t)
                self._trim(state.negatives, tick.t - p.exit_window_s)

            last_fresh = state.last_fresh_ai_t
            silence = float("inf") if last_fresh is None else tick.t - last_fresh
            uncertain = silence >= p.unknown_grace_s
            self._update_dominant(state, tick, uncertain)

            close = False
            if is_sighting:
                close = (
                    tick.t - episode.start_t >= p.sighting_max_s
                    or state.last_hit_t is None
                    or tick.t - state.last_hit_t >= p.sighting_idle_close_s
                )
            else:
                close = len(state.negatives) >= p.exit_min_misses
                close = close or silence >= p.unknown_grace_s * 4
            if close:
                episode = self._close(state, tick)
            self.db.upsert_episode(episode)
            changed.append(episode)
        return changed
