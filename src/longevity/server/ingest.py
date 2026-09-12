"""WebSocket ingest — the Mac end of the capture packet (PERSON_A.md A15, SPEC §11.2).

The phone is a dumb adapter; this is where its packets land. One endpoint,
`/ws/glasses`, decodes messages per `longevity.wire` and drops each capture packet into
a **one-slot mailbox**. That mailbox is the whole design:

  * **Drop, never queue** (CLAUDE.md invariant 2). `GlassesLink.put` overwrites. If the
    phone sends faster than T0 consumes — or T0 stalls behind a slow VLM call — the
    surplus packets are discarded, not buffered. A stale frame has negative value, and
    a queue would hand T0 a two-second-old frame while a fresh one sat behind it.
  * **A malformed packet never kills the socket.** `wire.decode` returns `{}` rather
    than raising, and the receive loop catches anything else. The glasses reconnecting
    is expensive — DAT availability is transient (hardware_software.md §24) — so the
    socket outliving a bad frame matters more than the bad frame.

`GlassesLink` also holds the live sockets, which is what the Mac->phone direction
(`speak`, A16/A17) will send on. That is one method, not a second module: the socket is
bidirectional (§11.4) and splitting the two directions across files would mean two
places that have to agree on which connection is current.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect

from .. import wire

log = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])

# Hard-coded into the Swift client, so it changes roughly never.
INGEST_PATH = "/ws/glasses"

# How far the phone's clock may disagree with the Mac's before its `t` is discarded in
# favour of the arrival time. The phone is not NTP-authoritative and a clock an hour out
# would put every frame outside the ring's 90 s window (§12.3) and skew every window
# Person B evaluates — for a value that only ever matters to ~100 ms.
MAX_CLOCK_SKEW_S = 60.0
INGEST_IDLE_TIMEOUT_S = float(os.environ.get("INGEST_IDLE_TIMEOUT_S", "30.0"))


@dataclass(frozen=True, slots=True)
class Packet:
    """One decoded capture packet.

    `seq` is assigned by the mailbox on arrival, Mac-side — the phone does not number
    its packets and does not need to. It exists so a consumer can ask "is this newer
    than what I last emitted?" without comparing floats from a clock it does not own.
    """

    seq: int
    t: float
    """The phone's capture timestamp (§11.2). Subject to phone/Mac clock skew."""

    recv_t: float
    """When the Mac finished decoding it. Always this machine's clock."""

    jpeg: bytes
    accel: dict[str, float] | None
    gps_speed: float | None

    accel_burst: tuple[tuple[float, float, float], ...] = ()
    """Optional raw [x, y, z] samples covering the last second (~20 at 20 Hz).

    Empty when the phone sends only the single `accel` sample. Raw, gravity included —
    the Mac derives `accel_rms` from these (§11.2: the phone computes nothing).
    """

    @property
    def transit_ms(self) -> float:
        """recv_t - t. Includes clock skew, so treat it as a smell test, not a metric."""
        return (self.recv_t - self.t) * 1000.0


class GlassesLink:
    """One-slot mailbox for inbound packets, plus the set of connected phones.

    Not locked. Everything here runs on the FastAPI event loop: the ingest coroutine
    writes, the `glasses` capture source reads, and neither yields between the read and
    the write of a field. (`FrameRing` takes a lock because it is reachable from a
    thread; nothing here is.)
    """

    def __init__(self) -> None:
        self._latest: Packet | None = None
        self._seq = 0
        self._taken_seq = 0
        self._event = asyncio.Event()
        self.clients: set[WebSocket] = set()

        # Health counters. `dropped` is the interesting one — it is invariant 2 doing
        # its job, and a nonzero value under a 1 Hz phone means T0 is falling behind.
        self.n_received = 0
        self.n_malformed = 0
        self.n_dropped = 0
        self.n_connects = 0
        self.n_disconnects = 0
        self.n_clock_fallback = 0
        self.n_pings = 0
        self.n_idle_closes = 0
        self.last_recv_t: float | None = None
        # When the most recent phone connected. A socket that only pings and never
        # sends a frame is "connected" but not streaming; health needs to know how long.
        self.last_connect_t: float | None = None

    # --- writing (ingest side) -------------------------------------------------

    def put(self, packet: Packet) -> None:
        """Replace the mailbox contents. Never appends — that is the whole point."""
        if self._latest is not None and self._latest.seq > self._taken_seq:
            self.n_dropped += 1
        self._latest = packet
        self._event.set()

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # --- reading (capture source side) -----------------------------------------

    @property
    def latest(self) -> Packet | None:
        return self._latest

    async def take_new(self, after_seq: int, timeout: float | None = None) -> Packet | None:
        """The newest packet with `seq > after_seq`, waiting up to `timeout` for one.

        Returns None on timeout rather than an old packet: "a stale frame has negative
        value", so a second with no capture in it produces no frame at all.
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            # Clear before checking, not after: a `put` landing between the check and
            # the clear would otherwise be swallowed and we would wait out the timeout
            # on a packet that had already arrived.
            self._event.clear()
            pkt = self._latest
            if pkt is not None and pkt.seq > after_seq:
                self._taken_seq = pkt.seq
                return pkt
            if deadline is None:
                await self._event.wait()
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._event.wait(), remaining)
            except (TimeoutError, asyncio.TimeoutError):
                return None

    # --- Mac -> phone (A16/A17 use this) ---------------------------------------

    async def send_text(self, message: str) -> int:
        """Push a `wire` message to every connected phone. Returns how many got it.

        Send failures are logged and the socket dropped, never raised: the speech path
        must not be able to take down capture.
        """
        sent = 0
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("ingest: send to phone failed: %s: %s", type(exc).__name__, exc)
                self.clients.discard(ws)
        return sent

    def stats(self) -> dict[str, Any]:
        latest = self._latest
        return {
            "connected": len(self.clients),
            "connects": self.n_connects,
            "disconnects": self.n_disconnects,
            "pings": self.n_pings,
            "n_idle_closes": self.n_idle_closes,
            "received": self.n_received,
            "malformed": self.n_malformed,
            "clock_fallback": self.n_clock_fallback,
            "dropped": self.n_dropped,
            "latest_seq": 0 if latest is None else latest.seq,
            "latest_age_s": None if latest is None else round(time.time() - latest.recv_t, 3),
            "connected_for_s": None if self.last_connect_t is None or not self.clients
            else round(time.time() - self.last_connect_t, 1),
            "latest_bytes": None if latest is None else len(latest.jpeg),
        }


# --- packet validation --------------------------------------------------------
#
# The phone is trusted but not assumed correct. Every field is checked, because the
# alternative is a NaN or a string propagating into the tick and surfacing three
# modules later as a mystery in Person B's gate.


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _as_accel(value: Any) -> dict[str, float] | None:
    """`{x, y, z}` of finite numbers, or None. Partial vectors are rejected whole."""
    if not isinstance(value, dict):
        return None
    out: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        f = _as_float(value.get(axis))
        if f is None:
            return None
        out[axis] = f
    return out


def _as_burst(value: Any) -> tuple[tuple[float, float, float], ...]:
    """A list of `[x, y, z]` triples, skipping any malformed entry. Never raises."""
    if not isinstance(value, list):
        return ()
    out: list[tuple[float, float, float]] = []
    for item in value[-64:]:  # bound the work; a runaway burst is a phone bug
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        triple = tuple(_as_float(v) for v in item)
        if None not in triple:
            out.append(triple)  # type: ignore[arg-type]
    return tuple(out)


def parse_capture(msg: dict[str, Any], link: GlassesLink, now: float) -> Packet | None:
    """Turn a decoded `capture` message into a `Packet`, or None if it is unusable.

    A missing or implausible `t` falls back to the Mac's receive time. The phone's
    clock is not authoritative and a frame with a garbage timestamp is still a frame —
    but a frame *labelled* an hour ago is worse than one labelled now, so a wildly
    skewed timestamp is replaced rather than trusted.
    """
    jpeg = wire.decode_image(msg)
    if not jpeg:
        return None
    t = _as_float(msg.get("t"))
    if t is None or t <= 0 or abs(t - now) > MAX_CLOCK_SKEW_S:
        if t is not None and link.n_clock_fallback == 0:
            log.warning(
                "ingest: phone clock is %.0fs off this Mac's; using arrival time for `t`",
                t - now,
            )
        link.n_clock_fallback += 1
        t = now
    return Packet(
        seq=link.next_seq(),
        t=t,
        recv_t=now,
        jpeg=jpeg,
        accel=_as_accel(msg.get("accel")),
        gps_speed=_as_float(msg.get("gps_speed")),
        accel_burst=_as_burst(msg.get("accel_burst")),
    )


# --- the endpoint -------------------------------------------------------------


def link_of(app: Any) -> GlassesLink:
    """The app's `GlassesLink`, created on first use.

    Lazy so the router works when included bare, without `attach()` — which is what
    happens if someone mounts this on their own app to poke at it.
    """
    link = getattr(app.state, "glasses_link", None)
    if link is None:
        link = GlassesLink()
        app.state.glasses_link = link
    return link


@router.websocket(INGEST_PATH)
async def glasses_ws(websocket: WebSocket) -> None:
    """Receive capture packets from the iOS bridge (A14) until the phone goes away."""
    link = link_of(websocket.app)
    await websocket.accept()
    link.clients.add(websocket)
    link.n_connects += 1
    link.last_connect_t = time.time()
    peer = websocket.client.host if websocket.client else "?"
    log.info("ingest: phone connected from %s (connection #%d)", peer, link.n_connects)

    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    websocket.receive(), timeout=INGEST_IDLE_TIMEOUT_S
                )
            except (asyncio.TimeoutError, TimeoutError):
                link.n_idle_closes += 1
                log.info("ingest: closing idle phone socket from %s", peer)
                await websocket.close(code=1001)
                break
            if event.get("type") == "websocket.disconnect":
                break
            # Text is the contract (§11.2 via wire.py), but accept a binary frame
            # carrying the same JSON — URLSessionWebSocketTask makes it easy to send
            # `.data` by accident and this is not worth a silent failure.
            raw = event.get("text")
            if raw is None:
                raw = event.get("bytes")
            if raw is None:
                continue
            try:
                _handle(link, raw)
            except Exception as exc:  # noqa: BLE001
                # One bad packet must never cost the connection.
                link.n_malformed += 1
                log.warning("ingest: dropped a packet: %s: %s", type(exc).__name__, exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("ingest: socket error from %s: %s: %s", peer, type(exc).__name__, exc)
    finally:
        link.clients.discard(websocket)
        link.n_disconnects += 1
        log.info(
            "ingest: phone disconnected from %s (%d received, %d malformed, %d dropped)",
            peer,
            link.n_received,
            link.n_malformed,
            link.n_dropped,
        )


def _handle(link: GlassesLink, raw: str | bytes) -> None:
    """Dispatch one inbound message. Synchronous and bounded — invariant 1."""
    msg = wire.decode(raw)
    if not msg:
        link.n_malformed += 1
        log.warning("ingest: undecodable message, %d bytes", len(raw))
        return

    mtype = msg.get("type")
    if mtype == wire.CAPTURE:
        packet = parse_capture(msg, link, time.time())
        if packet is None:
            link.n_malformed += 1
            log.warning("ingest: capture packet with no usable image")
            return
        link.n_received += 1
        link.last_recv_t = packet.recv_t
        link.put(packet)
    elif mtype == wire.HELLO:
        log.info("ingest: hello from phone: %s", {k: v for k, v in msg.items() if k != "image"})
    elif mtype in (wire.PONG, wire.ECHO):
        log.info("ingest: %s %s", mtype, msg.get("text", ""))
    elif mtype == wire.PING:
        link.n_pings += 1
        log.debug("ingest: ping")
    else:
        # Unknown types are forward compatibility, not errors — the Swift side may ship
        # a new message before this one knows about it.
        log.info("ingest: ignoring message of type %r", mtype)


@router.get("/ingest/stats")
def ingest_stats(request: Request) -> dict[str, Any]:
    """Ingest health. The first thing to check when the glasses path looks dead."""
    return link_of(request.app).stats()


def attach(app: FastAPI, link: GlassesLink | None = None) -> GlassesLink:
    """Mount ingest on `app` and return the link the capture source should read."""
    app.state.glasses_link = link or GlassesLink()
    app.include_router(router)
    return app.state.glasses_link
