"""Debounced, tri-state episode construction."""

from __future__ import annotations

import logging

from collections import Counter, deque
from dataclasses import dataclass, field
import re
from typing import Callable

from ..config import Timings
from ..db import Database
from ..models import OUTDOOR_SCENES, Episode, EpisodeKind, Tick

log = logging.getLogger(__name__)


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
    #: Freshness budget for an `ai` block, from ``Timings.ai_max_age_ms``.
    ai_max_age_ms: int = 3000

    @classmethod
    def from_timings(cls, timings: Timings, demo_mode: bool) -> "EpisodeParams":
        """Debounce parameters for one cadence.

        SPEC §10 requires an episode and its escalation to agree on
        boundaries, so every entry count here goes through the same
        ``timings.scaled_hits`` the gate's triggers use: a "6 hits in 20 s"
        threshold is 4 ticks at a 1.5 s cadence, on both sides.
        """

        hits = timings.scaled_hits
        entry = {
            "meal": (hits(timings.food_min_hits), timings.food_window),
            "food_sighting": (max(1, hits(2)), 10.0),
            "screen_block": (
                hits(timings.screen_sustained_min_hits),
                timings.screen_sustained_window,
            ),
            "conversation": (
                hits(timings.people_sustained_min_hits),
                timings.people_sustained_window,
            ),
            "outdoor_block": (
                hits(timings.outdoor_min_hits),
                timings.outdoor_sustained_window,
            ),
            "gym_session": (hits(3), 10.0),
            "sauna_session": (hits(3), 10.0),
            "caffeine_sighting": (max(1, hits(2)), 10.0),
            "alcohol_sighting": (max(1, hits(2)), 10.0),
        }
        common = dict(
            entry=entry,
            sighting_min_hits=max(1, hits(2)),
            ai_max_age_ms=timings.ai_max_age_ms,
        )
        if demo_mode:
            return cls(hits(3), 6.0, hits(4), 15.0, 8.0, **common)
        return cls(hits(3), 10.0, hits(4), 10.0, 20.0, **common)


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


def _flag(name: str, max_age_ms: int = 3000) -> Predicate:
    return lambda tick: tick.flag(name, max_age_ms)


def _outdoor(max_age_ms: int = 3000) -> Predicate:
    def predicate(tick: Tick) -> bool | None:
        scene = tick.enum("scene", max_age_ms)
        vegetation = tick.flag("vegetation_visible", max_age_ms)
        if scene in OUTDOOR_SCENES or vegetation is True:
            return True
        if scene is None and vegetation is None:
            return None
        return False

    return predicate


def _scene(expected: str, max_age_ms: int = 3000) -> Predicate:
    def predicate(tick: Tick) -> bool | None:
        value = tick.enum("scene", max_age_ms)
        return None if value is None else value == expected

    return predicate


#: Caption language that makes visible food a meal rather than a sighting.
#: Deliberately wide (Astra: a real lunch with a misclassified activity must
#: not be downgraded): eating verbs, meal names, and a plate or snack in use.
_MEAL_WORDS = re.compile(
    r"\b(?:eat(?:s|ing|en)?|bit(?:e|es|ing)|chew(?:s|ing)?|lunch|dinner|breakfast"
    r"|brunch|meal|snacking|plate|bowl of|sandwich|fork|spoon|chopsticks)\b"
)


def _predicates(max_age_ms: int) -> dict[EpisodeKind, Predicate]:
    """The per-kind tri-state tag readers, at one freshness budget."""

    def meal(tick: Tick) -> bool | None:
        activity = tick.enum("activity", max_age_ms)
        food = tick.flag("food_present", max_age_ms)
        if activity == "eating":
            return True
        if food is None:
            return None
        caption = tick.ai.caption.casefold() if tick.ai and tick.ai.caption else ""
        return food and re.search(_MEAL_WORDS, caption) is not None

    def food_sighting(tick: Tick) -> bool | None:
        food = tick.flag("food_present", max_age_ms)
        eating = meal(tick)
        if food is None:
            return None
        return food and eating is not True

    def conversation(tick: Tick) -> bool | None:
        people = tick.flag("people_present", max_age_ms)
        interacting = tick.flag("people_interacting", max_age_ms)
        activity = tick.enum("activity", max_age_ms)
        if people is None:
            return None
        return people and (interacting is True or activity == "talking")

    return {
        "meal": meal,
        "food_sighting": food_sighting,
        "conversation": conversation,
        "outdoor_block": _outdoor(max_age_ms),
        "screen_block": _flag("screen_present", max_age_ms),
        "gym_session": _scene("gym", max_age_ms),
        "sauna_session": _scene("sauna", max_age_ms),
        "caffeine_sighting": _flag("caffeine_visible", max_age_ms),
        "alcohol_sighting": _flag("alcohol_visible", max_age_ms),
    }


class EpisodeBuilder:
    """Collapse noisy tick tags into persisted episodes."""

    _SIGHTINGS = {"food_sighting", "caffeine_sighting", "alcohol_sighting"}

    def __init__(self, db: Database, timings: Timings) -> None:
        self.db = db
        self.timings = timings
        # Compare against the demo preset *at this cadence* -- a 1.5 s demo is
        # still a demo, and must keep the shorter debounce windows.
        self.demo_mode = timings == Timings.demo(timings.tick_interval_s)
        self.params = EpisodeParams.from_timings(timings, self.demo_mode)
        self._kind_predicates = _predicates(self.params.ai_max_age_ms)
        lock = getattr(db, "_lock", None)
        if lock is None:
            count = db.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        else:
            with lock:
                count = db.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        self._counter = int(count)
        self._states = {kind: _State() for kind in self._kind_predicates}
        #: The last tick seen, to recognise T0's publish-on-landing re-send.
        self._last_tick_id: str | None = None
        # A restart must not inherit open episodes from the process before it:
        # they would stay open forever (nothing in memory owns them) and the
        # scorer would clip them to every window it looks at.
        stale = getattr(db, "close_stale_open_episodes", None)
        if stale is not None:
            closed = stale()
            if closed:
                log.info("episodes: closed %d left open by a previous process", closed)

    def open_episodes(self) -> dict[EpisodeKind, Episode]:
        return {
            kind: state.episode
            for kind, state in self._states.items()
            if state.episode is not None and state.episode.open
        }

    def reset(self, t: float) -> None:
        """Close open episodes and discard all candidate state for a new wearer."""

        for state in self._states.values():
            if state.episode is not None and state.episode.open:
                episode = self._close(state, Tick(
                    tick_id="session_reset", t=t, seq=0,
                    sensor={}, frame_ref="session_reset",
                ))
                self.db.upsert_episode(episode)
            state.positives.clear()
            state.negatives.clear()
            state.candidate_t = None
            state.candidate_ticks = 0
            state.episode = None
            state.last_hit_t = None
            state.last_fresh_ai_t = None
            state.modes = {
                name: Counter() for name in ("scene", "activity", "food_type")
            }
        # Rows a previous process left open against this DB file.
        self.db.close_open_episodes(t)

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
            value = tick.enum(name, self.params.ai_max_age_ms)
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
        """Fold one tick into every episode kind.

        T0 re-sends the newest tick with its ai block once Gemini lands
        (publish-on-landing): same tick_id, first without ai, then with it.
        The first pass counted it as an unknown tick; the re-send adds only
        what is new -- the evidence -- and never counts the tick a second time
        (``tick_count``, ``candidate_ticks``). Without this every landed result
        either doubled the tick counts or, skipped, never reached an episode.
        """

        changed: list[Episode] = []
        p = self.params
        resend = tick.tick_id == self._last_tick_id
        self._last_tick_id = tick.tick_id
        for kind, predicate in self._kind_predicates.items():
            state = self._states[kind]
            value = predicate(tick)
            if tick.ai_fresh(p.ai_max_age_ms):
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
                    elif not resend:  # the first pass already counted this tick
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
                elif state.candidate_t is not None and not resend:
                    # Unknown observations do not affect debounce evidence, but
                    # they are still ticks within the episode once it opens.
                    state.candidate_ticks += 1
                continue

            episode = state.episode
            if not resend:
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
                miss_span = (
                    tick.t - state.negatives[0] if state.negatives else 0.0
                )
                close = len(state.negatives) >= p.exit_min_misses
                if kind in {"screen_block", "conversation", "outdoor_block", "meal"}:
                    close = close and miss_span >= p.exit_window_s
                close = close or silence >= p.unknown_grace_s * 4
            if close:
                episode = self._close(state, tick)
            self.db.upsert_episode(episode)
            changed.append(episode)
        return changed
