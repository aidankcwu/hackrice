"""Non-blocking synchronous escalation gate."""

from __future__ import annotations

import logging
from collections import Counter, deque
from collections.abc import Callable, Iterable

from ..config import Timings
from ..db import Database
from ..episodes import EpisodeBuilder
from ..models import Escalation, Tick
from .triggers import BiometricFeed, Trigger, wearable_now_line

log = logging.getLogger(__name__)


class TriggerGate:
    def __init__(
        self,
        triggers: Iterable[Trigger],
        timings: Timings,
        db: Database,
        episodes: EpisodeBuilder,
        try_escalate: Callable[[Escalation], bool],
        demo_mode: bool,
        feed: BiometricFeed | None = None,
    ) -> None:
        self.triggers = list(triggers)
        self.timings = timings
        self.db = db
        self.episodes = episodes
        self.try_escalate = try_escalate
        self.demo_mode = demo_mode
        # SPEC §14.3 folds the wearable into `biometric_anomaly`; the same
        # numbers are worth having on every other escalation too, so the feed
        # is held here as well and read once per fire.
        self.feed = feed
        self.window: deque[Tick] = deque()
        self.fired: Counter[str] = Counter()
        self.dropped = 0
        self.suppressed: Counter[str] = Counter()
        self.last_escalation_t: float | None = None
        self._last_trigger_t: dict[str, float] = {}
        self._escalated_episode_ids: set[str] = set()
        self._unbound_episode_kinds: set[str] = set()

    def reset(self) -> None:
        """Forget wearer-specific gating state while preserving lifetime stats."""

        self._last_trigger_t.clear()
        self.last_escalation_t = None
        self._escalated_episode_ids.clear()
        self._unbound_episode_kinds.clear()
        self.window.clear()

    def _wearable_lines(self, t: float) -> list[str]:
        """The "wearable now" line for one escalation, or nothing.

        Read at fire time, not every tick: escalations are rare and the feed
        read costs one row per metric. Never raises -- a broken feed must not
        cost an escalation.
        """

        if self.feed is None:
            return []
        try:
            line = wearable_now_line(self.feed, t)
        except Exception:  # pragma: no cover - defensive
            log.debug("wearable context unavailable", exc_info=True)
            return []
        return [line] if line else []

    def _submit(self, escalation: Escalation) -> bool:
        accepted = self.try_escalate(escalation)
        if not accepted:
            self.dropped += 1
            log.info("gate dropped escalation %s on contention", escalation.trigger)
        return accepted

    def on_tick(self, tick: Tick) -> Escalation | None:
        self.window.append(tick)
        # Keep enough history for the widest window any trigger evaluates
        # (frames live 90 s; the production biometric window is 180 s and must
        # still see exertion from its first half — Astra review of S6b).
        keep_s = max(90.0, float(getattr(self.timings, "biometric_window", 0.0)))
        while self.window and self.window[0].t < tick.t - keep_s:
            self.window.popleft()

        for check in self.db.due_pending_checks(tick.t):
            escalation = Escalation(
                trigger=f"watch:{check.reason}", t=tick.t, tick=tick,
                window=list(self.window), reason=check.reason,
                extra_text=self._wearable_lines(tick.t),
            )
            accepted = self._submit(escalation)
            self.db.mark_pending_fired(check.id)
            if accepted:
                self.fired[escalation.trigger] += 1
                self.last_escalation_t = tick.t

        open_episodes = self.episodes.open_episodes()
        for kind in tuple(self._unbound_episode_kinds):
            episode = open_episodes.get(kind)  # type: ignore[arg-type]
            if episode is not None:
                self._escalated_episode_ids.add(episode.id)
                self._unbound_episode_kinds.remove(kind)
        for trigger in self.triggers:
            if not trigger.predicate(list(self.window)):
                continue
            episode = open_episodes.get(trigger.episode_kind) if trigger.episode_kind else None
            # Per-trigger suppression must not block other triggers this tick
            # (e.g. caffeine seen while a screen_block is already escalated).
            if episode is not None and episode.id in self._escalated_episode_ids:
                self.suppressed[trigger.name] += 1
                continue
            last = self._last_trigger_t.get(trigger.name)
            if last is not None and tick.t - last < trigger.cooldown_s:
                self.suppressed[trigger.name] += 1
                continue
            if self.last_escalation_t is not None and tick.t - self.last_escalation_t < self.timings.global_escalation_min_gap:
                self.suppressed[trigger.name] += 1
                return None

            window = list(self.window)
            extra_text: list[str] = []
            if trigger.enrich is None:
                reason = trigger.reason.format(trigger=trigger.name)
            else:
                # Synchronous by contract -- the gate never awaits (SPEC §3).
                # `biometric_anomaly` uses this to fold the wearable HR series
                # into the escalation (SPEC §14.3); the frames explain it. Its
                # own `reason` is a template only `enrich` can fill.
                reason, extra_text = trigger.enrich(window)
                reason = reason or trigger.name
            # Every escalation carries the wearable's current numbers, after
            # any trigger-specific lines -- the HR series a `biometric_anomaly`
            # brings is the detail, this is the standing context.
            extra_text = extra_text + self._wearable_lines(tick.t)
            escalation = Escalation(
                trigger=trigger.name, t=tick.t, tick=tick, window=window,
                episode_id=episode.id if episode is not None else None,
                reason=reason, extra_text=extra_text,
            )
            if self._submit(escalation):
                self.fired[trigger.name] += 1
                self._last_trigger_t[trigger.name] = tick.t
                self.last_escalation_t = tick.t
                if episode is not None:
                    self._escalated_episode_ids.add(episode.id)
                elif trigger.episode_kind is not None:
                    self._unbound_episode_kinds.add(trigger.episode_kind)
            return escalation
        return None

    def stats(self) -> dict[str, object]:
        return {
            "fired": dict(self.fired),
            "dropped": self.dropped,
            "suppressed": dict(self.suppressed),
            "last_escalation_t": self.last_escalation_t,
        }
