"""Action handling and speech gating (SPEC §4.4, §4.6).

What T1 chooses, this package does: ``annotate``, ``log_insight``, ``watch``,
``speak``, ``nothing``. The speech rate limiter lives here because it is logic,
not plumbing -- we decide *whether* an utterance is emitted; Person A owns
*how* it reaches the glasses (SPEC §13.3).
"""

from __future__ import annotations

from .handlers import ActionHandler
from .speech import (
    SpeechLimiter,
    clear_spoken,
    default_speak_fn,
    get_speak_fn,
    set_speak_fn,
    spoken,
)

__all__ = [
    "ActionHandler",
    "SpeechLimiter",
    "set_speak_fn",
    "get_speak_fn",
    "default_speak_fn",
    "clear_spoken",
    "spoken",
]
