"""`GET /frames` (docs/PERSON_A.md A8, SPEC §12.3).

The escalation path's only way to reach pixels. Person B holds tick history, picks four
refs by perceptual-hash distance (§4.3), and fetches the bytes here to base64 into the
Claude call.

Response is base64 JSON rather than multipart, per A8: B is base64-ing the frames into
the Claude call anyway, so this saves them a conversion.

One deliberate deviation from §12.3, flagged because it is a contract detail with B.
The spec says an expired ref returns 410. Taken literally that fails the whole request
when one of four refs has aged out, costing B the three good frames for no reason. So:

    all refs resolve          -> 200, `missing` empty
    some resolve              -> 200, the rest named in `missing`
    none resolve              -> 410

The degenerate single-expired-ref case still returns 410, and B always gets whatever
was actually in the ring. Either way it is logged, never raised — a miss means the
pipeline has fallen behind, which is worth knowing but is not a reason to crash.

The shape, in full — `frames` comes back in the order asked, `missing` names the rest:

    GET /frames?refs=f_00001738,f_00001740,f_00001742
    200
    {
      "frames": [
        {"ref": "f_00001738", "t": 1757700838.0, "age_ms": 4000,
         "mime": "image/jpeg", "bytes": 41302, "b64": "/9j/4AAQSkZJRg..."},
        {"ref": "f_00001740", "t": 1757700840.0, "age_ms": 2000,
         "mime": "image/jpeg", "bytes": 40911, "b64": "/9j/4AAQSkZJRg..."}
      ],
      "missing": ["f_00001742"]
    }

Both keys are always present, on 200 and on 410 alike. `b64` is standard base64 of the
exact JPEG bytes, ready to drop into a Claude image block as `{"type": "base64",
"media_type": mime, "data": b64}`. Do not decode and re-encode it — re-compressing a
JPEG costs quality for nothing.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

from fastapi import APIRouter, FastAPI, Query, Request, Response

from ..ring import FrameRing, default_ring

log = logging.getLogger(__name__)

router = APIRouter(tags=["frames"])

# Escalation asks for four (§4.3). The cap is a guard against a malformed query
# string, not a real limit anyone should hit.
MAX_REFS = 32


_warned_unattached = False


def _ring(request: Request) -> FrameRing:
    """The ring this app was attached to, else the process-wide one.

    The fallback is what makes a bare `app.include_router(router)` work, since the app
    module is not mine to wire. It warns once, because a router quietly reading an
    empty ring would reach B looking exactly like every ref having expired.
    """
    global _warned_unattached
    ring = getattr(request.app.state, "frame_ring", None)
    if ring is None:
        if not _warned_unattached:
            _warned_unattached = True
            log.warning("frames: no ring attached to this app, using ring.default_ring()")
        ring = default_ring()
    return ring


def _parse_refs(refs: list[str]) -> list[str]:
    """`?refs=a,b,c` as §12.3 writes it, and repeated `?refs=a&refs=b` too.

    De-duped, in the order asked, because that order is the chronological order §4.3
    interleaves the images in.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in refs:
        for chunk in value.split(","):
            ref = chunk.strip()
            if ref and ref not in seen:
                seen.add(ref)
                out.append(ref)
    return out[:MAX_REFS]


@router.get("/frames")
def get_frames(
    request: Request,
    response: Response,
    refs: list[str] = Query(
        ..., description="frame_refs, comma-separated or repeated: f_00001742"
    ),
) -> dict[str, Any]:
    """Fetch frames by ref for escalation."""
    now = time.time()
    wanted = _parse_refs(refs)
    if not wanted:
        response.status_code = 400
        return {"frames": [], "missing": [], "error": "no refs given"}

    found, missing = _ring(request).get_many(wanted, now=now)

    if missing:
        # §12.3: "this should never occur — log it rather than crash, because it means
        # the pipeline has fallen behind."
        log.warning(
            "frames: %d/%d refs unavailable (expired or unknown): %s",
            len(missing),
            len(wanted),
            ",".join(missing),
        )

    if not found:
        response.status_code = 410

    return {
        "frames": [
            {
                "ref": f.ref,
                "t": round(f.t, 3),
                "age_ms": int(round((now - f.t) * 1000)),
                "mime": "image/jpeg",
                "bytes": f.nbytes,
                "b64": base64.b64encode(f.jpeg).decode("ascii"),
            }
            for f in found
        ],
        "missing": missing,
    }


@router.get("/frames/stats")
def get_frame_stats(request: Request) -> dict[str, Any]:
    """Ring health. `count` and `bytes` should be flat over a long run."""
    s = _ring(request).stats()
    return {
        "count": s.count,
        "bytes": s.bytes,
        "oldest_age_s": None if s.oldest_age_s is None else round(s.oldest_age_s, 2),
        "newest_age_s": None if s.newest_age_s is None else round(s.newest_age_s, 2),
        "puts": s.puts,
        "evicted_ttl": s.evicted_ttl,
        "evicted_cap": s.evicted_cap,
        "hits": s.hits,
        "misses": s.misses,
        "ttl_s": _ring(request).ttl_s,
    }


def attach(app: FastAPI, ring: FrameRing) -> None:
    """Mount the frames routes on `app`, backed by `ring`.

    Kept as a function rather than a module-level global so the T0 process and the
    tests can each own their own ring. An app that only does
    `app.include_router(router)` gets `ring.default_ring()` instead, which is the same
    ring the capture loop writes to.
    """
    app.state.frame_ring = ring
    app.include_router(router)
