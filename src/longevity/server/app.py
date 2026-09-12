"""The FastAPI app the phone and Person B both talk to (SPEC §11.5).

Three things hang off it:

    WS   /ws/glasses     capture packets in, `speak` messages out (A14/A15, A16)
    GET  /frames         escalation pixels out, to Person B (A8) — another lane's file
    GET  /health         is the glasses path alive, and has it seen a packet lately

The frames router is imported defensively. It is owned by a different lane and this app
has to boot whether or not that file has landed — the glasses path being blocked on the
escalation path would be a self-inflicted wound.

    uv run python -m longevity.server.app        # 0.0.0.0:8000, for the real phone
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI

from . import ingest

log = logging.getLogger(__name__)

DEFAULT_PORT = 8000

# The ring is the frames lane's contract, not ours; we only construct one so that
# `GET /frames` has something to read when its router is present.
try:  # pragma: no cover - exercised by whichever half of the tree exists
    from ..ring import FrameRing
except ImportError:  # pragma: no cover
    FrameRing = None  # type: ignore[assignment]

try:  # pragma: no cover
    from . import frames as frames_mod
except ImportError:  # pragma: no cover
    frames_mod = None  # type: ignore[assignment]


def create_app(*, link: ingest.GlassesLink | None = None, ring: Any = None) -> FastAPI:
    """Build the app. `link` and `ring` are injectable so tests own their own state."""
    app = FastAPI(title="longevity T0", version="0.1.0")
    app.state.started_at = time.time()

    app.state.glasses_link = ingest.attach(app, link)

    if frames_mod is not None:
        if ring is None and FrameRing is not None:
            ring = FrameRing()
        app.state.frame_ring = ring
        app.include_router(frames_mod.router)
    else:
        log.info("frames router not present yet; /frames is unavailable")

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Cheap liveness plus the two numbers worth looking at during a demo.

        `last_packet_age_s` is the honest answer to "are the glasses still feeding us":
        the socket can be open with nothing coming down it.
        """
        link_stats = app.state.glasses_link.stats()
        return {
            "ok": True,
            "uptime_s": round(time.time() - app.state.started_at, 1),
            "phone_connected": link_stats["connected"] > 0,
            "packets": link_stats["received"],
            "last_packet_age_s": link_stats["latest_age_s"],
            "frames_router": frames_mod is not None,
        }

    return app


app = create_app()


def main() -> None:
    """Run the ingest server on the LAN so the phone can reach it."""
    import argparse
    import socket

    import uvicorn

    ap = argparse.ArgumentParser(description="T0 ingest server (A15)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = str(s.getsockname()[0])
    except OSError:
        ip = "10.135.100.7"
    finally:
        s.close()

    bar = "=" * 72
    print(
        f"\n{bar}\n"
        f"  T0 INGEST — listening on {args.host}:{args.port}\n"
        f"{bar}\n"
        f"  The phone connects to the LAN IP, never localhost:\n"
        f"\n"
        f"      ws://{ip}:{args.port}{ingest.INGEST_PATH}\n"
        f"\n"
        f"  Health:  curl http://{ip}:{args.port}/health\n"
        f"  Ingest:  curl http://{ip}:{args.port}/ingest/stats\n"
        f"  No packets arriving? Prove the socket first with tools/echo_server.py —\n"
        f"  without NSLocalNetworkUsageDescription and NSAllowsLocalNetworking the\n"
        f"  iOS side fails silently and it reads exactly like a backend bug.\n"
        f"{bar}\n",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
