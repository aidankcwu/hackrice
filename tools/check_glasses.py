#!/usr/bin/env python3
"""A15 acceptance check: the whole Mac-side glasses path, without an iPhone.

    uv run python tools/check_glasses.py

Starts the real FastAPI app in-process, drives it with `tools/fake_phone.py`, consumes
`GlassesSource` exactly as T0 will, and asserts the five things A15 has to get right:

  1. packets arrive over the WebSocket and reach the mailbox
  2. frames come out at ~1 Hz, with a `device` block carrying sane values
  3. `gps_speed` is a passthrough of what the phone sent, `accel_rms` is derived
  4. a malformed packet does not kill the socket
  5. under backpressure the consumer gets the NEWEST frame, not a backlog

docs/PERSON_A.md: the two straddling tasks are where the bugs will be, "because a failure on
one side looks exactly like a failure on the other." This is the Python half's alibi —
if it prints PASS and the real phone still does not work, the bug is in Swift.

Exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

import fake_phone  # noqa: E402
from longevity.server.app import create_app  # noqa: E402
from longevity.server.ingest import INGEST_PATH  # noqa: E402
from longevity.sources.base import Frame  # noqa: E402
from longevity.sources.glasses import AccelWindow, GlassesSource  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —  {detail}" if detail else ""), flush=True)
    if not ok:
        FAILURES.append(label)
    return ok


async def collect(source: GlassesSource, n: int, timeout: float) -> list[tuple[float, Frame]]:
    """Consume up to `n` frames, recording the wall time each one was handed over."""
    out: list[tuple[float, Frame]] = []

    async def _pump() -> None:
        async for frame in source.frames():
            out.append((time.time(), frame))
            if len(out) >= n:
                return

    try:
        await asyncio.wait_for(_pump(), timeout)
    except (TimeoutError, asyncio.TimeoutError):
        pass
    return out


async def start_server(app: Any) -> tuple[uvicorn.Server, asyncio.Task[Any], int]:
    """Bind port 0 so the check never collides with a real server on 8000."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    else:
        raise RuntimeError("uvicorn did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, port


async def main() -> int:
    app = create_app()
    link = app.state.glasses_link
    server, server_task, port = await start_server(app)
    url = f"ws://127.0.0.1:{port}{INGEST_PATH}"
    print(f"\napp up on 127.0.0.1:{port}, ingest at {url}\n", flush=True)

    try:
        # --- 1/2/3: steady state -----------------------------------------------
        print("[steady state] 12 packets at 1 Hz, seated", flush=True)
        sent: dict[int, dict[str, Any]] = {}
        source = GlassesSource(link)
        phone = asyncio.create_task(
            fake_phone.run(url, hz=1.0, count=12, quiet=True, on_sent=lambda s, d: sent.update({s: d}))
        )
        frames = await collect(source, 8, timeout=16)
        await phone
        await source.aclose()

        check(link.n_received >= 8, "packets arrive over the socket", f"{link.n_received} received")
        check(len(frames) >= 8, "frames come out the other side", f"{len(frames)} frames")

        gaps = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
        mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
        worst = max(gaps) if gaps else 0.0
        check(
            0.85 <= mean_gap <= 1.15 and worst < 1.6,
            "cadence is ~1 Hz",
            f"mean {mean_gap:.3f}s, worst {worst:.3f}s over {len(gaps)} gaps",
        )

        devices = [f.device for _, f in frames]
        check(all(isinstance(d, dict) for d in devices), "every frame has a `device` block")
        rms = [d.get("accel_rms") for d in devices if d]
        check(
            bool(rms) and all(r is not None and 0.0 <= r <= 0.2 for r in rms),
            "accel_rms is sane for a seated user",
            f"{min(rms):.4f}..{max(rms):.4f} (§12 example is 0.04)",
        )
        sent_gps = {d["gps_speed"] for d in sent.values() if d["gps_speed"] is not None}
        got_gps = [d.get("gps_speed") for d in devices if d]
        check(
            bool(got_gps) and all(g in sent_gps for g in got_gps),
            "gps_speed is a straight passthrough",
            f"got {got_gps[:4]}...",
        )
        lag = [now - f.t for now, f in frames]
        check(
            max(lag) < 1.2,
            "frames are fresh when handed over",
            f"worst capture->emit lag {max(lag):.3f}s",
        )

        # --- 4: malformed packets ----------------------------------------------
        print("\n[malformed] 6 packets at 2 Hz, #2 and #4 are garbage", flush=True)
        before_recv, before_conn = link.n_received, link.n_connects
        source = GlassesSource(link)
        phone = asyncio.create_task(
            fake_phone.run(url, hz=2.0, count=4, malformed_at=(2, 4), quiet=True)
        )
        frames = await collect(source, 3, timeout=10)
        await phone
        await source.aclose()
        check(
            link.n_malformed >= 2,
            "malformed packets are counted, not raised",
            f"{link.n_malformed} malformed",
        )
        # One connection for the whole phase, and every packet after the garbage ones
        # still landed: the socket was never torn down and re-established.
        check(
            link.n_received - before_recv == 4 and link.n_connects - before_conn == 1,
            "the socket survives a malformed packet",
            f"{link.n_received - before_recv} good packets over "
            f"{link.n_connects - before_conn} connection(s), no reconnect",
        )
        check(len(frames) >= 2, "frames keep flowing afterwards", f"{len(frames)} frames")

        # --- 5: backpressure ----------------------------------------------------
        print("\n[backpressure] 40 packets at 10 Hz into a 1 Hz consumer", flush=True)
        before_recv, before_drop = link.n_received, link.n_dropped
        source = GlassesSource(link)
        phone = asyncio.create_task(fake_phone.run(url, hz=10.0, count=40, quiet=True))
        frames = await collect(source, 4, timeout=10)
        await phone
        await source.aclose()
        got, arrived = len(frames), link.n_received - before_recv
        check(
            got <= 6 < arrived,
            "surplus packets are dropped, not queued",
            f"{arrived} packets in, {got} frames out, {link.n_dropped - before_drop} dropped",
        )
        lag = [now - f.t for now, f in frames]
        check(
            max(lag) < 0.5,
            "the consumer gets the NEWEST frame, not a backlog",
            f"worst capture->emit lag {max(lag):.3f}s (a backlog would grow this)",
        )

        # --- sensor dropout ------------------------------------------------------
        print("\n[sensor dropout] packets with accel=null, gps_speed=null", flush=True)
        source = GlassesSource(link)
        phone = asyncio.create_task(
            fake_phone.run(url, hz=2.0, count=4, profile="none", quiet=True)
        )
        frames = await collect(source, 2, timeout=8)
        await phone
        await source.aclose()
        check(
            len(frames) >= 2 and all(isinstance(f.device, dict) for _, f in frames),
            "`device` survives a missing sensor",
            f"device={frames[0][1].device if frames else None}",
        )

        # --- accel_rms actually measures motion -----------------------------------
        print("\n[accel_rms] derivation, no socket involved", flush=True)
        now = time.time()
        seated, walking = AccelWindow(), AccelWindow()
        for i in range(8):
            seated.add(fake_phone.sensors("seated", i)[0], now - i * 0.1)
            walking.add(fake_phone.sensors("walking", i)[0], now - i * 0.1)
        s_rms, w_rms = seated.rms(now), walking.rms(now)
        check(
            s_rms is not None and w_rms is not None and w_rms > 4 * s_rms,
            "accel_rms separates still from moving",
            f"seated {s_rms:.4f} vs walking {w_rms:.4f}",
        )
        naive = AccelWindow()
        naive.add({"x": 0.01, "y": -0.12, "z": 0.98}, now)  # §11.2's example vector
        check(
            naive.rms(now) is not None and naive.rms(now) < 0.05,
            "§11.2's example vector reads as still, not 0.57",
            f"accel_rms={naive.rms(now):.4f} (naive RMS of the raw vector would be 0.57)",
        )

        # --- phone clock sanity ---------------------------------------------------
        print("\n[clock] a phone whose clock is an hour out", flush=True)
        from longevity import wire
        from longevity.server.ingest import GlassesLink, parse_capture

        now = time.time()
        jpeg = fake_phone.synthetic_jpeg(1)
        scratch = GlassesLink()
        skewed = parse_capture(
            wire.decode(wire.capture_packet(t=now - 3600, jpeg=jpeg, gps_speed=0.2, accel=None)),
            scratch,
            now,
        )
        good = parse_capture(
            wire.decode(wire.capture_packet(t=now - 0.4, jpeg=jpeg, gps_speed=0.2, accel=None)),
            scratch,
            now,
        )
        check(
            skewed is not None and abs(skewed.t - now) < 1.0,
            "an absurd phone clock falls back to arrival time",
            f"t was {now - 3600:.0f}, stored as {skewed.t:.0f}",
        )
        check(
            good is not None and abs(good.t - (now - 0.4)) < 0.01,
            "a sane phone clock is trusted",
            f"kept t = now - {now - good.t:.2f}s",
        )

        # --- health -----------------------------------------------------------
        import httpx

        async with httpx.AsyncClient() as client:
            health = (await client.get(f"http://127.0.0.1:{port}/health")).json()
            stats = (await client.get(f"http://127.0.0.1:{port}/ingest/stats")).json()
        check(health.get("ok") is True, "GET /health answers", str(health))
        check(stats.get("received", 0) > 0, "GET /ingest/stats answers", str(stats))

    finally:
        server.should_exit = True
        await server_task

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}/{CHECKS} checks failed: {', '.join(FAILURES)}", flush=True)
        return 1
    print(f"PASS — all {CHECKS} checks passed. A15 is done on the Python side.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
