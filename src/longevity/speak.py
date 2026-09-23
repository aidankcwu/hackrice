"""`speak(text, urgency)` — the utterance handoff (docs/PERSON_A.md A17, SPEC §13.3).

One of exactly two interfaces between A and B. §13.3: "B calls an in-process function A
owns. A synthesizes, ships the bytes to the phone, and plays them. **B decides whether
and what; A owns how.**"

So what is *not* here, deliberately:

  * **No rate limiting.** §4.6 says "the model proposes speech; code disposes", and
    docs/PERSON_A.md A17 puts that limiter on B's side — it is logic, not plumbing. If this
    module silently swallowed utterances, B could not tell a rate-limited decision from
    a broken socket, and the silent-decision feed on the dashboard would lie.
  * **No decision about whether to speak.** Called means say it.

The route is laptop -> phone -> Bluetooth A2DP -> glasses (§11.7: "Never laptop ->
glasses"). The phone speaks it with AVSpeechSynthesizer; A18 would swap the payload for
pre-rendered ElevenLabs bytes without changing this signature.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from . import wire

if TYPE_CHECKING:
    from .server.ingest import GlassesLink

log = logging.getLogger(__name__)

# §4.4 delivers utterances "with urgency level". The phone currently ignores it; it
# rides along so B's intent survives to the point where it starts mattering (voice
# choice, interrupting an in-progress utterance).
URGENCIES = ("low", "normal", "high")
DEFAULT_URGENCY = "normal"


class Speaker:
    """Owns the Mac->phone speech path. One per process, bound to the ingest link."""

    def __init__(self, link: "GlassesLink | None" = None) -> None:
        self._link = link
        self.spoken = 0
        self.undelivered = 0

    def bind(self, link: "GlassesLink") -> None:
        """Point at the live ingest link. Called by main.py once ingest is attached."""
        self._link = link

    async def speak(self, text: str, urgency: str = DEFAULT_URGENCY) -> bool:
        """Say `text` through the glasses. True if a phone actually got it.

        Never raises. B calls this from inside its action handling, and a dead socket
        or an absent phone must not propagate up into the reasoner — the trigger gate
        and episode builder keep running whether or not anyone is wearing the glasses.
        """
        text = (text or "").strip()
        if not text:
            return False

        if urgency not in URGENCIES:
            log.warning("speak: unknown urgency %r, treating as %r", urgency, DEFAULT_URGENCY)
            urgency = DEFAULT_URGENCY

        if self._link is None:
            self.undelivered += 1
            log.warning("speak: no ingest link bound — dropped %r", text[:60])
            return False

        try:
            delivered = await self._link.send_text(wire.speak_message(text, urgency))
        except Exception as exc:  # noqa: BLE001 — speech must never break capture
            self.undelivered += 1
            log.warning("speak: send failed: %s: %s", type(exc).__name__, exc)
            return False

        if delivered:
            self.spoken += 1
            log.info("speak[%s] -> %d phone(s): %s", urgency, delivered, text[:80])
            return True

        # Distinct from a failure: the socket is fine, nobody is listening. B may want
        # to log the decision rather than retry it — the moment has usually passed.
        self.undelivered += 1
        log.info("speak[%s] dropped, no phone connected: %s", urgency, text[:80])
        return False

    def stats(self) -> dict[str, Any]:
        return {
            "spoken": self.spoken,
            "undelivered": self.undelivered,
            "bound": self._link is not None,
        }


# --- the process-wide speaker --------------------------------------------------

_DEFAULT: Speaker | None = None


def default_speaker() -> Speaker:
    """The one speaker. Mirrors `ring.default_ring()` — same single-process reasoning."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Speaker()
    return _DEFAULT


async def speak(text: str, urgency: str = DEFAULT_URGENCY) -> bool:
    """Module-level convenience — this is what B imports.

        from longevity.speak import speak
        await speak("You have been at a screen for 90 minutes.", urgency="low")
    """
    return await default_speaker().speak(text, urgency)
