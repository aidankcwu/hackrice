"""A sound cue: a non-verbal ``act`` that costs less than speech (docs/PERCEPTION.md).

A sound is not a new action type. It is an ``act`` with ``kind: sound`` and
``args: {name: chime | tick | soft}``, dispatched by the existing act handler
(:meth:`pipeline.actions.handlers.ActionHandler.act`) and sent as
``wire.act_message``. It has its own hourly limiter, separate from speech, and a
sound next to a ``speak`` in one decision is dropped: the speak wins.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

__all__ = [
    "SOUND_KIND",
    "SOUND_NAMES",
    "SoundLimiter",
    "sound_act",
    "is_sound_act",
    "drop_sound_if_speaking",
]

SOUND_KIND = "sound"
SOUND_NAMES = ("chime", "tick", "soft")


class SoundLimiter:
    """Hourly cap on sound cues: a rolling 3600 s window, no minimum gap."""

    def __init__(self, max_per_hour: int, clock: Callable[[], float] = time.time) -> None:
        self.max_per_hour = int(max_per_hour)
        self._clock = clock
        #: Timestamps of granted cues, oldest first.
        self._granted: list[float] = []
        self.allowed = 0
        self.suppressed = 0

    def _prune(self, t: float) -> None:
        cutoff = t - 3600.0
        self._granted = [g for g in self._granted if g > cutoff]

    def allow(self, t: float | None = None) -> bool:
        """May a cue play at ``t`` (default: the clock)? A granted slot is consumed."""

        t = self._clock() if t is None else t
        self._prune(t)
        if len(self._granted) >= self.max_per_hour:
            self.suppressed += 1
            log.info(
                "sound suppressed: %d cues in the last hour, cap %d",
                len(self._granted),
                self.max_per_hour,
            )
            return False
        self._granted.append(t)
        self.allowed += 1
        return True

    def remaining(self, t: float | None = None) -> int:
        t = self._clock() if t is None else t
        self._prune(t)
        return max(0, self.max_per_hour - len(self._granted))

    def stats(self) -> dict[str, int]:
        return {
            "allowed": self.allowed,
            "suppressed": self.suppressed,
            "in_last_hour": len(self._granted),
        }


def sound_act(name: str, *, act_id: str | None = None) -> dict[str, Any]:
    """An ``act`` action row for a sound cue, in the handler's row shape.

    The handler mints the wire id itself; ``act_id`` is only set when given.
    """

    if name not in SOUND_NAMES:
        raise ValueError(f"unknown sound {name!r}; expected one of {SOUND_NAMES}")
    act: dict[str, Any] = {"type": "act", "kind": SOUND_KIND, "args": {"name": name}}
    if act_id is not None:
        act["id"] = act_id
    return act


def _field(action: Any, key: str) -> Any:
    if isinstance(action, dict):
        return action.get(key)
    return getattr(action, key, None)


def is_sound_act(action: Any) -> bool:
    """True for a sound ``act``, as a dict payload or an object with ``.kind``."""

    return _field(action, "kind") == SOUND_KIND and _field(action, "type") in (None, "act")


def drop_sound_if_speaking(actions: list) -> list:
    """Without sound acts when any ``speak`` is present; otherwise unchanged."""

    if not any(_field(a, "type") == "speak" for a in actions):
        return actions
    return [a for a in actions if not is_sound_act(a)]
