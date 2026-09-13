"""Dashboard REST routes."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from ..actions.speech import get_speak_fn
from ..db import day_key
from ..models import PendingCheck
from ..reasoner.schema import AskAction
from ..scoring import healthspan_for_day
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


@router.post("/api/session/start")
async def start_session(request: Request, body: dict[str, Any] | None = None) -> dict:
    name = str((body or {}).get("name", ""))
    return _pipeline(request).sessions.start(name).model_dump()


@router.post("/api/session/end")
async def end_session(request: Request) -> dict:
    session = _pipeline(request).sessions.end()
    if session is None:
        raise HTTPException(status_code=404, detail="no open session")
    return session.model_dump()


@router.get("/api/session/current")
async def current_session(request: Request) -> dict | None:
    session = _pipeline(request).sessions.current()
    return session.model_dump() if session is not None else None


@router.get("/api/ticks/recent")
async def recent_ticks(request: Request, n: int = Query(60, ge=1)) -> list[dict]:
    return _dump(_pipeline(request).db.recent_ticks(n))


#: How many question rows the ``reported`` projection reads in its one query.
#: Questions are rate-limited to a handful an hour (§8.6), so this is several
#: days of them; a run long enough to overflow it loses only the oldest
#: episodes' answers, which are off the dashboard's day view anyway.
REPORTED_SCAN = 1000


def _reported_map(db) -> dict[str, dict]:
    """``episode_id -> reported`` for every episode, in one query (ASK_DESIGN §8.3).

    Projected rather than stored on the episode because
    :class:`~pipeline.episodes.builder.EpisodeBuilder` rewrites ``dominant`` on
    every tick and would erase it -- and because "the wearer reported two" must
    stay distinguishable from "the camera saw two".

    Built for the whole page at once rather than per episode: the day view asks
    for every episode it shows, and one query per row turns a dashboard poll
    into N round trips through the same lock the tick loop writes under.

    The newest answered question with a non-empty parse wins: a follow-up
    ("how many?") is asked after its root and carries the more specific answer,
    while a row whose parse is still empty is an answer the reasoner never got
    to and has nothing to report. ``list_questions`` is newest-first, so the
    first row that qualifies for an episode is that episode's answer.
    """
    reported: dict[str, dict] = {}
    for question in db.list_questions(limit=REPORTED_SCAN):
        episode_id = question.episode_id
        if episode_id is None or episode_id in reported:
            continue
        if question.status != "answered" or not question.parsed:
            continue
        parsed = question.parsed
        reported[episode_id] = {
            "confirmed": parsed.get("confirmed"),
            "count": parsed.get("count"),
            "food_type": parsed.get("food_type"),
            "note": parsed.get("note", ""),
            "question_id": question.id,
            "answered_t": question.answer_t,
        }
    return reported


@router.get("/api/episodes")
async def episodes(request: Request, day: str | None = None) -> list[dict]:
    pipeline = _pipeline(request)
    selected = day or day_key(pipeline.last_tick.t if pipeline.last_tick else time.time())
    rows = pipeline.db.list_episodes(selected)
    reported = _reported_map(pipeline.db)
    return [{**row.model_dump(), "reported": reported.get(row.id)}
            for row in rows]


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


@router.get("/api/healthspan")
async def healthspan(request: Request, day: str | None = None) -> dict:
    """Dose-response hazard view for one day (``scoring/healthspan.py``).

    Computed on request, never written. Runs off the event loop like the
    scoring loop in ``wiring.py``: up to 13 ``list_episodes`` reads under
    ``db._lock`` must not stall the pipeline's ticks.
    """

    pipeline = _pipeline(request)
    selected = day or day_key(pipeline.last_tick.t if pipeline.last_tick else time.time())
    try:
        date.fromisoformat(selected)
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    return await asyncio.to_thread(
        healthspan_for_day, pipeline.db, pipeline.settings, selected, now_t=_now(pipeline))


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


@router.post("/api/speak")
async def speak_now(body: dict[str, Any]) -> dict[str, Any]:
    """Demo/debug: say something through the glasses right now.

    Bypasses the rate limiter on purpose (SPEC §4.6 governs the model's speech,
    not an operator's). Goes through the same hook T1 uses, so it exercises the
    real Mac -> phone -> Bluetooth path.
    """

    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    urgency = str(body.get("urgency", "normal"))
    get_speak_fn()(text, urgency)
    return {"ok": True, "text": text, "urgency": urgency}



# -- ask / answer (ASK_DESIGN §8.11) --------------------------------------


#: Served when the pipeline has no question manager. The manager is optional
#: wiring -- a pipeline built without it still serves every other route rather
#: than failing to start -- so "not wired" is a service state, not a bad request.
def _no_questions() -> JSONResponse:
    return JSONResponse(status_code=503, content={"error": "questions unavailable"})


def _questions(pipeline):
    return getattr(pipeline, "questions", None)


@router.get("/api/questions")
async def questions(request: Request, limit: int = Query(20, ge=1)) -> list[dict]:
    """Every question asked of the wearer, newest first, answers included."""

    return _dump(_pipeline(request).db.list_questions(limit))


@router.post("/api/answer")
async def answer(request: Request, body: dict[str, Any]) -> Any:
    """Answer a question by hand -- the demo path when no phone is listening.

    ``question_id`` is optional: with one question open at a time (§4) the
    obvious default is that one, and typing a generated id into curl is friction
    the demo does not need. 404 rather than a silent no-op when nothing is open,
    because "my answer went nowhere" is the failure worth seeing.

    *Omitted* and *wrong* are not the same thing, though. A caller that sent
    ``question_id`` and got the type wrong meant a specific row, and silently
    answering whatever happens to be open instead would attach a transcript to
    the wrong question -- the one failure here that is invisible afterwards.
    Same for ``text`` and ``heard``: ``str(None)`` is the string ``"None"`` and
    ``bool("no")`` is ``True``, so coercion here manufactures answers the wearer
    never gave.
    """

    pipeline = _pipeline(request)
    manager = _questions(pipeline)
    if manager is None:
        return _no_questions()
    if "question_id" in body:
        question_id = body["question_id"]
        if not isinstance(question_id, str) or not question_id:
            raise HTTPException(400, "question_id must be a non-empty string")
    else:
        open_row = pipeline.db.open_question()
        if open_row is None:
            raise HTTPException(404, "no open question")
        question_id = open_row.id
    text = body.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(400, "text must be a string")
    heard = body.get("heard", True)
    if not isinstance(heard, bool):
        raise HTTPException(400, "heard must be a boolean")
    manager.on_answer(question_id, text, heard, _now(pipeline))
    return {"question_id": question_id, "accepted": True}


@router.post("/api/ask")
async def ask(request: Request, body: dict[str, Any]) -> Any:
    """Demo/debug: ask the wearer something right now.

    Unlike ``/api/speak`` this does **not** bypass the guards -- an ask that
    ignored ``one_open`` would leave two rows waiting for one answer window, and
    the reply shape (``suppressed_reason``) is how an operator sees which guard
    said no.
    """

    raw_text = body.get("text", "")
    if not isinstance(raw_text, str):
        # `str(None)` is the string "None" -- a question the glasses would
        # happily read out loud. A wrong type is a bad request, not an utterance.
        raise HTTPException(400, "text must be a string")
    text = raw_text.strip()
    if not text:
        raise HTTPException(400, "text required")
    pipeline = _pipeline(request)
    manager = _questions(pipeline)
    if manager is None:
        return _no_questions()
    try:
        action = AskAction(
            text=text,
            answer_kind=str(body.get("answer_kind", "yes_no")),
            fills=str(body.get("fills", "confirmed")),
            reason="manual",
        )
    except ValidationError as exc:
        # `answer_kind` and `fills` are closed menus (§5); an operator typo is a
        # 400, not a 500 from deep inside pydantic.
        raise HTTPException(400, f"bad ask: {exc.error_count()} invalid field(s)") from exc
    episode_id = None
    if "episode_id" in body:
        episode_id = body["episode_id"]
        if not isinstance(episode_id, str):
            # Dropping a malformed one would ask the question detached from the
            # episode it is about, and the answer would never reach `reported`.
            raise HTTPException(400, "episode_id must be a string")
    row, suppressed_reason = manager.ask(
        decision_id="manual",
        t=_now(pipeline),
        episode_id=episode_id,
        action=action,
    )
    return {
        "question_id": None if row is None else row.id,
        "suppressed_reason": suppressed_reason,
    }
