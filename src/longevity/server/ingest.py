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
import hmac
import logging
import os
import time
from collections.abc import Callable
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
# A send to the phone that never completes (a stalled cellular link) must not hold
# the next utterance hostage: bound it, then drop the socket and let it reconnect.
PHONE_SEND_TIMEOUT_S = float(os.environ.get("PHONE_SEND_TIMEOUT_S", "5.0"))


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

        # What each connected phone said it could do in its `hello` (ASK_DESIGN
        # §8.7), keyed by `id(websocket)` because a WebSocket is not reliably
        # hashable across Starlette versions and the key only has to outlive the
        # socket itself. Cleared on disconnect: capabilities describe a
        # connection, not a phone, and a stale "ask" here would have the Mac
        # speak a question into a socket that closed.
        self.caps: dict[int, set[str]] = {}

        # Connect order, keyed the same way. `pick` prefers the newest socket:
        # when a phone reconnects without its old socket having been reaped yet,
        # the live one is the one that just said hello. A socket registered
        # without going through `add_client` (a test dropping one straight into
        # `clients`) sorts as oldest rather than being invisible.
        self._order: dict[int, int] = {}
        self._order_seq = 0

        # Which socket a sent question actually went to, question_id -> id(websocket)
        # (ASK_DESIGN §8.2, §8.7). `pick` chooses one socket for the whole exchange,
        # but with two phones connected any of them can still send an `answer` with
        # that question's id — this is what lets `_handle_answer` tell the one that
        # was actually asked from a second or stale one that was not. Recorded in
        # `send_to` when the outgoing message is an `ask`, popped once the question
        # is resolved.
        self._question_socket: dict[str, int] = {}

        #: Set by the bridge to `QuestionManager.on_answer` (ASK_DESIGN §8.11).
        #: Called synchronously with (question_id, text, heard, mac_recv_t).
        self.on_answer: Callable[[str, str, bool, float], None] | None = None

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
        self.n_answers = 0
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

    # --- capabilities ----------------------------------------------------------

    def add_client(self, websocket: Any) -> None:
        """Register a freshly accepted socket, newest-last."""
        self.clients.add(websocket)
        self._order_seq += 1
        self._order[id(websocket)] = self._order_seq

    def drop_client(self, websocket: Any) -> None:
        """Forget a socket and everything recorded about that connection."""
        self.clients.discard(websocket)
        self.caps.pop(id(websocket), None)
        self._order.pop(id(websocket), None)

    def pick(self, cap: str | None = None) -> Any | None:
        """One connected socket that advertises `cap`, or None.

        The ask path needs a *connection*, not a broadcast: the audio, the `ask`
        and the answer that comes back are one exchange, and splitting them
        across two phones would open a microphone on a device that never heard
        the question. So the caller picks once, up front, and sends everything
        to what it picked.

        Newest wins. Two live sockets means a phone reconnected and the old one
        has not been reaped yet; the one that just said hello is the one holding
        the wearer's ears. `cap=None` means any connected socket.
        """
        best: Any | None = None
        best_order = -1
        for ws in self.clients:
            if cap is not None and cap not in self.caps.get(id(ws), ()):
                continue
            order = self._order.get(id(ws), 0)
            if best is None or order > best_order:
                best, best_order = ws, order
        return best

    def supports(self, cap: str) -> bool:
        """Does any phone on this link advertise `cap`?

        "Any", not "all", because there is one phone in practice. This is the
        admission question ("could we ask at all?"); `pick` is the delivery one
        ("which socket gets this exchange?"), and they must agree — hence one
        implementation.
        """
        return self.pick(cap) is not None

    # --- Mac -> phone (A16/A17 use this) ---------------------------------------

    async def send_to(self, websocket: Any, message: str) -> bool:
        """Push one `wire` message to one socket. True iff it went out.

        The single-socket counterpart of `send_text`, with the same rule: a send
        failure drops the connection and is reported, never raised. A socket
        that just failed is a socket that is gone, so it is discarded here
        rather than left to be picked again for the next half of the exchange.
        """
        try:
            await asyncio.wait_for(websocket.send_text(message), timeout=PHONE_SEND_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("ingest: send to phone failed: %s: %s", type(exc).__name__, exc)
            self.drop_client(websocket)
            return False
        sent = wire.decode(message)
        if sent.get("type") == wire.ASK:
            question_id = sent.get("question_id")
            if isinstance(question_id, str) and question_id:
                self._question_socket[question_id] = id(websocket)
        return True

    async def send_text(self, message: str) -> int:
        """Push a `wire` message to every connected phone. Returns how many got it.

        Send failures are logged and the socket dropped, never raised: the speech path
        must not be able to take down capture.
        """
        sent = 0
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=PHONE_SEND_TIMEOUT_S)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("ingest: send to phone failed: %s: %s", type(exc).__name__, exc)
                self.drop_client(ws)
        return sent

    def stats(self) -> dict[str, Any]:
        latest = self._latest
        return {
            "connected": len(self.clients),
            "connects": self.n_connects,
            "disconnects": self.n_disconnects,
            "pings": self.n_pings,
            "n_idle_closes": self.n_idle_closes,
            "answers": self.n_answers,
            "received": self.n_received,
            "malformed": self.n_malformed,
            "clock_fallback": self.n_clock_fallback,
            "dropped": self.n_dropped,
            "latest_seq": 0 if latest is None else latest.seq,
            "latest_age_s": None if latest is None else round(time.time() - latest.recv_t, 3),
            "connected_for_s": None if self.last_connect_t is None or not self.clients
            else round(time.time() - self.last_connect_t, 1),
            "latest_bytes": None if latest is None else len(latest.jpeg),
            "caps": sorted({c for ws in self.clients for c in self.caps.get(id(ws), ())}),
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


#: Close code for a socket that presented no token or the wrong one. 4000-4999 is
#: the range RFC 6455 leaves to applications; 4401 reads as "HTTP 401, on a socket".
CLOSE_UNAUTHORIZED = 4401


def _bearer(authorization: str | None) -> str:
    """The token out of ``Authorization: Bearer <token>``; "" for any other scheme."""
    scheme, _, value = (authorization or "").strip().partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def socket_presented_token(websocket: WebSocket) -> str:
    """The one token this socket carries, by the same fixed precedence as the HTTP
    middleware (``pipeline.api.auth.presented_token``): the ``X-Access-Token`` header,
    then ``Authorization: Bearer``, then ``?token=``. The first one present is the only
    one compared; blank counts as absent. Written out here rather than imported, because
    this package does not depend on the pipeline.
    """
    return ((websocket.headers.get("x-access-token") or "").strip()
            or _bearer(websocket.headers.get("authorization"))
            or (websocket.query_params.get("token") or "").strip())


def socket_token_ok(websocket: WebSocket) -> bool:
    """Whether this socket may stream. The auth hook for a hosted backend.

    The expected token is whatever the hosting app put on ``app.state.access_token``
    (the pipeline sets it from ACCESS_TOKEN, or its legacy alias API_TOKEN); absent or
    empty means open, which is how the Mac on the venue Wi-Fi has always run. The phone
    may present it as the ``X-Access-Token`` header (URLSessionWebSocketTask can set
    one), as ``Authorization: Bearer <token>``, or as ``?token=`` in the URL (the
    simplest thing to paste into a text field); see `socket_presented_token` for the
    order. Constant-time comparison.
    """
    expected = str(getattr(websocket.app.state, "access_token", "") or "").strip()
    if not expected:
        return True
    got = socket_presented_token(websocket)
    return hmac.compare_digest(expected.encode("utf-8"), got.encode("utf-8"))


@router.websocket(INGEST_PATH)
async def glasses_ws(websocket: WebSocket) -> None:
    """Receive capture packets from the iOS bridge (A14) until the phone goes away."""
    link = link_of(websocket.app)
    await websocket.accept()
    # Accept, then close, on purpose: refusing before accept fails the handshake with a
    # bare HTTP 403 the phone cannot tell from a proxy error. Accepted first, the phone
    # reads close code 4401 and can say "wrong token" instead of "cannot connect".
    if not socket_token_ok(websocket):
        peer = websocket.client.host if websocket.client else "?"
        log.warning("ingest: refused a phone socket from %s: missing or wrong token", peer)
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="unauthorized")
        return
    link.add_client(websocket)
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
                _handle(link, raw, websocket)
            except Exception as exc:  # noqa: BLE001
                # One bad packet must never cost the connection.
                link.n_malformed += 1
                log.warning("ingest: dropped a packet: %s: %s", type(exc).__name__, exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("ingest: socket error from %s: %s: %s", peer, type(exc).__name__, exc)
    finally:
        link.drop_client(websocket)
        link.n_disconnects += 1
        log.info(
            "ingest: phone disconnected from %s (%d received, %d malformed, %d dropped)",
            peer,
            link.n_received,
            link.n_malformed,
            link.n_dropped,
        )


def _as_caps(value: Any) -> set[str]:
    """The `caps` list out of a `hello`, strings only (ASK_DESIGN §8.7).

    Anything that is not a list of strings contributes nothing. A phone
    advertising garbage is a phone whose capabilities are unknown, and unknown
    means "do not send it a question" — never an exception on the connect path.
    """
    if not isinstance(value, list):
        return set()
    return {item for item in value[:32] if isinstance(item, str) and item}


def _handle(link: GlassesLink, raw: str | bytes, websocket: Any = None) -> None:
    """Dispatch one inbound message. Synchronous and bounded — invariant 1.

    `websocket` is the connection the message arrived on, needed only to record
    per-connection capabilities; it is optional so the dispatcher stays callable
    with nothing but a link.
    """
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
        caps = _as_caps(msg.get("caps"))
        if websocket is not None:
            link.caps[id(websocket)] = caps
        log.info(
            "ingest: hello from phone: %s (caps: %s)",
            {k: v for k, v in msg.items() if k != "image"},
            ",".join(sorted(caps)) or "none",
        )
    elif mtype == wire.ANSWER:
        _handle_answer(link, msg, websocket)
    elif mtype in (wire.PONG, wire.ECHO):
        log.info("ingest: %s %s", mtype, msg.get("text", ""))
    elif mtype == wire.PING:
        link.n_pings += 1
        log.debug("ingest: ping")
    else:
        # Unknown types are forward compatibility, not errors — the Swift side may ship
        # a new message before this one knows about it.
        log.info("ingest: ignoring message of type %r", mtype)


def _handle_answer(link: GlassesLink, msg: dict[str, Any], websocket: Any = None) -> None:
    """One `answer` from the phone: validate, count, hand to the manager.

    The timestamp handed on is this Mac's receipt time, not the phone's `t`
    (ASK_DESIGN §8.4). The phone's clock is not authoritative, and an answer
    labelled a minute ago would be compared against an expiry computed on the
    Mac's clock and could resolve as "too late" for a wearer who replied at once.

    `websocket` is the connection this answer arrived on. With one phone
    connected it is always the one the question was sent to; with two, a second
    or stale connection could otherwise finalize a question it never received
    (ASK_DESIGN §8.2 picks one socket for the exchange, but `claim_answer`
    accepts by question id alone). See the `_question_socket` check below.

    The callback runs synchronously — `QuestionManager.on_answer` claims the row
    and schedules the parse, both bounded — and any exception it raises is
    swallowed here. A bug downstream must not cost the capture socket; the
    glasses reconnecting is expensive (hardware_software.md §24).
    """
    question_id = msg.get("question_id")
    if not isinstance(question_id, str) or not question_id:
        link.n_malformed += 1
        log.warning("ingest: answer with no usable question_id: %r", question_id)
        return
    text = msg.get("text", "")
    if not isinstance(text, str):
        link.n_malformed += 1
        log.warning("ingest: answer %s carried a non-string transcript", question_id)
        return
    heard = msg.get("heard", True)
    if not isinstance(heard, bool):
        # Absent means True — an older phone build sends no `heard` at all and a
        # transcript is evidence enough that the mic worked. *Present but not a
        # bool* is a different animal: the phone tried to say something about the
        # microphone and this Mac cannot tell what. Coercing it to True would
        # feed an empty transcript to the parser as "the wearer said nothing",
        # which §8.8 turns into a real answer. Malformed, and no callback.
        link.n_malformed += 1
        log.warning(
            "ingest: answer %s carried a non-boolean `heard`: %r", question_id, heard
        )
        return

    sent_to = link._question_socket.get(question_id)
    if (
        sent_to is not None
        and websocket is not None
        and id(websocket) != sent_to
        and any(id(ws) == sent_to for ws in link.clients)
    ):
        # The socket this question was actually sent to is still connected, so
        # this is a second (or stale, not-yet-reaped) client trying to finalize
        # a question it was never asked — never let it win a race with the one
        # that was. If that original socket has since disconnected, this is
        # instead the phone finishing an exchange it started before a drop —
        # "glasses off and on" (XCODE_ASK.md §7) — and must go through as before.
        log.info(
            "ingest: ignoring answer to %s from a socket it was never sent to",
            question_id,
        )
        return
    link._question_socket.pop(question_id, None)

    link.n_answers += 1
    recv_t = time.time()
    if os.environ.get("HOSTED", "").strip().lower() in {"1", "true", "yes", "on"}:
        # Hosted testers are strangers: their words go in the database they
        # consented to, not in a log line that outlives the tester.
        log.info(
            "ingest: answer to %s: heard=%s text_len=%d (phone t=%s)",
            question_id, heard, len(text), msg.get("t"),
        )
    else:
        log.info(
            "ingest: answer to %s: heard=%s %r (phone t=%s)",
            question_id, heard, text[:80], msg.get("t"),
        )
    log.debug("ingest: answer %s phone clock %s vs mac %.3f", question_id, msg.get("t"), recv_t)

    callback = link.on_answer
    if callback is None:
        log.warning("ingest: no answer handler bound; dropping answer to %s", question_id)
        return
    try:
        callback(question_id, text, heard, recv_t)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "ingest: answer handler failed for %s: %s: %s",
            question_id, type(exc).__name__, exc,
        )


@router.get("/ingest/stats")
def ingest_stats(request: Request) -> dict[str, Any]:
    """Ingest health. The first thing to check when the glasses path looks dead."""
    return link_of(request.app).stats()


def attach(app: FastAPI, link: GlassesLink | None = None) -> GlassesLink:
    """Mount ingest on `app` and return the link the capture source should read."""
    app.state.glasses_link = link or GlassesLink()
    app.include_router(router)
    return app.state.glasses_link
