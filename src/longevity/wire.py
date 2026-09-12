"""The phone <-> Mac WebSocket contract.

SPEC §11.2 shows a bare capture packet with no envelope, but the socket is explicitly
bidirectional (§11.4) and the Mac->phone direction needs at least a `speak` message. So
every message in both directions carries a `type` discriminator. This module is the
only place that shape is written down; the Swift side must match it exactly.

§11.2 shows `"image": "<jpeg bytes>"` without saying whether that is base64 or a binary
frame. We use base64 inside the JSON text message. At 1 Hz and ~40 KB per JPEG the
uplink is ~40 KB/s (§11.1) and base64's 33% overhead takes it to ~53 KB/s, which is
nothing on a LAN. One self-describing message beats correlating a binary frame with a
separate JSON header.
"""

from __future__ import annotations

import base64
import json
from typing import Any

PROTOCOL_VERSION = 1

# --- Message types ------------------------------------------------------------

# phone -> Mac
CAPTURE = "capture"  # one per tick: frame + phone sensors (§11.2)
HELLO = "hello"      # sent once on connect; identifies the phone, carries clock offset
PONG = "pong"

# Mac -> phone
SPEAK = "speak"      # {"text": ..., "urgency": ...} — AVSpeechSynthesizer path (A16)
AUDIO = "audio"      # pre-rendered audio bytes — ElevenLabs path (A18)
PING = "ping"

# both directions
ECHO = "echo"        # A11 only: prove the socket before adding payload


def capture_packet(
    *,
    t: float,
    jpeg: bytes,
    gps_speed: float | None,
    accel: dict[str, float] | None,
    accel_burst: list[list[float]] | None = None,
) -> str:
    """Build the phone->Mac capture packet (§11.2). Written for tests and the fake phone.

    The real producer is Swift; this exists so the Python side can be exercised end to
    end without an iPhone, which is how A15 gets finished before A14 does.

    `accel_burst` is optional and additive: ~20 raw [x, y, z] samples covering the last
    second, at ~500 bytes against a 53 KB packet. At 1 Hz a single accelerometer sample
    cannot distinguish "moving" from "tilted", so without the burst `accel_rms` needs
    an 8-second window and cannot see a step or a head turn until seconds later. The
    phone still computes nothing from them (§11.2) — these are raw samples.
    """
    packet: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": CAPTURE,
        "t": round(t, 3),
        "image": base64.b64encode(jpeg).decode("ascii"),
        "gps_speed": gps_speed,
        "accel": accel,
    }
    if accel_burst:
        packet["accel_burst"] = accel_burst
    return json.dumps(packet)


def speak_message(text: str, urgency: str = "normal") -> str:
    """Mac->phone. The phone speaks this with AVSpeechSynthesizer (A16)."""
    return json.dumps(
        {"v": PROTOCOL_VERSION, "type": SPEAK, "text": text, "urgency": urgency}
    )


def audio_message(data: bytes, fmt: str = "mp3") -> str:
    """Mac->phone. Pre-rendered audio, the ElevenLabs upgrade path (A18)."""
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": AUDIO,
            "format": fmt,
            "data": base64.b64encode(data).decode("ascii"),
        }
    )


def echo_message(text: str) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "type": ECHO, "text": text})


def decode(raw: str | bytes) -> dict[str, Any]:
    """Parse an inbound message. Returns {} on anything malformed, never raises.

    A malformed packet from the phone must not take down the ingest socket — the
    glasses reconnecting is expensive, and invariant 1 says T0 never blocks.
    """
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return msg if isinstance(msg, dict) else {}


def decode_image(msg: dict[str, Any]) -> bytes | None:
    """Pull the JPEG out of a capture packet. None if absent or not valid base64."""
    img = msg.get("image")
    if not isinstance(img, str):
        return None
    try:
        return base64.b64decode(img, validate=True)
    except (ValueError, TypeError):
        return None
