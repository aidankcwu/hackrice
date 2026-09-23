"""The `glasses` capture adapter — the demo path (docs/PERSON_A.md A15, SPEC §11.3).

Reads the one-slot mailbox that `server/ingest.py` fills from the phone's WebSocket and
yields the newest packet as a `Frame` at 1 Hz. Nothing here touches a socket: ingest
owns the connection, this owns the cadence, and the mailbox between them is where
"drop, never queue" is enforced.

Two jobs beyond passing bytes along.

**Cadence.** The loop targets a monotonic deadline rather than sleeping a second per
iteration, because `sleep(1)` in a loop accumulates drift and you notice at minute
twelve, not minute one. If a second goes by with no packet in it, this yields *nothing*
for that second instead of re-emitting the last frame. A repeated frame would look like
a still scene to `frame_delta` and quietly corrupt the very metric it feeds.

**The `device` block.** §11.2 is explicit that "the phone computes nothing from them" —
raw `accel: {x,y,z}` and `gps_speed` arrive, and §12's `device: {accel_rms, gps_speed}`
is derived here, Mac-side. See `AccelWindow` for why that derivation is not the obvious
one-liner.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..server.ingest import GlassesLink, Packet
from .base import CaptureSource, Frame

log = logging.getLogger(__name__)

TICK_INTERVAL_S = 1.0

# Rolling window for `accel_rms`: 8 samples / 8 seconds. At the 1 Hz packet rate those
# are the same number, and both bounds are applied so the window still means eight
# seconds if the phone's rate ever changes.
#
# Eight seconds is a compromise. Shorter and a single hand gesture spikes it; longer
# and standing up takes ten seconds to show. §7's activity metrics are about posture
# over minutes, so smoothing towards the long side is the safer error.
ACCEL_WINDOW_N = 8
ACCEL_WINDOW_S = 8.0

# Standard gravity, in the units CoreMotion reports (g, not m/s²).
G = 1.0

# A packet older than this at emit time is skipped rather than emitted late. Measured
# against the Mac's receive clock, never the phone's `t`, which carries clock skew.
MAX_PACKET_AGE_S = 2.0


class AccelWindow:
    """Rolling `accel_rms` — the one real design decision in this adapter.

    §12's example shows `accel_rms: 0.04` for a seated user, while §11.2's example raw
    vector `{x: 0.01, y: -0.12, z: 0.98}` has magnitude 0.988. A naive RMS of the raw
    vector gives ~0.57 — for a person sitting perfectly still. The difference is
    gravity: `CMAccelerometerData.acceleration` includes the 1 g the Earth applies, and
    an instantaneous RMS of a single sample measures orientation, not movement.

    So `accel_rms` is the RMS of the *gravity-removed magnitude deviation* over a
    rolling window:

        dev_i    = |a_i| - 1g
        accel_rms = sqrt( mean( dev_i² ) )   over the last 8 s

    Still: |a| ≈ 1 g, dev ≈ 0, rms ≈ 0.01–0.05, which is the order §12 shows. Walking
    puts |a| well off 1 g on both sides and the rms rises accordingly. Taking the
    magnitude first also makes this orientation-independent — the phone in a pocket
    upside down reads the same as one on a desk, which per-axis maths would not.

    This assumes the phone sends **raw accelerometer** samples (gravity included), as
    §11.2's example vector does. If A13 ever switches to `deviceMotion.userAcceleration`
    (gravity already removed) every magnitude drops to ~0 and this would report a
    constant ~1.0; `_check_units` warns once if it sees that.

    ## Window length depends on what the phone sends

    With one sample per packet the window has to span 8 seconds just to collect eight
    points, which blurs a step or a head turn across most of a tick's neighbours. When
    the phone includes `accel_burst` (~20 samples/second) the same eight points cover
    under half a second, so the window shrinks to 2.5 s and `accel_rms` becomes a
    measure of *this moment* rather than of the last eight seconds. The mode switches
    automatically on the first burst received.
    """

    BURST_WINDOW_S = 2.5

    def __init__(self, n: int = 160, window_s: float = ACCEL_WINDOW_S) -> None:
        self._samples: deque[tuple[float, float]] = deque(maxlen=n)  # (t, |a|)
        self.window_s = window_s
        self._warned_units = False
        self.burst_mode = False

    @staticmethod
    def _magnitude(x: float, y: float, z: float) -> float:
        return math.sqrt(x * x + y * y + z * z)

    def add(self, accel: dict[str, float] | None, t: float) -> None:
        """Record one raw accelerometer vector. A missing sensor simply adds nothing."""
        if not accel:
            return
        self._samples.append((t, self._magnitude(accel["x"], accel["y"], accel["z"])))
        self._check_units()

    def add_burst(self, burst: Sequence[Sequence[float]], t_end: float) -> None:
        """Record a burst of raw samples ending at `t_end`.

        The phone does not timestamp individual samples (§11.2 keeps it dumb), so they
        are spread evenly backwards over one second. That is accurate enough: what the
        RMS needs is the spread of magnitudes, not when each one occurred.
        """
        if not burst:
            return
        if not self.burst_mode:
            self.burst_mode = True
            self.window_s = self.BURST_WINDOW_S
            log.info("accel: burst mode (%d samples/packet), window -> %.1fs",
                     len(burst), self.window_s)
        step = 1.0 / len(burst)
        for i, sample in enumerate(burst):
            t = t_end - (len(burst) - 1 - i) * step
            self._samples.append((t, self._magnitude(*sample)))
        self._check_units()

    def __len__(self) -> int:
        return len(self._samples)

    def rms(self, now: float) -> float | None:
        """RMS of (|a| - 1g) over the window, or None if no sample is recent enough."""
        live = [m for t, m in self._samples if now - t <= self.window_s]
        if not live:
            return None
        return math.sqrt(sum((m - G) ** 2 for m in live) / len(live))

    def _check_units(self) -> None:
        """Warn once if the phone looks like it is sending gravity-free acceleration."""
        if self._warned_units or len(self._samples) < 8:
            return
        mean_mag = sum(m for _, m in self._samples) / len(self._samples)
        if mean_mag < 0.5:
            self._warned_units = True
            log.warning(
                "accel magnitudes average %.3f g, expected ~1.0 — the phone is probably "
                "sending userAcceleration (gravity removed). accel_rms assumes raw "
                "CMAccelerometerData.acceleration and will read ~1.0 constantly.",
                mean_mag,
            )


def device_block(accel_rms: float | None, gps_speed: float | None) -> dict[str, Any]:
    """The §12 `device` block. Present under `glasses` even when a sensor is not.

    Unknown values are **omitted, not null**, matching how `tick.build_tick` treats the
    blocks above it: absence reads as "no reading", where a null invites B to do
    arithmetic on it. B reads ticks defensively anyway (A1), so `.get("gps_speed", 0.0)`
    is the natural read and it does the right thing here.
    """
    block: dict[str, Any] = {}
    if accel_rms is not None:
        block["accel_rms"] = round(accel_rms, 4)
    if gps_speed is not None:
        block["gps_speed"] = round(gps_speed, 3)
    return block


class GlassesSource(CaptureSource):
    """Yields the newest packet the phone has sent, at 1 Hz, with `device` populated."""

    name = "glasses"

    def __init__(
        self,
        link: GlassesLink,
        *,
        interval: float = TICK_INTERVAL_S,
        max_packet_age_s: float = MAX_PACKET_AGE_S,
    ) -> None:
        self._link = link
        self._interval = interval
        self._max_age = max_packet_age_s
        self._accel = AccelWindow()
        self._closed = False
        # Whatever is already sitting in the mailbox arrived before this source existed,
        # so it is stale by definition and gets skipped. Normally zero: T0 starts long
        # before the phone connects.
        self._last_seq = link.latest.seq if link.latest is not None else 0

        # Counters, for `check_glasses.py` and for the health line during the demo.
        self.n_emitted = 0
        self.n_idle = 0   # seconds that passed with no packet to emit
        self.n_stale = 0  # packets skipped for arriving too late to be worth emitting

    @property
    def link(self) -> GlassesLink:
        """The mailbox this source reads; T0 hangs the watcher's packet hook on it."""
        return self._link

    async def frames(self) -> AsyncIterator[Frame]:
        """Emit one `Frame` per interval, skipping intervals with nothing fresh in them."""
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        idle_logged_at = 0.0

        while not self._closed:
            now = loop.time()
            if now < next_at:
                await asyncio.sleep(next_at - now)

            packet = await self._link.take_new(self._last_seq, timeout=self._interval)

            # The deadline advances by exactly one interval from the previous target,
            # so cadence is anchored to the start of the run and cannot drift. `max`
            # resets the phase after a stall instead of firing a catch-up burst.
            next_at = max(next_at + self._interval, loop.time())

            if packet is None:
                self.n_idle += 1
                if loop.time() - idle_logged_at > 5.0:
                    idle_logged_at = loop.time()
                    log.info("glasses: no packet for %.0fs — is the phone sending?", self._interval)
                continue

            self._last_seq = packet.seq
            age = time.time() - packet.recv_t
            if age > self._max_age:
                self.n_stale += 1
                log.info("glasses: skipped a packet %.1fs old (stale frames have negative value)", age)
                continue

            self.n_emitted += 1
            yield self._to_frame(packet)

    def _to_frame(self, packet: Packet) -> Frame:
        """Packet -> `Frame`, deriving `device` on the way through.

        `Frame.device` carries the **derived** §12 block, which is what `build_tick`
        copies straight into the tick; the raw sensor payload rides in `meta`, which
        base.py says never reaches the tick. That keeps the derivation in one place —
        here, "the glasses adapter's job" per base.py — rather than in tick assembly.
        """
        now = time.time()
        if packet.accel_burst:
            self._accel.add_burst(packet.accel_burst, packet.recv_t)
        else:
            self._accel.add(packet.accel, packet.recv_t)
        return Frame(
            t=packet.t,
            jpeg=packet.jpeg,
            device=device_block(self._accel.rms(now), packet.gps_speed),
            meta={
                "source": self.name,
                "seq": packet.seq,
                "recv_t": packet.recv_t,
                "transit_ms": round(packet.transit_ms, 1),
                "accel_raw": packet.accel,
                "accel_burst_n": len(packet.accel_burst),
                "accel_samples": len(self._accel),
            },
        )

    async def aclose(self) -> None:
        self._closed = True
