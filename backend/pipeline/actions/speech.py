"""Speech gating (SPEC §4.6) and the seam to Person A's TTS.

*The model proposes speech; code disposes.* A ``speak`` action passes through
:class:`SpeechLimiter` before reaching TTS, and the model never drives the
speaker directly.

The hook is the second of the two interfaces across the work seam (SPEC §13.3,
docs/API.md seam §3): we decide *whether* and *what*, Person A owns *how*. A
wires their ElevenLabs implementation in at startup with :func:`set_speak_fn`.
``speak()`` is fire-and-forget -- it must return immediately and it must never
raise into the action handler, so we catch everything it throws.

All gating is on **simulated** time (``tick.t``), never wall-clock, so
``--speed 10`` shortens a demo without desyncing the rate limiter.
"""

from __future__ import annotations

import logging
from typing import Callable, Literal

log = logging.getLogger(__name__)

__all__ = [
    "Urgency",
    "SpeakFn",
    "spoken",
    "set_speak_fn",
    "get_speak_fn",
    "default_speak_fn",
    "clear_spoken",
    "SpeechLimiter",
]

Urgency = Literal["low", "normal", "high"]
SpeakFn = Callable[[str, str], None]

#: Everything that actually reached the hook: ``(t, text, urgency)``.
#: Recorded by :meth:`SpeechLimiter.speak` regardless of which hook is wired,
#: because the dashboard wants the utterance log even once A's TTS is live.
spoken: list[tuple[float, str, str]] = []


def default_speak_fn(text: str, urgency: str) -> None:
    """Stub until Person A wires ElevenLabs in. Logs and returns."""

    log.info("SPEAK[%s]: %s", urgency, text)


_speak_fn: SpeakFn = default_speak_fn


def set_speak_fn(fn: SpeakFn) -> None:
    """Install the utterance sink. Called once at startup by A's wiring."""

    global _speak_fn
    _speak_fn = fn


def get_speak_fn() -> SpeakFn:
    return _speak_fn


def clear_spoken() -> None:
    spoken.clear()


class SpeechLimiter:
    """Rate limiter in front of TTS: a minimum gap and an hourly cap."""

    def __init__(self, min_gap_s: float, max_per_hour: int) -> None:
        self.min_gap_s = float(min_gap_s)
        self.max_per_hour = int(max_per_hour)
        #: Simulated timestamps of granted utterances, oldest first.
        self._granted: list[float] = []
        self.allowed = 0
        self.suppressed = 0

    # -- gating ----------------------------------------------------------

    def allow(self, t: float) -> bool:
        """May we speak at simulated time ``t``?

        Mutating on purpose: a granted slot is consumed here, so the caller
        cannot ask twice and speak twice.
        """

        cutoff = t - 3600.0
        self._granted = [g for g in self._granted if g > cutoff]

        if self._granted and (t - self._granted[-1]) < self.min_gap_s:
            self.suppressed += 1
            log.info(
                "speech suppressed: %.1fs since last utterance, min gap %.1fs",
                t - self._granted[-1],
                self.min_gap_s,
            )
            return False
        if len(self._granted) >= self.max_per_hour:
            self.suppressed += 1
            log.info(
                "speech suppressed: %d utterances in the last hour, cap %d",
                len(self._granted),
                self.max_per_hour,
            )
            return False

        self._granted.append(t)
        self.allowed += 1
        return True

    def grant(self, t: float) -> None:
        """Record an utterance without applying either speech guard."""

        self._granted = [g for g in self._granted if g > t - 3600.0]
        self._granted.append(t)
        self.allowed += 1

    # -- dispatch --------------------------------------------------------

    def speak(self, text: str, urgency: str = "low", t: float | None = None) -> None:
        """Hand the utterance to the seam. Never raises into the caller."""

        stamp = t if t is not None else (self._granted[-1] if self._granted else 0.0)
        spoken.append((stamp, text, urgency))
        try:
            _speak_fn(text, urgency)
        except Exception:
            log.exception("speak() hook raised; swallowing (SPEC §13.3 seam)")

    # -- introspection ---------------------------------------------------

    @property
    def last_spoken_t(self) -> float | None:
        return self._granted[-1] if self._granted else None

    def stats(self) -> dict[str, int]:
        return {
            "allowed": self.allowed,
            "suppressed": self.suppressed,
            "in_last_hour": len(self._granted),
        }
