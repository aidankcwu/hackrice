"""Fire-and-forget speech return path over the glasses WebSocket.

Two callers share one synthesiser. ``make_speak_fn`` is the fire-and-forget hook
B's action handler calls and must never block or raise; ``LongevityCapture.
send_question`` (ASK_DESIGN §8.2) needs the *same* synthesis awaited inline,
because a question has to finish playing before the ``ask`` message that opens
the microphone goes out. Both go through :class:`Speech`, so the ElevenLabs
client, the text fallback and the counters exist once per link rather than once
per caller — otherwise a spoken question would be missing from
``/api/status``'s speech block.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, asdict, field
from collections.abc import Callable

from longevity import wire
from longevity.server.ingest import GlassesLink

from ..config import Settings
from .tts import ElevenLabsTTS

log = logging.getLogger(__name__)

#: Where :func:`speech_for` stashes the live :class:`Speech` on a link.
_LINK_ATTR = "_speech"

#: How long ElevenLabs gets before the phone's own voice takes over (ASK_DESIGN
#: §8.2, §8.4). Demo night measured uncached synthesis at p50 282 ms, p90 388 ms,
#: max 994 ms, so 2.5 s is ~2.5x the worst call ever seen: past it the call is
#: hung, not slow, and every further second is dead air on stage before the
#: fallback voice speaks (it used to be 8 s, sized for the old 25 s ask window).
#: The client's own timeouts do not cover a server that dribbles bytes forever,
#: so the deadline lives here.
SYNTH_TIMEOUT_S = 2.5

#: Playback-length estimate for the mouth-busy guard (the voice agent must not
#: start a line while the last one is still playing on the glasses).
#: ``tts.py`` asks ElevenLabs for ``mp3_22050_32``: 32 kbit/s is 4000 bytes per
#: second of audio, so a clip lasts ``len(mp3) / 4000`` s (the audit's
#: measure used the same figure).
MP3_BYTES_PER_S = 4000.0
#: Text mode has no bytes: the phone's own voice reads the words at roughly
#: 0.23 s a word (~260 wpm, the iOS default rate).
TEXT_S_PER_WORD = 0.23
#: Added to every estimate: Bluetooth A2DP buffers ~0.15-0.3 s before the first
#: sample is heard, so a clip ends that much later than the bytes say.
PLAYBACK_PAD_S = 0.3


def playback_s(mode: str, text: str, n_bytes: int) -> float:
    """How long one sent utterance keeps the mouth busy, pad included."""
    if n_bytes > 0:
        return n_bytes / MP3_BYTES_PER_S + PLAYBACK_PAD_S
    return len(text.split()) * TEXT_S_PER_WORD + PLAYBACK_PAD_S


@dataclass
class SpeechStats:
    mode: str
    sent: int = 0
    skipped_no_phone: int = 0
    tts_failures: int = 0
    last_error: str | None = None
    last_ms: float | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class Speech:
    """One synthesiser bound to one link. `send` is the whole Mac->phone path."""

    link: GlassesLink
    tts: ElevenLabsTTS | None
    stats: SpeechStats = field(default_factory=lambda: SpeechStats("text"))
    #: ``clock()`` value until which the last sent clip is still playing.
    #: Monotonic, not wall time: an NTP step must not free or hold the mouth.
    busy_until: float = 0.0
    clock: Callable[[], float] = time.monotonic
    #: Utterances accepted but not yet sent (still synthesising). The mouth is
    #: busy for these too: a line handed to ``speak`` a moment ago has no byte
    #: count yet, and the next hand-off must not slip into that gap.
    _inflight: int = 0

    def reserve(self) -> None:
        """Mark an utterance as on its way before its task has even started."""
        self._inflight += 1

    def busy_for(self) -> float:
        """Seconds until the mouth is free; 0.0 when it is free now.

        An utterance still synthesising counts as busy for the rest of the
        synthesis deadline -- an upper bound, only used to say "not yet".
        """
        remaining = max(0.0, self.busy_until - self.clock())
        if self._inflight > 0:
            remaining = max(remaining, SYNTH_TIMEOUT_S)
        return remaining

    def _mark_played(self, text: str, mode: str, n_bytes: int, delivered: bool) -> None:
        if not delivered:
            return  # nobody is hearing it, so it holds nothing up
        self.busy_until = max(
            self.busy_until, self.clock() + playback_s(mode, text, n_bytes)
        )

    async def _render(self, text: str, urgency: str) -> tuple[str, str, int]:
        """The wire message for one utterance, plus `(mode, n_bytes)` for stats.

        The one place synthesis can fail, so the one place that decides what a
        failure means. A timeout is not special-cased: an ElevenLabs call that
        blew its deadline and one that raised are the same event to the wearer —
        no audio — and both fall back to the phone's own voice.

        ``asyncio.timeout`` rather than ``wait_for``: the same deadline, but it
        arms a timer on the current task instead of wrapping the call in a
        second one, so synthesis that returns immediately still finishes without
        an extra trip through the event loop.
        """
        if self.tts is None:
            return wire.speak_message(text, urgency), "text", 0
        try:
            async with asyncio.timeout(SYNTH_TIMEOUT_S):
                audio = await self.tts.synthesize(text)
        except Exception as exc:  # includes the TimeoutError from the deadline
            self.stats.tts_failures += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("ElevenLabs TTS failed; falling back to phone speech")
            return wire.speak_message(text, urgency), "text-fallback", 0
        return wire.audio_message(audio, "mp3", text), "elevenlabs", len(audio)

    def _account(self, sent: int, mode: str, n_bytes: int, started: float) -> int:
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.stats.sent += sent
        self.stats.last_ms = round(elapsed_ms, 1)
        if mode != "text-fallback":
            self.stats.last_error = None
        log.info("speech mode=%s bytes=%d ms=%.0f", mode, n_bytes, elapsed_ms)
        return sent

    async def warm(self) -> bool:
        """Pre-open the ElevenLabs connection. Never raises; False if nothing to warm.

        Only a handshake, never a synthesis: the key may be dead, and a warm-up
        that spends credit or throws would be worse than no warm-up at all.
        """
        if self.tts is None:
            return False
        try:
            return await self.tts.warm()
        except Exception:  # noqa: BLE001 -- belt and braces; tts.warm already swallows
            log.exception("speech warm-up failed")
            return False

    async def send(
        self, text: str, urgency: str = "normal", *, reserved: bool = False
    ) -> int:
        """Synthesise and push one utterance to every phone. Returns how many got it.

        Never raises: an ElevenLabs failure falls back to the phone's own
        synthesiser, and a dead socket is already swallowed by
        ``GlassesLink.send_text``. Zero means the words reached nobody.
        ``reserved``: the caller already counted this utterance with
        :meth:`reserve`, so it is only released here, not counted twice.
        """
        if not reserved:
            self._inflight += 1
        try:
            started = time.perf_counter()
            message, mode, n_bytes = await self._render(text, urgency)
            sent = await self.link.send_text(message)
            self._mark_played(text, mode, n_bytes, bool(sent))
            return self._account(sent, mode, n_bytes, started)
        finally:
            self._inflight = max(0, self._inflight - 1)

    async def send_to(self, ws: object, text: str, urgency: str = "normal") -> bool:
        """Synthesise and push one utterance to **one** socket (ASK_DESIGN §8.2).

        A question and the ``ask`` that opens the microphone for it are halves of
        one exchange and must land on the same phone, so the ask path renders for
        a socket it has already picked rather than broadcasting. Identical
        synthesis, identical accounting: ``/api/status`` counts a spoken question
        exactly as it counts a statement.
        """
        self._inflight += 1
        try:
            started = time.perf_counter()
            message, mode, n_bytes = await self._render(text, urgency)
            ok = await self.link.send_to(ws, message)
            self._mark_played(text, mode, n_bytes, bool(ok))
            self._account(1 if ok else 0, mode, n_bytes, started)
            return ok
        finally:
            self._inflight = max(0, self._inflight - 1)


def make_speech(link: GlassesLink, settings: Settings | None = None) -> Speech:
    """Build the synthesiser for `link` and make it the link's current one."""
    # Preserve the historical direct-call behaviour. Runtime wiring always passes
    # its Settings instance; callers that omit it explicitly get phone-side speech.
    settings = settings or Settings(speech_mode="text")
    use_elevenlabs = settings.speech_mode == "elevenlabs" or (
        settings.speech_mode == "auto" and bool(settings.elevenlabs_api_key)
    )
    tts = (
        ElevenLabsTTS(settings.elevenlabs_api_key or "", settings.elevenlabs_voice_id)
        if use_elevenlabs
        else None
    )
    speech = Speech(link, tts, SpeechStats("elevenlabs" if tts is not None else "text"))
    try:
        setattr(link, _LINK_ATTR, speech)
    except Exception:  # noqa: BLE001 — a link that refuses attributes still speaks
        log.debug("could not cache speech on the link; the ask path will build its own")
    return speech


def current_speech(link: GlassesLink) -> Speech | None:
    """The link's synthesiser if one has been built, without building one."""
    existing = getattr(link, _LINK_ATTR, None)
    return existing if isinstance(existing, Speech) else None


def speech_for(link: GlassesLink, settings: Settings | None = None) -> Speech:
    """The link's synthesiser, built on first use.

    The bridge resolves lazily rather than at construction because wiring builds
    the capture graph *before* it calls ``make_speak_fn`` — asking at send time is
    what makes both paths share one :class:`SpeechStats`.
    """
    existing = current_speech(link)
    return existing if existing is not None else make_speech(link, settings)


def make_speak_fn(
    link: GlassesLink, settings: Settings | None = None
) -> Callable[[str, str], bool]:
    speech = make_speech(link, settings)
    stats = speech.stats

    def speak(text: str, urgency: str) -> bool:
        """``True`` when the utterance was accepted for delivery.

        Still fire-and-forget -- ``True`` means a send was *scheduled*, not that
        the phone played it. ``False`` means nothing was, so callers that log
        "spoken" (the recap does) do not claim an utterance nobody could hear.
        The return value widens ``SpeakFn``'s ``None`` rather than replacing it:
        a hook that returns ``None`` still counts as accepted.
        """

        try:
            loop = asyncio.get_running_loop()
            if not link.clients:
                stats.skipped_no_phone += 1
                log.info("speech skipped: no phone connected")
                return False
            # Reserve synchronously: the task below has not started yet, and
            # the voice agent may be asked for the next line before it does.
            speech.reserve()
            try:
                task = loop.create_task(
                    speech.send(text, urgency, reserved=True),
                    name="glasses-speak",
                )
            except BaseException:
                speech._inflight = max(0, speech._inflight - 1)
                raise
            task.add_done_callback(_consume_failure)
            return True
        except Exception:
            log.exception("could not schedule speech to phone")
            return False
    speak.stats = stats  # type: ignore[attr-defined]
    speak.speech = speech  # type: ignore[attr-defined]
    # The voice agent's mouth-busy guard reads this: seconds until the last
    # clip on the glasses has finished playing (0.0 when the mouth is free).
    speak.busy_for = speech.busy_for  # type: ignore[attr-defined]
    # Startup hook: ``await speak.warm()`` opens the ElevenLabs socket before the
    # first moment on stage. Safe to call with a dead key or no network.
    speak.warm = speech.warm  # type: ignore[attr-defined]
    return speak


def _consume_failure(task: asyncio.Task[int]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("speech send failed")
