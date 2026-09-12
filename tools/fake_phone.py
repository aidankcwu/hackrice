#!/usr/bin/env python3
"""A synthetic iPhone: capture packets over a WebSocket, no hardware involved.

This is what lets A15 be *finished* before A14 exists. PERSON_A.md: "Half 1 does not
need the glasses... Half 2 does not need Half 1." The two halves meet at the capture
packet, so a Python client that speaks exactly that packet — `wire.capture_packet`, not
a hand-rolled dict — closes the Python half on its own. When the real phone is then
plugged in and something breaks, the failure is isolated to the Swift half by
construction.

    uv run python tools/fake_phone.py                       # 1 Hz, seated, forever
    uv run python tools/fake_phone.py --hz 10 --count 30     # backpressure
    uv run python tools/fake_phone.py --profile walking
    uv run python tools/fake_phone.py --url ws://10.135.100.7:8765/ --hz 0.5
    uv run python tools/fake_phone.py --answer "yeah two"     # answers the first ask
    uv run python tools/fake_phone.py --caps ""               # a phone that cannot listen

It also prints anything the Mac sends back, so it doubles as a receiver for the `speak`
path while the iOS audio handler is being written.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

from longevity import wire  # noqa: E402

DEFAULT_URL = "ws://127.0.0.1:8000/ws/glasses"

# §11.4: downscale to 512 px, JPEG q70. Matching those here means the Mac-side sensor
# maths sees frames the size it will see in the demo.
FRAME_PX = 512
JPEG_QUALITY = 70


def synthetic_jpeg(seq: int, px: int = FRAME_PX) -> bytes:
    """A frame that actually changes frame to frame.

    A constant image would make `frame_delta` and `phash` look perfect for the wrong
    reason, so this drifts a gradient and slides a bright block across it. Not a scene —
    just enough structure that the sensor block produces different numbers each tick.
    """
    ramp = np.linspace(0, 255, px, dtype=np.float32)
    base = (ramp[None, :] * 0.5 + ramp[:, None] * 0.5 + seq * 3) % 255
    img = np.stack([base, np.roll(base, 40, axis=0), np.roll(base, 80, axis=1)], axis=-1)

    x = int((math.sin(seq / 6.0) * 0.4 + 0.5) * (px - 80))
    y = int((math.cos(seq / 9.0) * 0.4 + 0.5) * (px - 80))
    img[y : y + 80, x : x + 80] = 240

    img += np.random.default_rng(seq).normal(0, 6, img.shape).astype(np.float32)
    buf = io.BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(
        buf, format="JPEG", quality=JPEG_QUALITY
    )
    return buf.getvalue()


def sensors(profile: str, seq: int) -> tuple[dict[str, float] | None, float | None]:
    """Plausible raw CoreMotion / CoreLocation values for the packet (§11.2).

    Raw means gravity included — `CMAccelerometerData.acceleration`, in g, which is
    what §11.2's example vector `{0.01, -0.12, 0.98}` is. The Mac derives `accel_rms`
    from these; the phone computes nothing.
    """
    rng = random.Random(seq)
    if profile == "walking":
        # A stride puts |a| well off 1 g in both directions, a few times a second.
        swing = math.sin(seq * 2.1) * 0.35
        accel = {
            "x": round(rng.gauss(0.02, 0.12), 4),
            "y": round(rng.gauss(-0.12, 0.12) + swing, 4),
            "z": round(rng.gauss(0.98, 0.10), 4),
        }
        return accel, round(abs(rng.gauss(1.35, 0.15)), 3)  # ~1.35 m/s, walking pace
    if profile == "none":
        # GPS lost indoors and motion updates not running — both nullable in the packet.
        return None, None
    # seated: the phone is on a desk, |a| ~= 1 g with hand-tremor noise
    accel = {
        "x": round(rng.gauss(0.01, 0.012), 4),
        "y": round(rng.gauss(-0.12, 0.012), 4),
        "z": round(rng.gauss(0.98, 0.012), 4),
    }
    return accel, round(abs(rng.gauss(0.05, 0.05)), 3)


async def _print_incoming(
    ws: Any,
    quiet: bool,
    *,
    answer: str | None = None,
    answer_delay: float = 2.0,
    answer_heard: bool = True,
) -> None:
    """Drain the Mac->phone direction. On the real phone this is AVSpeechSynthesizer.

    With `--answer` it also plays the second half of ASK_DESIGN §2: the first
    `ask` message gets a reply after `answer_delay` seconds, standing in for
    playback plus on-device transcription. Only the first — the real wearer
    answers one question at a time, and a fake phone that replied to every ask
    would make the follow-up rules untestable.
    """
    answered = False
    try:
        async for raw in ws:
            msg = wire.decode(raw)
            mtype = msg.get("type")
            if mtype == wire.SPEAK and not quiet:
                print(f"  [phone speaks] {msg.get('text')!r}", flush=True)
            elif mtype == wire.ASK:
                if not quiet:
                    print(
                        f"  [phone listens] {msg.get('text')!r} "
                        f"({msg.get('answer_kind')}, {msg.get('listen_s')}s, "
                        f"{msg.get('question_id')})",
                        flush=True,
                    )
                if answer is not None and not answered:
                    answered = True
                    asyncio.create_task(
                        _reply(
                            ws, str(msg.get("question_id", "")), answer,
                            delay=answer_delay, heard=answer_heard, quiet=quiet,
                        )
                    )
            elif not quiet:
                print(f"  [phone recv] {str(raw)[:120]}", flush=True)
    except Exception:  # noqa: BLE001 — the socket closing is how this ends
        pass


async def _reply(
    ws: Any, question_id: str, text: str, *, delay: float, heard: bool, quiet: bool
) -> None:
    """Send one `answer` back after a pause. Never raises into the reader task."""
    try:
        await asyncio.sleep(delay)
        await ws.send(wire.answer_message(question_id, text, heard=heard, t=time.time()))
        if not quiet:
            print(
                f"  [phone answers] {question_id} heard={heard} {text!r}", flush=True
            )
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"  [phone answer failed] {exc}", flush=True)


async def run(
    url: str = DEFAULT_URL,
    *,
    hz: float = 1.0,
    count: int | None = None,
    duration: float | None = None,
    profile: str = "seated",
    malformed_at: tuple[int, ...] = (),
    start_seq: int = 0,
    quiet: bool = False,
    caps: tuple[str, ...] = ("ask",),
    answer: str | None = None,
    answer_delay: float = 2.0,
    answer_heard: bool = True,
    on_sent: Callable[[int, dict[str, Any]], None] | None = None,
) -> int:
    """Send capture packets until `count` or `duration` runs out. Returns packets sent.

    The send loop chases a monotonic deadline for the same reason the capture source
    does — a sleep-per-iteration drifts, and a fake phone that drifts makes the Mac's
    cadence look wrong when it is not.
    """
    period = 1.0 / hz
    sent = 0
    seq = start_seq
    loop = asyncio.get_running_loop()
    stop_at = None if duration is None else loop.time() + duration

    async with connect(url, max_size=8 * 1024 * 1024, open_timeout=10) as ws:
        if not quiet:
            print(
                f"fake_phone: connected to {url} at {hz} Hz, profile={profile}, "
                f"caps={','.join(caps) or 'none'}",
                flush=True,
            )
        # The hello is what tells the Mac this phone can open its microphone
        # (ASK_DESIGN §8.7). Without it the Mac suppresses every ask, which is
        # the correct behaviour and a confusing way to find out about a flag.
        await ws.send(wire.hello_message(device="fake_phone", caps=list(caps)))
        reader = asyncio.create_task(
            _print_incoming(
                ws, quiet, answer=answer, answer_delay=answer_delay,
                answer_heard=answer_heard,
            )
        )
        next_at = loop.time()
        try:
            while True:
                if count is not None and sent >= count:
                    break
                if stop_at is not None and loop.time() >= stop_at:
                    break

                seq += 1
                if seq in malformed_at:
                    # Deliberate garbage. The socket must survive this (A15) — the
                    # glasses reconnecting costs more than any single frame is worth.
                    await ws.send("{ this is not a capture packet ")
                    if not quiet:
                        print(f"  sent #{seq}: MALFORMED (on purpose)", flush=True)
                else:
                    jpeg = synthetic_jpeg(seq)
                    accel, gps = sensors(profile, seq)
                    t = time.time()
                    await ws.send(wire.capture_packet(t=t, jpeg=jpeg, gps_speed=gps, accel=accel))
                    sent += 1
                    if on_sent is not None:
                        on_sent(seq, {"t": t, "jpeg": len(jpeg), "accel": accel, "gps_speed": gps})
                    if not quiet:
                        print(
                            f"  sent #{seq}: jpeg {len(jpeg) // 1024} KB  "
                            f"gps_speed={gps}  accel={accel}",
                            flush=True,
                        )

                next_at = max(next_at + period, loop.time())
                delay = next_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            reader.cancel()
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic phone sending capture packets")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"default {DEFAULT_URL}")
    ap.add_argument("--hz", type=float, default=1.0)
    ap.add_argument("--count", type=int, default=None, help="stop after N packets")
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    ap.add_argument("--profile", choices=("seated", "walking", "none"), default="seated")
    ap.add_argument(
        "--malformed-at",
        default="",
        help="comma-separated packet numbers to send as garbage instead",
    )
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--caps",
        default="ask",
        help='comma-separated capabilities for the hello (default "ask"; "" for none)',
    )
    ap.add_argument(
        "--answer",
        default=None,
        help="reply to the first `ask` with this transcript, e.g. --answer 'yeah two'",
    )
    ap.add_argument("--answer-delay", type=float, default=2.0,
                    help="seconds to wait before answering (default 2)")
    ap.add_argument("--answer-heard", type=int, default=1,
                    help="0 sends heard=false — the wearer said nothing")
    args = ap.parse_args()

    bad = tuple(int(x) for x in args.malformed_at.split(",") if x.strip())
    caps = tuple(c.strip() for c in args.caps.split(",") if c.strip())
    try:
        n = asyncio.run(
            run(
                args.url,
                hz=args.hz,
                count=args.count,
                duration=args.duration,
                profile=args.profile,
                malformed_at=bad,
                quiet=args.quiet,
                caps=caps,
                answer=args.answer,
                answer_delay=args.answer_delay,
                answer_heard=bool(args.answer_heard),
            )
        )
        print(f"fake_phone: sent {n} packets", flush=True)
    except KeyboardInterrupt:
        print("\nfake_phone: stopped", flush=True)
    except OSError as exc:
        print(f"fake_phone: could not connect to {args.url}: {exc}", flush=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
