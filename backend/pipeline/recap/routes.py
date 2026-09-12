"""Recap REST routes: the moments strip, and the recap itself."""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .builder import (
    DEFAULT_WINDOW_S,
    UnknownSession,
    build_recap,
    now_t,
    recap_store,
)
from .moments import select_moments

__all__ = ["MAX_T", "router"]

#: Upper bound for any timestamp this API accepts: 2100-01-01 UTC. Anything
#: past it is a millisecond clock, a typo, or a probe -- and a window ending in
#: the year 33000 would ask SQLite for every row it has.
MAX_T = 4102444800.0

router = APIRouter()


def _pipeline(request: Request):
    return request.app.state.pipeline


@router.get("/api/moments")
async def moments(
    request: Request,
    from_: float | None = Query(None, alias="from"),
    to: float | None = None,
) -> list[dict]:
    """Key moments in a window, newest last. Defaults to the last 15 minutes.

    The window is on the **tick clock** like everything else downstream, so
    ``--speed 200`` does not desync it from the frames it is pointing at.
    """

    pipeline = _pipeline(request)
    from_, to = _check_t(from_), _check_t(to)
    end = to if to is not None else now_t(pipeline)
    start = from_ if from_ is not None else end - DEFAULT_WINDOW_S
    rows = select_moments(pipeline.db, pipeline.reasoner.evidence, start, end)
    return [row.model_dump() for row in rows]


@router.post("/api/recap")
async def recap(request: Request, body: dict[str, Any] | None = None) -> dict:
    """Generate a recap: score the window, pick the moments, write and speak.

    Body is one of ``{"session_id": ...}``, ``{"from": t0, "to": t1}`` or
    ``{}`` (the last 15 minutes of the tick clock), plus an optional
    ``"speak"`` (default true).
    """

    payload = body or {}
    session_id = payload.get("session_id")
    try:
        return await build_recap(
            _pipeline(request),
            session_id=str(session_id) if session_id else None,
            from_t=_float(payload.get("from")),
            to_t=_float(payload.get("to")),
            speak=bool(payload.get("speak", True)),
        )
    except UnknownSession:
        raise HTTPException(status_code=404, detail="unknown session") from None


@router.get("/api/recap/latest")
async def latest_recap(request: Request) -> dict:
    """The most recently generated recap, or 404 if none has been asked for."""

    body = recap_store(_pipeline(request)).latest()
    if body is None:
        raise HTTPException(status_code=404, detail="no recap yet")
    return body


def _check_t(value: float | None) -> float | None:
    """Reject a timestamp no clock could have produced.

    ``NaN`` compares false against everything, so it does not merely widen a
    window -- it empties one silently, and ``inf`` does the opposite. Both are
    a client bug, and a 400 says so where an empty recap would not.
    """

    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or not (0.0 <= number <= MAX_T):
        raise HTTPException(
            status_code=400,
            detail=f"from/to must be finite timestamps between 0 and {int(MAX_T)}",
        )
    return number


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="from/to must be numbers") from None
    return _check_t(number)
