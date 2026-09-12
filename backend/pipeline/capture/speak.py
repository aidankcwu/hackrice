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
#: §8.2, §8.4). `ask_expire_s` (25 s) is budgeted as synthesis <= 8 + playback ~5
#: + listen 8 + slack, so an HTTP call that hangs past 8 s has already eaten the
#: window it was rendering audio *for*. The client's own timeouts do not cover a
#: server that dribbles bytes forever, so the deadline lives here.
SYNTH_TIMEOUT_S = 8.0


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
        return wire.audio_message(audio, "mp3"), "elevenlabs", len(audio)

    def _account(self, sent: int, mode: str, n_bytes: int, started: float) -> int:
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.stats.sent += sent
        self.stats.last_ms = round(elapsed_ms, 1)
        if mode != "text-fallback":
            self.stats.last_error = None
        log.info("speech mode=%s bytes=%d ms=%.0f", mode, n_bytes, elapsed_ms)
        return sent

    async def send(self, text: str, urgency: str = "normal") -> int:
        """Synthesise and push one utterance to every phone. Returns how many got it.

        Never raises: an ElevenLabs failure falls back to the phone's own
        synthesiser, and a dead socket is already swallowed by
        ``GlassesLink.send_text``. Zero means the words reached nobody.
        """
        started = time.perf_counter()
        message, mode, n_bytes = await self._render(text, urgency)
        return self._account(
            await self.link.send_text(message), mode, n_bytes, started
        )

    async def send_to(self, ws: object, text: str, urgency: str = "normal") -> bool:
        """Synthesise and push one utterance to **one** socket (ASK_DESIGN §8.2).

        A question and the ``ask`` that opens the microphone for it are halves of
        one exchange and must land on the same phone, so the ask path renders for
        a socket it has already picked rather than broadcasting. Identical
        synthesis, identical accounting: ``/api/status`` counts a spoken question
        exactly as it counts a statement.
        """
        started = time.perf_counter()
        message, mode, n_bytes = await self._render(text, urgency)
        ok = await self.link.send_to(ws, message)
        self._account(1 if ok else 0, mode, n_bytes, started)
        return ok


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
) -> Callable[[str, str], None]:
    speech = make_speech(link, settings)
    stats = speech.stats

    def speak(text: str, urgency: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            if not link.clients:
                stats.skipped_no_phone += 1
                log.info("speech skipped: no phone connected")
                return
            task = loop.create_task(
                speech.send(text, urgency),
                name="glasses-speak",
            )
            task.add_done_callback(_consume_failure)
        except Exception:
            log.exception("could not schedule speech to phone")
    speak.stats = stats  # type: ignore[attr-defined]
    speak.speech = speech  # type: ignore[attr-defined]
    return speak


def _consume_failure(task: asyncio.Task[int]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("speech send failed")
