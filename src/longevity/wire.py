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
import time
from typing import Any

PROTOCOL_VERSION = 1

# --- Message types ------------------------------------------------------------

# phone -> Mac
CAPTURE = "capture"  # one per tick: frame + phone sensors (§11.2)
HELLO = "hello"      # sent once on connect; identifies the phone, carries clock offset
PONG = "pong"
ANSWER = "answer"    # what the wearer said back (ASK_DESIGN §3)

# Mac -> phone
SPEAK = "speak"      # {"text": ..., "urgency": ...} — AVSpeechSynthesizer path (A16)
AUDIO = "audio"      # pre-rendered audio bytes — ElevenLabs path (A18)
ASK = "ask"          # open the mic after the audio just sent (ASK_DESIGN §3)

# both directions
PING = "ping"        # keepalive; the phone sends one every 10 s so the Mac's
                     # INGEST_IDLE_TIMEOUT_S doesn't close an idle-but-live socket
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


# --- ask / answer (ASK_DESIGN §3) --------------------------------------------
#
# One question at a time, spoken first and listened for second. The audio itself
# still travels as today's `audio`/`speak` message; `ask` is the instruction that
# follows it, so the phone knows the utterance it just received wants a reply and
# how long to keep the mic open. Splitting the two keeps the speech path
# unchanged — a phone that ignores `ask` still speaks the question, it just never
# hears the answer, which is exactly the degradation `caps` (§8.7) negotiates.


def ask_message(
    question_id: str,
    listen_s: float,
    answer_kind: str = "yes_no",
    text: str = "",
) -> str:
    """Mac->phone. Listen for an answer to the question just spoken.

    `question_id` is echoed back on the answer and is how the Mac matches a
    transcript to the row that asked for it; a phone must never invent one.
    `listen_s` is a ceiling, not a target — the phone may stop early on a pause,
    and must start the window only once playback of the preceding audio has
    finished, or it will transcribe the glasses talking to themselves.
    `answer_kind` (`yes_no` | `count` | `free`) is a hint for on-device STT;
    `text` is the question again, for display, because the phone has no other
    copy of what it just played.
    """
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": ASK,
            "question_id": question_id,
            "listen_s": round(float(listen_s), 3),
            "answer_kind": answer_kind,
            "text": text,
        }
    )


def answer_message(
    question_id: str, text: str = "", heard: bool = True, t: float | None = None
) -> str:
    """phone->Mac. What the wearer said, transcribed on the device.

    `heard` is the phone's own verdict on whether anything was said at all, kept
    separate from an empty `text` so "the wearer stayed silent" and "STT returned
    nothing useful" are distinguishable on the Mac. Written here for the tests and
    `tools/fake_phone.py`; the real producer is Swift.

    `t` is the phone's clock and is metadata only — the Mac stamps the answer with
    its own receipt time (§8.4), because the phone is not NTP-authoritative and an
    answer labelled an hour ago would expire the moment it arrived.
    """
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": ANSWER,
            "question_id": question_id,
            "text": text,
            "heard": bool(heard),
            "t": round(time.time() if t is None else t, 3),
        }
    )


def hello_message(device: str = "phone", caps: list[str] | None = None) -> str:
    """phone->Mac, once on connect. `caps` is what this phone can do (§8.7).

    The Mac never speaks a question to a phone that did not advertise `"ask"` —
    an older build would play the question and then never open the mic, leaving a
    row open until it expired and a wearer answering into nothing.
    """
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": HELLO,
            "device": device,
            "caps": list(caps or []),
            "t": round(time.time(), 3),
        }
    )


# --- act / act_result (PLAN 4.1) ----------------------------------------------
#
# The system acts instead of nagging: the Mac decides, the phone executes one
# thing it was asked to do and says whether it worked. The phone decides nothing
# about health (invariant 5); it never sends an `act_result` it was not asked for.
#
# `kind` and its `args`, as the Mac sends them today:
#   calendar_block  {"minutes": 20, "earliest": <unix s>, "latest": <unix s>}
#                   find the first free `minutes` between the two, add the event
#   screen_shield   {"until": "HH:MM"}  shield the chosen apps until local HH:MM

# Mac -> phone
ACT = "act"                # do one thing for the wearer
# phone -> Mac
ACT_RESULT = "act_result"  # whether it happened


def act_message(act_id: str, kind: str, args: dict[str, Any] | None = None) -> str:
    """Mac->phone. Execute `kind` with `args`, then reply `act_result` with `id`.

    `id` is how the Mac matches the result to the decision that asked for it; a
    phone must echo it unchanged and never invent one.
    """
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": ACT,
            "id": act_id,
            "kind": kind,
            "args": dict(args or {}),
        }
    )


def act_result_message(act_id: str, ok: bool, detail: str = "") -> str:
    """phone->Mac. `ok` is the phone's verdict; `detail` says what it did or why not.

    Written here for the tests and `tools/fake_phone.py`; the real producer is Swift.
    """
    return json.dumps(
        {
            "v": PROTOCOL_VERSION,
            "type": ACT_RESULT,
            "id": act_id,
            "ok": bool(ok),
            "detail": detail,
        }
    )
