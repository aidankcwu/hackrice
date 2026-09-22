"""Non-blocking synchronous escalation gate."""

from __future__ import annotations

import logging
from collections import Counter, deque
from collections.abc import Callable, Iterable

from ..config import Timings
from ..db import Database
from ..episodes import EpisodeBuilder
from ..models import Escalation, Tick
from .triggers import (
    HAND_CUE_KINDS,
    BiometricFeed,
    CueMoments,
    Trigger,
    same_item,
    tick_cues,
    wearable_now_line,
)

log = logging.getLogger(__name__)

#: Outcome strings a ``fast_path`` callable returns. ``handed_off:<id>`` and
#: ``conversation_repeat`` are the voice agent's own words
#: (``conversation.agent``); ``no_agent`` is ``Reasoner.fast_path`` saying no
#: voice agent is wired, so the cue takes the ordinary clerk route instead.
#: Kept as literals so the gate never imports the agent.
HANDED_OFF = "handed_off:"
REPEAT = "conversation_repeat"
NO_AGENT = "no_agent"
#: The agent has no phone to speak through. Nothing can be said, but the moment
#: can still be written down, so the cue takes the clerk route like NO_AGENT
#: rather than retrying into a mouth that is not there.
NO_TRANSPORT = "no_transport"

#: Once a persona cue went to the voice agent, the slower triggers that would
#: describe the same moment stay quiet for this long, so the clerk is not woken
#: to hand the same coffee off a second time (``caffeine_seen`` fires one tick
#: later, the instant the global gap has passed).
CUE_COVER_S = 30.0
#: Which triggers a handed-off cue kind covers, and which open episode it
#: labels: the clerk still writes the moment into the episode it belongs to.
CUE_COVERS: dict[str, tuple[str, ...]] = {
    "caffeine": ("caffeine_seen",),
    "food": ("food_in_frame",),
    "drink": (),
    "phone": (),
    "crowd": (),
}
CUE_EPISODES: dict[str, tuple[str, ...]] = {
    "caffeine": ("caffeine_sighting",),
    "food": ("food_sighting", "meal"),
    "drink": (),
    "phone": (),
    "crowd": (),
}


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
        fast_path: Callable[[Escalation], str] | None = None,
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
        #: ``Reasoner.fast_path``: hands a persona cue to the voice agent and
        #: wakes the clerk beside it. ``None`` sends cues down the clerk path.
        self.fast_path = fast_path
        #: The cue trigger's moments, if one is installed: ``_stamp_cue``
        #: falls back to the prop still being held when a wake-up's own tick
        #: cannot see the hands.
        self.moments: CueMoments | None = next(
            (t.moments for t in self.triggers if t.moments is not None), None)
        self.fast_pathed = 0
        self.fast_dropped: Counter[str] = Counter()
        self._covered_until: dict[str, float] = {}
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
        self._covered_until.clear()
        self.window.clear()
        for trigger in self.triggers:
            if trigger.reset is not None:
                trigger.reset()

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
        if self.window and self.window[-1].tick_id == tick.tick_id:
            # T0 may re-send the newest tick with its ai block attached the
            # moment Gemini lands (publish-on-landing, longevity.loop). It is
            # the same second, now with eyes: replace it, never count it twice.
            self.window[-1] = tick
        else:
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
            if self._covered_until.get(trigger.name, float("-inf")) > tick.t:
                # A persona cue already handed this moment to the voice agent.
                self.suppressed[trigger.name] += 1
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
            if (not trigger.bypass_gap and self.last_escalation_t is not None
                    and tick.t - self.last_escalation_t < self.timings.global_escalation_min_gap):
                self.suppressed[trigger.name] += 1
                continue

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
            described = trigger.cue(window) if trigger.cue is not None else None
            if described is not None:
                escalation.cue, escalation.cue_item, escalation.cue_topic, mode = described
                escalation.cue_mode = "question" if mode == "question" else "statement"
                episode = self._cue_episode(escalation.cue, open_episodes)
                if episode is not None:
                    escalation.episode_id = episode.id
            else:
                self._stamp_cue(escalation)

            if described is not None and self.fast_path is not None:
                outcome = self.fast_path(escalation)
                if outcome.startswith(HANDED_OFF):
                    self.fast_pathed += 1
                    self._accepted(trigger, escalation, episode, tick.t)
                    return escalation
                if outcome == REPEAT:
                    # Already said inside the agent's repeat window: the moment
                    # is spent, and there is nothing new for the clerk either.
                    if trigger.on_fired is not None:
                        trigger.on_fired(tick.t)
                    self.fast_dropped[outcome] += 1
                    self.suppressed[trigger.name] += 1
                    continue
                if outcome not in (NO_AGENT, NO_TRANSPORT):
                    # The mouth is busy: a conversation is open. Nothing is
                    # queued and the moment is not spent -- the next fresh tick
                    # still showing the cue tries again, and a lower trigger
                    # may still wake the clerk on this one.
                    self.fast_dropped[outcome] += 1
                    self.suppressed[trigger.name] += 1
                    continue
                self.fast_dropped[outcome] += 1
            if self._submit(escalation):
                self._accepted(trigger, escalation, episode, tick.t)
            return escalation
        return None

    def _accepted(self, trigger: Trigger, escalation: Escalation,
                  episode, t: float) -> None:
        """Book-keeping for an escalation that actually went somewhere."""

        self.fired[trigger.name] += 1
        self._last_trigger_t[trigger.name] = t
        if trigger.on_fired is not None:
            trigger.on_fired(t)
        self.last_escalation_t = t
        if episode is not None:
            self._escalated_episode_ids.add(episode.id)
        elif trigger.episode_kind is not None:
            self._unbound_episode_kinds.add(trigger.episode_kind)
        if trigger.cue is not None and escalation.cue:
            for name in CUE_COVERS.get(escalation.cue.split(":", 1)[0], ()):
                self._covered_until[name] = t + CUE_COVER_S

    def _cue_episode(self, key: str | None, open_episodes):
        """The open episode a cue moment belongs to, so the clerk's annotate
        labels it (the caffeine sighting gets named after the coffee)."""

        for kind in CUE_EPISODES.get((key or "").split(":", 1)[0], ()):
            episode = open_episodes.get(kind)
            if episode is not None:
                return episode
        return None

    def _stamp_cue(self, escalation: Escalation) -> None:
        """Label a clerk wake-up with the persona cue it could be about.

        The voice agent keys "say it once" on it: a `screen_sustained` wake-up
        while the coffee is still in the hand must not become a second line
        about the coffee. The label is only a label -- the agent counts it as a
        repeat only when the hand-off's topic actually names the item, so a
        screen-time nudge stamped with the coffee (or with the crowd that is in
        view on nearly every tick of the hackathon room) is still spoken.

        Order: a prop in the hand on this tick; else the prop whose moment was
        already handed off and is still live (the hands are often out of view
        for a tick, and null is unknown, not "put down"); else the crowd.
        ``cue_spent`` marks a stamp that matches a moment already spoken, which
        the agent treats as a repeat for as long as the moment lives, not only
        inside its 45 s window -- a treat held for a minute is still the treat.
        """

        try:
            cues = tick_cues(escalation.tick, self.timings.ai_max_age_ms)
        except Exception:  # pragma: no cover - a label must never cost a wake-up
            log.debug("cue stamp failed", exc_info=True)
            cues = []
        spent = self.moments.live_spent(escalation.t) if self.moments is not None else None
        held = next((c for c in cues if c.kind in HAND_CUE_KINDS), None)
        if held is not None:
            escalation.cue, escalation.cue_item = held.key, held.item
            escalation.cue_spent = spent is not None and spent[0] == held.key \
                and same_item(held.item, spent[1])
        elif spent is not None:
            escalation.cue, escalation.cue_item = spent
            escalation.cue_spent = True
        elif cues:
            escalation.cue, escalation.cue_item = cues[0].key, cues[0].item

    def stats(self) -> dict[str, object]:
        return {
            "fired": dict(self.fired),
            "dropped": self.dropped,
            "suppressed": dict(self.suppressed),
            "last_escalation_t": self.last_escalation_t,
            # Which path cues take, so /api/status shows a FAST_PATH=0 or
            # CUE_TRIGGER=0 run for what it is.
            "fast_path_enabled": self.fast_path is not None,
            "cue_trigger_enabled": any(t.name == "cue" for t in self.triggers),
            "fast_pathed": self.fast_pathed,
            "fast_dropped": dict(self.fast_dropped),
        }
