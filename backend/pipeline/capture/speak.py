"""Fire-and-forget speech return path over the glasses WebSocket."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from longevity import wire
from longevity.server.ingest import GlassesLink

log = logging.getLogger(__name__)


def make_speak_fn(link: GlassesLink) -> Callable[[str, str], None]:
    def speak(text: str, urgency: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            if not link.clients:
                log.info("speech skipped: no phone connected")
            task = loop.create_task(
                link.send_text(wire.speak_message(text, urgency)),
                name="glasses-speak",
            )
            task.add_done_callback(_consume_failure)
        except Exception:
            log.exception("could not schedule speech to phone")
    return speak


def _consume_failure(task: asyncio.Task[int]) -> None:
    try:
        task.result()
    except Exception:
        log.exception("speech send failed")
