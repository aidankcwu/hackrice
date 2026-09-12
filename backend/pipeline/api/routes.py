"""Dashboard REST routes."""

from __future__ import annotations

import base64
import time
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from ..db import day_key
from ..models import PendingCheck
from ..scoring.scorer import rollup

router = APIRouter()


def _pipeline(request: Request):
    return request.app.state.pipeline


def _dump(rows: list) -> list[dict]:
    return [row.model_dump() for row in rows]


@router.get("/api/status")
async def status(request: Request) -> dict:
    return _pipeline(request).status()


@router.get("/api/ticks/recent")
async def recent_ticks(request: Request, n: int = Query(60, ge=1)) -> list[dict]:
    return _dump(_pipeline(request).db.recent_ticks(n))


@router.get("/api/episodes")
async def episodes(request: Request, day: str | None = None) -> list[dict]:
    pipeline = _pipeline(request)
    selected = day or day_key(pipeline.last_tick.t if pipeline.last_tick else time.time())
    return _dump(pipeline.db.list_episodes(selected))


@router.get("/api/decisions")
async def decisions(request: Request, limit: int = Query(50, ge=1)) -> list[dict]:
    return _dump(_pipeline(request).db.list_decisions(limit))


@router.get("/api/insights")
async def insights(request: Request, limit: int = Query(50, ge=1)) -> list[dict]:
    return _dump(_pipeline(request).db.list_insights(limit))


@router.get("/api/scores")
async def scores(request: Request,
                 period: Literal["daily", "weekly"] = "daily") -> dict:
    pipeline = _pipeline(request)
    rows = pipeline.db.list_scores(period)
    if not rows:
        today = day_key(pipeline.last_tick.t if pipeline.last_tick else time.time())
        pipeline.scorer.score_all(today, pipeline.scorer.week_days(today))
        rows = pipeline.db.list_scores(period)
    return {"period": period, "scores": _dump(rows), "overall": rollup(rows)}


@router.get("/api/pending_checks")
async def pending_checks(request: Request) -> list[dict]:
    db = _pipeline(request).db
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM pending_checks WHERE fired = 0 ORDER BY created_t ASC"
        ).fetchall()
    return _dump([PendingCheck(**{**dict(row), "fired": bool(row["fired"])})
                  for row in rows])


@router.get("/api/summary/today")
async def today_summary(request: Request) -> dict:
    pipeline = _pipeline(request)
    day = day_key(pipeline.last_tick.t if pipeline.last_tick else time.time())
    return {"day": day, "lines": _dump(pipeline.db.today_summary_lines(day))}


@router.get("/api/seeded")
async def seeded(request: Request, days: int = Query(7, ge=1)) -> dict:
    pipeline = _pipeline(request)
    end = date.fromisoformat(day_key(
        pipeline.last_tick.t if pipeline.last_tick else time.time()))
    start = end - timedelta(days=days - 1)
    return {"rows": _dump(pipeline.db.list_seeded(start.isoformat(), end.isoformat()))}


@router.get("/api/events")
async def events() -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": "SSE deferred; poll"})


@router.get("/frames")
async def frames(request: Request, refs: str = "") -> dict[str, str]:
    wanted = [ref for ref in refs.split(",") if ref]
    found = _pipeline(request).frame_store.get(wanted)
    if not found:
        raise HTTPException(status_code=410, detail="frames expired")
    return {ref: base64.b64encode(jpeg).decode("ascii")
            for ref, jpeg in found.items()}


@router.get("/api/evidence/{decision_id}")
async def evidence(request: Request, decision_id: str) -> list[dict]:
    return _pipeline(request).reasoner.evidence.list(decision_id)


@router.get("/api/evidence/{decision_id}/{ref}")
async def evidence_jpeg(request: Request, decision_id: str, ref: str) -> Response:
    jpeg = _pipeline(request).reasoner.evidence.get(decision_id, ref)
    if jpeg is None:
        raise HTTPException(status_code=404, detail="evidence frame not found")
    return Response(jpeg, media_type="image/jpeg")
