#!/usr/bin/env python3
"""A11 (Python half) — prove the socket before adding payload.

docs/PERSON_A.md calls A11 the worst of the two straddling tasks: without
`NSLocalNetworkUsageDescription` and `NSAllowsLocalNetworking` in the iOS Info.plist,
the WebSocket fails *silently* in a way that reads exactly like a backend bug. So this
server exists to be loud. It is standalone — no FastAPI, no app state, nothing from the
T0 pipeline — so that when the phone cannot connect, the only two suspects left are the
plist and the IP address.

    uv run python tools/echo_server.py

What it does:
  * binds 0.0.0.0:8765, so a phone on the same Wi-Fi can reach it
  * logs every inbound message with timestamp, type and byte size — and for a capture
    packet the *decoded JPEG* size, never the base64 blob
  * echoes text back, which proves phone -> Mac and Mac -> phone in one round trip
  * sends anything you type on stdin to the phone as a `speak` message (§13.3 / A16),
    so the same tool covers "typing a string on the Mac produces speech in the glasses"

The message shapes come from `longevity.wire` — this tool does not invent its own, or
A11 would prove a protocol A14 does not speak.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Run straight from a checkout without an editable install: tools/ -> repo root -> src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from websockets.asyncio.server import ServerConnection, serve  # noqa: E402

from longevity import wire  # noqa: E402

# Fixed, so it can be hard-coded in Swift and written on a whiteboard. The ingest
# server (A15) is a different process on 8000 — this one is deliberately separate.
PORT = 8765

# Capture packets are ~53 KB of base64 (§11.1 plus base64's 33%). 4 MB of headroom
# means a fat frame logs as a fat frame instead of dropping the connection.
MAX_MESSAGE_BYTES = 4 * 1024 * 1024

BANNER = "=" * 72


def lan_ip() -> str:
    """Best guess at this Mac's LAN address — what the phone must actually dial.

    No packets are sent; connecting a UDP socket just asks the routing table which
    interface would be used. Falls back to the address hardware_software.md recorded.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return str(s.getsockname()[0])
    except OSError:
        return "10.135.100.7"
    finally:
        s.close()


def stamp() -> str:
    return time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"


def log(line: str) -> None:
    print(f"[{stamp()}] {line}", flush=True)


def loud(line: str) -> None:
    """Connect/disconnect events. Someone is debugging hardware — clarity over elegance."""
    print(f"\n{BANNER}\n  {line}\n{BANNER}", flush=True)


def describe(raw: str | bytes) -> tuple[str, str]:
    """(type, human-readable detail) for one inbound message.

    A capture packet is summarised by its decoded JPEG size. Dumping 53 KB of base64
    into the terminal would bury exactly the information being debugged.
    """
    nbytes = len(raw if isinstance(raw, bytes) else raw.encode("utf-8", "replace"))
    if isinstance(raw, bytes):
        return "binary", f"{nbytes} B on the wire (no JSON envelope)"

    msg: dict[str, Any] = wire.decode(raw)
    if not msg:
        preview = raw[:80].replace("\n", " ")
        return "malformed", f"{nbytes} B on the wire, not JSON: {preview!r}"

    mtype = str(msg.get("type", "<no type>"))
    if mtype == wire.CAPTURE:
        jpeg = wire.decode_image(msg)
        jpeg_desc = "image MISSING or not base64" if jpeg is None else f"jpeg {len(jpeg)} B"
        return mtype, (
            f"{nbytes} B on the wire, {jpeg_desc}, "
            f"t={msg.get('t')} gps_speed={msg.get('gps_speed')} accel={msg.get('accel')}"
        )
    if mtype == wire.ECHO:
        return mtype, f"{nbytes} B on the wire, text={msg.get('text')!r}"
    if mtype == wire.HELLO:
        return mtype, f"{nbytes} B on the wire, {msg}"
    return mtype, f"{nbytes} B on the wire, keys={sorted(msg)}"


class EchoServer:
    """Tracks connected phones so stdin lines have somewhere to go."""

    def __init__(self) -> None:
        self.clients: set[ServerConnection] = set()
        self.n_connects = 0
        self.n_messages = 0

    async def handle(self, ws: ServerConnection) -> None:
        peer = f"{ws.remote_address[0]}:{ws.remote_address[1]}"
        path = getattr(ws.request, "path", "/")
        self.clients.add(ws)
        self.n_connects += 1
        loud(f"PHONE CONNECTED  {peer}  path={path}  (connection #{self.n_connects})")
        try:
            async for raw in ws:
                self.n_messages += 1
                mtype, detail = describe(raw)
                log(f"#{self.n_messages:<5} recv  type={mtype:<10} {detail}")
                await self._reply(ws, raw, mtype)
        except Exception as exc:  # noqa: BLE001 — a dead socket is news, not a crash
            log(f"connection error from {peer}: {type(exc).__name__}: {exc}")
        finally:
            self.clients.discard(ws)
            loud(f"PHONE DISCONNECTED  {peer}  ({len(self.clients)} still connected)")

    async def _reply(self, ws: ServerConnection, raw: str | bytes, mtype: str) -> None:
        """Echo something back for every message, so both directions are proven."""
        if isinstance(raw, bytes):
            reply = wire.echo_message(f"got {len(raw)} binary bytes")
        elif mtype == wire.CAPTURE:
            jpeg = wire.decode_image(wire.decode(raw))
            reply = wire.echo_message(f"capture ack: jpeg {0 if jpeg is None else len(jpeg)} B")
        elif mtype == "malformed":
            reply = wire.echo_message("that was not JSON, but the socket is fine")
        else:
            text = wire.decode(raw).get("text")
            reply = wire.echo_message(f"echo: {text if text is not None else mtype}")
        await ws.send(reply)
        log(f"      send  type=echo       {len(reply)} B")

    async def speak(self, text: str) -> None:
        """Push a `speak` message to every connected phone (A16)."""
        if not self.clients:
            log("no phone connected — nothing to speak to")
            return
        msg = wire.speak_message(text)
        for ws in list(self.clients):
            try:
                await ws.send(msg)
                log(f"      send  type=speak      {len(msg)} B  text={text!r}")
            except Exception as exc:  # noqa: BLE001
                log(f"speak failed: {type(exc).__name__}: {exc}")


def start_stdin_reader(server: EchoServer, loop: asyncio.AbstractEventLoop) -> None:
    """Read stdin on a thread and hand each line to the event loop as a `speak`.

    A thread rather than `loop.add_reader`, because this has to behave when stdin is a
    pipe or closed entirely — `check_glasses.py`-style automation should not hang here.
    """

    def pump() -> None:
        for line in sys.stdin:
            text = line.strip()
            if text:
                asyncio.run_coroutine_threadsafe(server.speak(text), loop)

    threading.Thread(target=pump, name="stdin-speak", daemon=True).start()


async def main() -> None:
    ap = argparse.ArgumentParser(description="A11 WebSocket echo server")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()

    ip = lan_ip()
    server = EchoServer()
    print(
        f"\n{BANNER}\n"
        f"  A11 ECHO SERVER — listening on {args.host}:{args.port}\n"
        f"{BANNER}\n"
        f"  From the phone, dial the LAN IP. NOT localhost, NOT 127.0.0.1:\n"
        f"\n"
        f"      ws://{ip}:{args.port}/\n"
        f"\n"
        f"  (any path works here; the real ingest endpoint is /ws/glasses on :8000)\n"
        f"\n"
        f"  If the phone connects and nothing appears below, the bug is on iOS:\n"
        f"    1. Info.plist needs NSLocalNetworkUsageDescription\n"
        f"    2. Info.plist needs NSAppTransportSecurity -> NSAllowsLocalNetworking\n"
        f"  Without those two, URLSessionWebSocketTask fails SILENTLY and reads\n"
        f"  exactly like a backend bug. Mac and phone must be on the same Wi-Fi.\n"
        f"\n"
        f"  Type a line here + Enter to send it to the phone as a `speak` message.\n"
        f"  Ctrl-C to stop.\n"
        f"{BANNER}\n",
        flush=True,
    )

    start_stdin_reader(server, asyncio.get_running_loop())
    async with serve(
        server.handle,
        args.host,
        args.port,
        max_size=MAX_MESSAGE_BYTES,
        ping_interval=20,
    ):
        await asyncio.Future()  # serve forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.", flush=True)
