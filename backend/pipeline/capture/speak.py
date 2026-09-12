"""Fire-and-forget speech return path over the glasses WebSocket."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from longevity import wire
from longevity.server.ingest import GlassesLink

from ..config import Settings
from .tts import ElevenLabsTTS

log = logging.getLogger(__name__)


def make_speak_fn(
    link: GlassesLink, settings: Settings | None = None
) -> Callable[[str, str], None]:
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

    def speak(text: str, urgency: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            if not link.clients:
                log.info("speech skipped: no phone connected")
                return
            task = loop.create_task(
                _send(link, tts, text, urgency),
                name="glasses-speak",
            )
            task.add_done_callback(_consume_failure)
        except Exception:
            log.exception("could not schedule speech to phone")
    return speak


async def _send(
    link: GlassesLink,
    tts: ElevenLabsTTS | None,
    text: str,
    urgency: str,
) -> int:
    started = time.perf_counter()
    mode = "text"
    n_bytes = 0
    if tts is not None:
        mode = "elevenlabs"
        try:
            audio = await tts.synthesize(text)
            n_bytes = len(audio)
            sent = await link.send_text(wire.audio_message(audio, "mp3"))
        except Exception:
            mode = "text-fallback"
            log.exception("ElevenLabs TTS failed; falling back to phone speech")
            sent = await link.send_text(wire.speak_message(text, urgency))
    else:
        sent = await link.send_text(wire.speak_message(text, urgency))
    elapsed_ms = (time.perf_counter() - started) * 1000
    log.info("speech mode=%s bytes=%d ms=%.0f", mode, n_bytes, elapsed_ms)
    return sent


def _consume_failure(task: asyncio.Task[int]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        log.exception("speech send failed")
