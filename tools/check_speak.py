#!/usr/bin/env python3
"""Proof that A17 holds: `uv run python tools/check_speak.py`.

`tests/test_speak.py` pins the contract against a fake link. This drives the real one:
a live uvicorn, a real WebSocket, a real phone on the other end. Five things that
cannot be checked by staring at the code, and that a fake link cannot show either:

  1. A message handed to `speak()` actually arrives at a connected phone, intact.
  2. Nothing rate-limits it. The limiter is B's (§4.6, PERSON_A.md A17), so fifty
     utterances in a row must produce fifty messages on the wire.
  3. A phone that vanishes mid-session yields `False`, not an exception — B calls this
     from inside action handling and speech must not reach the reasoner as a crash.
  4. Speech resumes when the phone comes back, with no rebinding.
  5. Speaking does not block capture. This is invariant 1 at the one place the two
     directions of the socket meet.

Exits non-zero if any check fails.
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uvicorn  # noqa: E402
from PIL import Image  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

from longevity import speak as speak_mod  # noqa: E402
from longevity import wire  # noqa: E402
from longevity.server.app import create_app  # noqa: E402
from longevity.server.ingest import INGEST_PATH  # noqa: E402
from longevity.sources.glasses import GlassesSource  # noqa: E402

_results: list[tuple[str, bool]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    _results.append((label, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —  {detail}" if detail else ""),
          flush=True)
    return bool(ok)


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (90, 120, 160)).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


async def start_server(app: Any) -> tuple[uvicorn.Server, asyncio.Task[Any], int]:
    """Port 0, so this never collides with a real T0 on 8000."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("uvicorn did not start")
    return server, task, server.servers[0].sockets[0].getsockname()[1]


class Phone:
    """A WebSocket client that records every `speak` message it is told to say."""

    def __init__(self) -> None:
        self.heard: list[dict[str, Any]] = []
        self._task: asyncio.Task[Any] | None = None
        self._ws: Any = None

    async def __aenter__(self) -> "Phone":
        return self

    async def connect(self, url: str) -> "Phone":
        self._cm = connect(url, max_size=None)
        self._ws = await self._cm.__aenter__()
        self._task = asyncio.create_task(self._read())
        await asyncio.sleep(0.1)  # let the server register the client
        return self

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                msg = wire.decode(raw)
                if msg.get("type") == wire.SPEAK:
                    self.heard.append(msg)
        except Exception:  # noqa: BLE001 — a closed socket ends the reader, quietly
            pass

    async def send_capture(self) -> None:
        await self._ws.send(
            wire.capture_packet(t=time.time(), jpeg=jpeg(), gps_speed=0.0,
                                accel={"x": 0.01, "y": -0.12, "z": 0.98})
        )

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.15)  # let the server notice the disconnect


async def main() -> int:
    app = create_app()
    link = app.state.glasses_link
    server, server_task, port = await start_server(app)
    url = f"ws://127.0.0.1:{port}{INGEST_PATH}"

    # Exactly what main.py does at startup, and what makes `from longevity.speak
    # import speak` work for B without B knowing a socket exists.
    speaker = speak_mod.default_speaker()
    speaker.bind(link)
    print(f"\napp up on 127.0.0.1:{port}, ingest at {url}\n", flush=True)

    try:
        # --- 1: the utterance actually reaches a phone -------------------------
        print("[delivery] one utterance, through a real socket", flush=True)
        phone = await Phone().connect(url)

        text = "You have been at a screen for 90 minutes."
        ok = await speak_mod.speak(text, "low")
        await asyncio.sleep(0.15)

        check(ok is True, "speak() reports delivery", f"returned {ok}")
        check(len(phone.heard) == 1, "the phone received exactly one utterance",
              f"{len(phone.heard)} received")
        heard = phone.heard[0] if phone.heard else {}
        check(heard.get("text") == text, "the text survived the wire intact")
        check(heard.get("urgency") == "low", "B's urgency survived", f"{heard.get('urgency')!r}")

        # --- 2: nothing rate-limits ------------------------------------------
        print("\n[no rate limiting] 50 utterances back to back", flush=True)
        phone.heard.clear()
        before = speaker.spoken
        for i in range(50):
            await speak_mod.speak(f"utterance {i}")
        await asyncio.sleep(0.3)

        check(len(phone.heard) == 50, "all 50 reached the phone, none suppressed",
              f"{len(phone.heard)} arrived (a limiter here would swallow some)")
        check([m["text"] for m in phone.heard] == [f"utterance {i}" for i in range(50)],
              "they arrived in order, unmodified")
        check(speaker.spoken - before == 50, "the counter agrees",
              f"spoken +{speaker.spoken - before}")

        # --- 3: speaking does not block capture -------------------------------
        print("\n[invariant 1] capture keeps flowing while speaking", flush=True)
        source = GlassesSource(link)
        frames: list[Any] = []

        async def consume() -> None:
            async for f in source.frames():
                frames.append(f)

        consumer = asyncio.create_task(consume())
        t0 = time.monotonic()
        for _ in range(6):
            await phone.send_capture()
            await speak_mod.speak("still talking")
            await asyncio.sleep(0.2)
        elapsed = time.monotonic() - t0
        await asyncio.sleep(0.3)
        await source.aclose()
        consumer.cancel()

        check(len(frames) >= 2, "frames still arrived while utterances were in flight",
              f"{len(frames)} frames over {elapsed:.2f}s")
        check(elapsed < 2.5, "speaking never stalled the capture loop",
              f"6 send+speak rounds took {elapsed:.2f}s")

        # --- 4: the phone vanishes -------------------------------------------
        print("\n[no phone] the glasses come off mid-session", flush=True)
        await phone.close()
        before_undelivered = speaker.undelivered

        try:
            result = await speak_mod.speak("nobody is listening")
            raised = None
        except Exception as exc:  # noqa: BLE001
            result, raised = None, exc

        check(raised is None, "a missing phone does not raise into B's reasoner",
              "no exception" if raised is None else f"raised {type(raised).__name__}")
        check(result is False, "it reports False instead", f"returned {result}")
        check(speaker.undelivered > before_undelivered, "the drop is counted, not hidden",
              f"undelivered={speaker.undelivered}")

        # --- 5: it comes back -------------------------------------------------
        print("\n[reconnect] the phone returns", flush=True)
        phone2 = await Phone().connect(url)
        ok = await speak_mod.speak("welcome back")
        await asyncio.sleep(0.15)

        check(ok is True, "speech resumes with no rebinding",
              "the same Speaker instance, still bound to the same link")
        check(any(m["text"] == "welcome back" for m in phone2.heard),
              "the returning phone hears it", f"{len(phone2.heard)} received")
        await phone2.close()

    finally:
        server.should_exit = True
        await asyncio.sleep(0.2)
        server_task.cancel()

    failed = [name for name, ok in _results if not ok]
    print()
    if failed:
        print(f"FAIL — {len(failed)}/{len(_results)} checks failed: {', '.join(failed)}")
        return 1
    print(f"PASS — all {len(_results)} checks passed. A17 is done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
