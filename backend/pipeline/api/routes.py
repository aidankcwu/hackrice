"""Dashboard REST routes."""

from __future__ import annotations

import base64
import os
import time
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from ..db import day_key
from ..models import PendingCheck
from ..scoring.scorer import rollup
from ..wearables import LIVE_METRICS
from ..wearables.adapters import (
    health_auto_export_to_samples,
    whoop_seeded_rows,
    whoop_to_samples,
)
from ..wearables.ingest import ingest, ingest_samples

#: A live sample newer than this counts as "a wearable is connected right now".
LIVE_FRESH_S = 15 * 60

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


def _now(pipeline) -> float:
    """The tick clock if it is running, wall clock otherwise.

    Everything in ``biometric_series`` is stamped on the tick clock (SPEC
    §14.2) so that ``--speed N`` does not desync the series from the frames.
    """

    return pipeline.last_tick.t if pipeline.last_tick is not None else time.time()


@router.get("/api/biometrics")
async def biometrics(
    request: Request,
    metric: str = "heart_rate",
    metrics: str | None = None,
    from_: float | None = Query(None, alias="from"),
    to: float | None = None,
) -> dict:
    """One metric, or several at once via ``?metrics=a,b,c``.

    The single-metric shape is unchanged. With ``metrics`` the response is
    ``{"series": {metric: {source, origin, points}}}`` -- one round trip for
    the whole wearable strip, and each entry says whether the numbers came off
    a real device (``live``) or the seeded demo day (``seed``).
    """

    pipeline = _pipeline(request)
    end = to if to is not None else _now(pipeline)
    start = from_ if from_ is not None else end - 3600.0
    if metrics is not None:
        wanted = [name.strip() for name in metrics.split(",") if name.strip()]
        return {
            "series": {
                name: pipeline.db.biometric_window(name, start, end)
                for name in wanted
            }
        }
    window = pipeline.db.biometric_window(metric, start, end)
    return {
        "metric": metric,
        "source": window["source"] or "apple_watch",
        "origin": window["origin"],
        "points": window["points"],
    }


# -- live wearable ingest (docs/WEARABLES.md) -----------------------------


def _check_token(token: str | None) -> None:
    """Shared-secret gate, only enforced when the deployment sets one.

    Read from the environment rather than ``Settings`` on purpose: the ingest
    endpoint is the one thing that gets exposed to the LAN during a demo, and
    the secret should be settable without a config file or a restart of the
    whole config object.
    """

    expected = os.environ.get("WEARABLE_INGEST_TOKEN", "").strip()
    if expected and (token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="bad or missing X-Ingest-Token")


@router.post("/api/wearables/ingest")
async def wearables_ingest(
    request: Request,
    payload: dict[str, Any],
    x_ingest_token: str | None = Header(default=None),
) -> dict:
    """Canonical push endpoint.

    ``{"device": "apple_watch", "samples": [{"t", "metric", "value", "unit"}]}``
    """

    _check_token(x_ingest_token)
    pipeline = _pipeline(request)
    return ingest(pipeline.db, payload, now=time.time(),
                  wall_to_tick=pipeline.clock.wall_to_tick)


@router.post("/api/wearables/ingest/health-auto-export")
async def wearables_ingest_hae(
    request: Request,
    payload: dict[str, Any],
    x_ingest_token: str | None = Header(default=None),
) -> dict:
    """Health Auto Export's REST JSON, straight from the iOS automation."""

    _check_token(x_ingest_token)
    pipeline = _pipeline(request)
    return ingest_samples(pipeline.db, health_auto_export_to_samples(payload),
                          now=time.time(), wall_to_tick=pipeline.clock.wall_to_tick)


@router.post("/api/wearables/ingest/whoop")
async def wearables_ingest_whoop(
    request: Request,
    payload: dict[str, Any],
    x_ingest_token: str | None = Header(default=None),
) -> dict:
    """WHOOP API v2 objects, forwarded one record or one page at a time.

    Resting heart rate is a once-a-night number, so it lands in the daily
    ``seeded`` table the scorer already reads rather than in the intraday
    series (SPEC §14.2).
    """

    _check_token(x_ingest_token)
    pipeline = _pipeline(request)
    result = ingest_samples(pipeline.db, whoop_to_samples(payload),
                            now=time.time(), wall_to_tick=pipeline.clock.wall_to_tick)
    result["seeded_rows"] = pipeline.db.insert_seeded_rows(whoop_seeded_rows(payload))
    return result


@router.get("/api/wearables/status")
async def wearables_status(request: Request) -> dict:
    """What is actually stored, and whether anything real is pushing right now."""

    pipeline = _pipeline(request)
    present = pipeline.db.biometric_metrics_present()
    now = (pipeline.last_tick.t if pipeline.last_tick is not None
           else pipeline.clock.wall_to_tick(time.time()))
    # Live rows are stored on the tick clock; 15 wall minutes span 900 * speed
    # seconds there.
    live = [
        row for row in present
        if row["origin"] == "live" and (row["last_t"] or 0) >= now - LIVE_FRESH_S * pipeline.clock.speed
    ]
    return {
        "metrics": present,
        "live_connected": bool(live),
        "live_devices": sorted({str(row["source"]) for row in live if row["source"]}),
        "catalogue": {
            name: {"unit": info.unit, "devices": list(info.devices),
                   "cadence_s": info.cadence_s, "label": info.label}
            for name, info in LIVE_METRICS.items()
        },
    }


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
