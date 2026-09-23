"""Dashboard REST routes."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import base64
import os
import re
import time
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from ..actions.speech import get_speak_fn
from ..db import ALL_DAYS, PROTOCOL_KINDS, day_key
from ..models import PendingCheck, SeededRow
from ..reasoner.prompts import DEFAULT_PERSONA
from ..reasoner.schema import AskAction
from ..scoring import brian_score as bs
from ..scoring.healthspan import (
    GOALS,
    MAX_WEEK_DAYS,
    healthspan_for_day,
    healthspan_registry,
    healthspan_week,
)
from ..scoring.scorer import rollup
from ..wearables import LIVE_METRICS, air
from ..wearables.adapters import (
    health_auto_export_to_samples,
    healthkit_seeded_rows,
    healthkit_to_samples,
    is_healthkit,
    whoop_seeded_rows,
    whoop_to_samples,
)
from ..wearables.ingest import ingest, ingest_samples, parse_payload
from ..wearables.ingest import ingest, ingest_samples
from .auth import token_matches

#: A live sample newer than this counts as "a wearable is connected right now".
LIVE_FRESH_S = 15 * 60

log = logging.getLogger(__name__)

router = APIRouter()


def _pipeline(request: Request):
    return request.app.state.pipeline


def _dump(rows: list) -> list[dict]:
    return [row.model_dump() for row in rows]


@router.get("/api/status")
async def status(request: Request) -> dict:
    return _pipeline(request).status()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, bool | float]:
    """Public process liveness only; never expose user or pipeline state."""

    pipeline = _pipeline(request)
    started_at = pipeline.started_at
    return {
        "ok": True,
        "uptime_s": max(0.0, time.time() - started_at) if started_at is not None else 0.0,
    }


@router.post("/api/session/start")
async def start_session(request: Request, body: dict[str, Any] | None = None) -> dict:
    name = str((body or {}).get("name", ""))
    return _pipeline(request).sessions.start(name).model_dump()


@router.post("/api/session/end")
async def end_session(request: Request) -> dict:
    """End the open session and generate its recap in the background.

    The recap is the point of a session -- what the wearer was doing, scored
    and narrated -- so it is produced whenever a session ends, however it was
    ended (dashboard, curl, a script), not only from the one button that knew
    to ask. It runs as a task because it makes a model call; the caller gets
    the ended session at once and reads the recap from ``GET /api/recaps``
    when it lands (``recap: "generating"`` says so).

    Not spoken. The recap is a written entry in the Logs list, and the glasses
    have already been talking throughout the session -- reading the summary
    aloud on top of that is noise at the moment the wearer takes them off.
    """
    pipeline = _pipeline(request)
    session = pipeline.sessions.end()
    if session is None:
        raise HTTPException(status_code=404, detail="no open session")

    pipeline.spawn_recap(session.id)
    return {**session.model_dump(), "recap": "generating"}


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
    # `label` is merged from the table rather than read off the model: it is a
    # column the episode builder never writes and `Episode` does not carry, so
    # `model_dump()` cannot know about it. `or None` keeps the key's contract
    # ("null until T1 names it") whichever side supplies it.
    labels = pipeline.db.episode_labels(selected)
    return [{**row.model_dump(),
             "reported": reported.get(row.id),
             "label": getattr(row, "label", None) or labels.get(row.id)}
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
async def healthspan(request: Request, day: str | None = None,
                     days: int | None = Query(None, ge=1, le=MAX_WEEK_DAYS),
                     goal: str | None = None) -> dict:
    """Dose-response hazard view for one day (``scoring/healthspan.py``).

    Computed on request, never written. Runs off the event loop like the
    scoring loop in ``wiring.py``: up to 13 ``list_episodes`` reads under
    ``db._lock`` must not stall the pipeline's ticks.

    With ``?days=N`` the answer is ``{days: [lite payload per day, oldest
    first], today: <the full payload for ``day``>}`` -- one round trip for the
    week chart instead of N. ``?day=`` keeps its single-day shape either way, so
    an existing client is unaffected.

    ``?goal=`` scores this one request under another ``Profile`` goal (the
    dashboard's goal picker) and leaves ``PROFILE_GOAL`` alone; anything but
    the four goals is a 400, never a silent fallback to the default.
    """

    pipeline = _pipeline(request)
    selected = day or day_key(pipeline.last_tick.t if pipeline.last_tick else time.time())
    try:
        date.fromisoformat(selected)
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if goal is not None and goal not in GOALS:
        raise HTTPException(400, f"goal must be one of {', '.join(GOALS)}")
    # Pins show the frames the reasoner saved at escalation (STATE.md §7).
    evidence = pipeline.reasoner.evidence
    if days is not None:
        return await asyncio.to_thread(
            healthspan_week, pipeline.db, pipeline.settings, selected, days,
            now_t=_now(pipeline), goal=goal, evidence=evidence)
    return await asyncio.to_thread(
        healthspan_for_day, pipeline.db, pipeline.settings, selected,
        now_t=_now(pipeline), goal=goal, evidence=evidence)


@router.get("/api/healthspan/registry")
async def healthspan_registry_route() -> dict:
    """Everything the "How it's scored" page renders, in one object.

    ``export_registry()`` from the engine (the pipeline steps, every factor's
    dose-response curve, evidence grade, shrink and source, and the
    limitations) plus ``adapter``, which says which app source feeds each
    factor's dose. Pure -- no database read, no clock read.
    """

    return healthspan_registry()


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

    # The same constant-time check as the ACCESS_TOKEN in front of the whole
    # API (api/auth.py). This one stacks on top: with both set, a wearable
    # push presents both -- the access token to reach the container at all,
    # the ingest token to write into its biometrics.
    if not token_matches(os.environ.get("WEARABLE_INGEST_TOKEN"), token):
        raise HTTPException(status_code=401, detail="bad or missing X-Ingest-Token")


@router.post("/api/wearables/ingest")
async def wearables_ingest(
    request: Request,
    payload: dict[str, Any],
    x_ingest_token: str | None = Header(default=None),
) -> dict:
    """Canonical push endpoint.

    ``{"device": "apple_watch", "samples": [{"t", "metric", "value", "unit"}]}``

    The same body with ``source: "healthkit"`` is the phone's Apple Health
    sync: its sleep, resting HR, HRV and steps also land as daily rows, the way
    the WHOOP route files resting HR.
    """

    _check_token(x_ingest_token)
    pipeline = _pipeline(request)
    if is_healthkit(payload):
        samples, result = parse_payload(payload)
        result = ingest_samples(pipeline.db, healthkit_to_samples(samples), now=time.time(),
                                result=result, wall_to_tick=pipeline.clock.wall_to_tick)
        result["seeded_rows"] = pipeline.db.insert_seeded_rows(healthkit_seeded_rows(samples))
        return result
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


# -- air quality (wearables/air.py) ---------------------------------------


@router.get("/api/air/status")
async def air_status(request: Request) -> dict:
    """Whether the air layer is configured, and the newest reading if any.

    A fetch is attempted when coordinates are set, so a cold dashboard gets a
    value on its first poll; ``air.fetch_pm25`` caches per ``AIR_POLL_S``, so
    polling this route every second still makes one upstream request an hour.
    ``last_value`` is ``None`` whenever nothing was measured -- never a zero,
    never a guess -- and the route cannot fail on a dead OpenAQ.
    """

    pipeline = _pipeline(request)
    settings = pipeline.settings
    configured = settings.air_lat is not None and settings.air_lon is not None
    if configured:
        await air.poll_pm25(pipeline.db, settings.air_lat, settings.air_lon,
                            api_key=settings.air_openaq_key, poll_s=settings.air_poll_s)
    last = air.last_reading()
    return {
        "configured": configured,
        "lat": settings.air_lat,
        "lon": settings.air_lon,
        "last_value": None if last is None else last[0].value,
        "last_t": None if last is None else last[1],
        "source": None if last is None else f"{air.PM25_SOURCE} · {last[0].station}",
    }


# -- PVT (the 3-minute reaction-time test, screens.md §6) -----------------

#: Rows ``POST /api/pvt`` writes, body field -> (seeded metric, unit).
PVT_ROWS: dict[str, tuple[str, str]] = {
    "rt_z": ("pvt_rt_z", "z"),
    "lapses": ("pvt_lapses", "count"),
    "rt_ms_median": ("pvt_rt_ms", "ms"),
    "energy": ("pvt_check_energy", "1-5"),
    "mood": ("pvt_check_mood", "1-5"),
    "clarity": ("pvt_check_clarity", "1-5"),
}
#: Provenance of every PVT row: the wearer took the test, nothing inferred it.
PVT_SOURCE = "pvt"
#: Largest plausible |z| against a person's own baseline; beyond it the test
#: misfired (a tap storm, a pocket press) and must not become a Mind reading.
PVT_MAX_ABS_Z = 5.0
#: Lapses in a 3-min PVT; 100 is already every stimulus missed.
PVT_MAX_LAPSES = 100


def _pvt_number(body: dict[str, Any], field: str, lo: float, hi: float,
                *, required: bool = False) -> float | None:
    """One numeric PVT field inside ``[lo, hi]``, or ``None`` when omitted.

    Booleans are rejected explicitly: ``bool`` is an ``int`` in Python, so
    ``{"lapses": true}`` would otherwise be filed as one lapse the wearer never
    had. A wrong type or an out-of-range number is a 400, never a clamp -- a
    clamped reading is an invented one.
    """

    if field not in body or body[field] is None:
        if required:
            raise HTTPException(400, f"{field} is required")
        return None
    value = body[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(400, f"{field} must be a number")
    number = float(value)
    if not lo <= number <= hi:
        raise HTTPException(400, f"{field} must be between {lo:g} and {hi:g}")
    return number


@router.post("/api/pvt")
async def pvt(request: Request, body: dict[str, Any]) -> dict:
    """File a 3-minute PVT result and its three-tap check.

    ``{rt_z, lapses?, rt_ms_median?, energy?, mood?, clarity?, t?}`` -> the local
    day it was filed against and that day's ``utility_today``. The rows land in
    the same seeded table every other daily metric uses, so the next
    ``/api/healthspan`` reads ``rt_z`` and the self-check exactly the way it
    reads a WHOOP row -- there is no second path into the score.
    """

    pipeline = _pipeline(request)
    values = {
        "rt_z": _pvt_number(body, "rt_z", -PVT_MAX_ABS_Z, PVT_MAX_ABS_Z, required=True),
        "lapses": _pvt_number(body, "lapses", 0, PVT_MAX_LAPSES),
        "rt_ms_median": _pvt_number(body, "rt_ms_median", 0, 10_000),
        "energy": _pvt_number(body, "energy", 1, 5),
        "mood": _pvt_number(body, "mood", 1, 5),
        "clarity": _pvt_number(body, "clarity", 1, 5),
    }
    stamped = _pvt_number(body, "t", 0, 4e9)
    day = day_key(_now(pipeline) if stamped is None else stamped)
    pipeline.db.insert_seeded_rows([
        SeededRow(day=day, metric=metric, value=values[field], unit=unit, source=PVT_SOURCE)
        for field, (metric, unit) in PVT_ROWS.items() if values[field] is not None
    ])

    seeded_rows = {row.metric: row.value for row in pipeline.db.list_seeded(day, day)}
    check = {key: seeded_rows[metric] for metric, key in
             (("pvt_check_energy", "energy"), ("pvt_check_mood", "mood"),
              ("pvt_check_clarity", "clarity")) if metric in seeded_rows}
    recovery = seeded_rows.get("recovery_score")
    measured_pvt = {"rt_z": values["rt_z"]}
    if values["lapses"] is not None:
        measured_pvt["lapses"] = values["lapses"]
    experience = bs.utility_today(
        pvt=measured_pvt, check=check or None,
        recovery_score=None if recovery is None else float(recovery))
    return {"day": day, "experience": experience}


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



# -- persona and profile (the growing persona) ----------------------------


@router.get("/api/persona")
async def get_persona(request: Request) -> dict:
    """The persona T1 is briefed with, and whether it is the built-in one.

    ``source`` matters more than it looks: an operator editing this box needs
    to know whether they are looking at the default -- which they can revise
    freely -- or at something they already overrode.
    """

    pipeline = _pipeline(request)
    override = pipeline.db.get_persona()
    reasoner = getattr(pipeline, "reasoner", None)
    fallback = getattr(reasoner, "persona", "") or DEFAULT_PERSONA
    return {"text": override or fallback,
            "source": "custom" if override else "default"}


@router.put("/api/persona")
async def put_persona(request: Request, body: dict[str, Any]) -> dict:
    """Set the persona override; empty text clears it back to the default.

    Clearing rather than storing "" on purpose: a blank persona would brief the
    model with nothing at all, and "I cleared the box" always means "go back to
    how it was", never "work for nobody".
    """

    text = body.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(400, "text must be a string")
    pipeline = _pipeline(request)
    pipeline.db.set_persona(text, _now(pipeline))
    return await get_persona(request)


@router.get("/api/profile")
async def profile(request: Request, limit: int = Query(50, ge=1)) -> list[dict]:
    """Active learned lines -- what `remember` has established -- oldest first."""

    return _pipeline(request).db.profile_lines(limit)


@router.delete("/api/profile/{line_id}")
async def delete_profile_line(request: Request, line_id: str) -> dict:
    """Retire one learned line. 404 when it is already gone or never existed."""

    if not _pipeline(request).db.deactivate_profile_line(line_id):
        raise HTTPException(404, "no such active profile line")
    return {"id": line_id, "removed": True}


# -- the protocol (PLAN 2.1, docs/API.md "The protocol") ------------------

_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: Columns of ``GET /api/protocol/export.csv``, in order.
PROTOCOL_CSV_COLUMNS = ("day", "item_id", "name", "kind", "window_start",
                        "window_end", "status", "seen_t", "evidence_ref",
                        "updated_t")


def _protocol_fields(body: dict[str, Any], base: dict[str, Any] | None) -> dict:
    """Validate a new item (``base`` is ``None``) or ``body`` merged onto one.

    Checked here rather than coerced: ``str(7)`` is a name nobody typed and
    ``bool`` is an ``int`` to Python, so ``[True]`` would otherwise be Tuesday.
    """

    item = dict(base or {})
    for key in ("name", "kind", "window_start", "window_end", "days"):
        if key in body:
            item[key] = body[key]
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(400, "name must be a non-empty string")
    item["name"] = name.strip()
    if item.get("kind") not in PROTOCOL_KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(PROTOCOL_KINDS)}")
    for key in ("window_start", "window_end"):
        value = item.get(key)
        if not isinstance(value, str) or not _HHMM.match(value):
            raise HTTPException(400, f"{key} must be HH:MM")
    if item["window_start"] >= item["window_end"]:
        raise HTTPException(400, "window_start must be before window_end")
    days = item.get("days", list(ALL_DAYS))
    if (not isinstance(days, list) or not days
            or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6
                   for d in days)):
        raise HTTPException(400, "days must be a non-empty list of 0-6 (0 = Monday)")
    item["days"] = sorted(set(days))
    return item


def _protocol_item_or_404(pipeline, item_id: str) -> dict:
    item = pipeline.db.get_protocol_item(item_id)
    if item is None:
        raise HTTPException(404, "no such protocol item")
    return item


def _protocol_today_row(item: dict, status: dict | None) -> dict:
    """One item plus what happened to it today; no status row reads ``waiting``."""

    status = status or {}
    return {**item,
            "status": status.get("status", "waiting"),
            "seen_t": status.get("seen_t"),
            "evidence_ref": status.get("evidence_ref"),
            "updated_t": status.get("updated_t")}


def _protocol_mark(request: Request, item_id: str, status: str) -> dict:
    pipeline = _pipeline(request)
    item = _protocol_item_or_404(pipeline, item_id)
    now = _now(pipeline)
    day = day_key(now)
    row = pipeline.db.set_protocol_status(item_id, day, status, t=now)
    return {**_protocol_today_row(item, row), "day": day}


@router.get("/api/protocol")
async def protocol_items(request: Request) -> list[dict]:
    """Every protocol item, earliest window first."""

    return _pipeline(request).db.list_protocol_items()


@router.post("/api/protocol", status_code=201)
async def create_protocol_item(request: Request, body: dict[str, Any]) -> dict:
    pipeline = _pipeline(request)
    item = _protocol_fields(body, None)
    item["created_t"] = _now(pipeline)
    return pipeline.db.upsert_protocol_item(item)


@router.get("/api/protocol/today")
async def protocol_today(request: Request) -> dict:
    """Today's items (those whose ``days`` include today) with today's status.

    "Today" is the local day on the tick clock, like ``/api/episodes``.
    """

    pipeline = _pipeline(request)
    day = day_key(_now(pipeline))
    weekday = date.fromisoformat(day).weekday()
    statuses = {row["item_id"]: row
                for row in pipeline.db.protocol_statuses(day, day)}
    return {"day": day,
            "items": [_protocol_today_row(item, statuses.get(item["id"]))
                      for item in pipeline.db.list_protocol_items()
                      if weekday in item["days"]]}


@router.get("/api/protocol/export.csv")
async def protocol_export(request: Request,
                          days: int = Query(14, ge=1, le=366)) -> Response:
    """The last ``days`` local days, oldest first, one row per item per day.

    A day gets a row when something was recorded for it, or when the item was
    scheduled that day and already existed -- an item added today has no
    fortnight of ``waiting`` behind it.
    """

    pipeline = _pipeline(request)
    end = date.fromisoformat(day_key(_now(pipeline)))
    start = end - timedelta(days=days - 1)
    items = pipeline.db.list_protocol_items()
    stored = {(row["item_id"], row["day"]): row
              for row in pipeline.db.protocol_statuses(start.isoformat(),
                                                       end.isoformat())}
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(PROTOCOL_CSV_COLUMNS)
    for offset in range(days):
        current = start + timedelta(days=offset)
        key = current.isoformat()
        for item in items:
            status = stored.get((item["id"], key))
            if status is None and (current.weekday() not in item["days"]
                                   or key < day_key(item["created_t"])):
                continue
            row = _protocol_today_row(item, status)
            writer.writerow([key, item["id"], item["name"], item["kind"],
                             item["window_start"], item["window_end"],
                             row["status"],
                             "" if row["seen_t"] is None else row["seen_t"],
                             row["evidence_ref"] or "",
                             "" if row["updated_t"] is None else row["updated_t"]])
    return Response(
        buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="protocol-{end.isoformat()}.csv"'})


@router.put("/api/protocol/{item_id}")
async def update_protocol_item(request: Request, item_id: str,
                               body: dict[str, Any]) -> dict:
    """Change any of ``name, kind, window_start, window_end, days``; the rest
    of the item is kept. ``id`` and ``created_t`` never change."""

    pipeline = _pipeline(request)
    item = _protocol_fields(body, _protocol_item_or_404(pipeline, item_id))
    return pipeline.db.upsert_protocol_item(item)


@router.delete("/api/protocol/{item_id}")
async def delete_protocol_item(request: Request, item_id: str) -> dict:
    """Remove an item and its status history. 404 when it does not exist."""

    if not _pipeline(request).db.delete_protocol_item(item_id):
        raise HTTPException(404, "no such protocol item")
    return {"id": item_id, "removed": True}


@router.post("/api/protocol/{item_id}/done")
async def protocol_done(request: Request, item_id: str) -> dict:
    """The wearer marks today's item done by hand; a sighting is kept."""

    return _protocol_mark(request, item_id, "done")


@router.post("/api/protocol/{item_id}/undo")
async def protocol_undo(request: Request, item_id: str) -> dict:
    """The wearer takes back today's ``seen`` or ``done``: status ``undone``."""

    return _protocol_mark(request, item_id, "undone")


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


# -- conversations (docs/CONVERSATION_DESIGN.md §7) -----------------------


#: Served when the pipeline has no voice agent. Like the question manager it is
#: optional wiring, so "not wired" is a service state and not a bad request.
def _no_conversation() -> JSONResponse:
    return JSONResponse(status_code=503,
                        content={"error": "conversation unavailable"})


def _conversation(pipeline):
    return getattr(pipeline, "conversation", None)


@router.get("/api/conversations")
async def conversations(request: Request, limit: int = Query(20, ge=1)) -> Any:
    """Every conversation, newest first, threads included."""

    agent = _conversation(_pipeline(request))
    if agent is None:
        return _no_conversation()
    return agent.list(limit)


@router.get("/api/conversation/current")
async def current_conversation(request: Request) -> Any:
    """The running conversation, or ``null``.

    Declared before ``/api/conversations/{id}`` would be reachable by it -- the
    paths differ in their prefix, but keeping the literal route first is the
    habit that stops a future rename from swallowing it.
    """

    agent = _conversation(_pipeline(request))
    if agent is None:
        return _no_conversation()
    return agent.current()


@router.post("/api/conversation/open")
async def open_conversation(request: Request, body: dict[str, Any]) -> Any:
    """Demo: hand the voice agent a topic by hand.

    409 when one is already running, because "say something about the cereal"
    arriving mid-conversation is not a queued request -- §1 drops it, and an
    operator needs to see that rather than wonder why nothing was said.
    """

    raw = body.get("topic", "")
    if not isinstance(raw, str):
        raise HTTPException(400, "topic must be a string")
    topic = raw.strip()
    if not topic:
        raise HTTPException(400, "topic required")
    mode = body.get("mode", "statement")
    if mode not in ("statement", "question"):
        raise HTTPException(400, "mode must be 'statement' or 'question'")
    pipeline = _pipeline(request)
    agent = _conversation(pipeline)
    if agent is None:
        return _no_conversation()
    outcome = agent.request(topic, mode, decision_id="manual", reason="manual")
    if outcome == "conversation_active":
        return JSONResponse(status_code=409,
                            content={"reason": "conversation_active"})
    conversation_id = (outcome.split(":", 1)[1]
                       if outcome.startswith("handed_off:") else None)
    return {"conversation_id": conversation_id, "outcome": outcome}


@router.get("/api/conversations/{conversation_id}")
async def conversation(request: Request, conversation_id: str) -> Any:
    """One conversation row. 404 when there is no such id."""

    agent = _conversation(_pipeline(request))
    if agent is None:
        return _no_conversation()
    row = agent.get(conversation_id)
    if row is None:
        raise HTTPException(404, "no such conversation")
    return row


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
