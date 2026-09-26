"""Assemble one recap and persist it.

The response shape here is frozen -- the dashboard codes against it. Everything
in it is either read straight off the database or produced by one of the two
modules next door:

    session   the window itself, and how well the pipeline covered it
    score     Scorer.score_window, labelled and unit-ed for display
    moments   recap.moments.select_moments
    narrative recap.narrative, which is also what gets read aloud

The ``recaps`` table is created here rather than in :mod:`pipeline.db` for the
same reason ``escalated_frames`` is created in
:mod:`pipeline.reasoner.evidence`: it is this feature's concern, and all it
borrows is ``db.conn`` and ``db._lock``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any

from ..actions.speech import get_speak_fn, spoken
from ..db import Database, day_key
from ..scoring.scorer import rollup
from ..scoring.thresholds import THRESHOLDS
from .moments import select_moments
from ..persona import effective_persona
from ..reasoner.prompts import DEFAULT_PERSONA
from .narrative import RecapContext, RecapNarrative, make_narrative_client

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_WINDOW_S",
    "MAX_WINDOW_S",
    "RECAP_SCHEMA",
    "RecapStore",
    "UnknownSession",
    "build_recap",
    "label_for",
    "now_t",
    "clamp_window",
    "recap_store",
    "resolve_window",
    "unit_for",
]

#: ``POST /api/recap`` with no window recaps this much of the tick clock. A
#: judging session is about two minutes; 15 covers the demo that ran long.
DEFAULT_WINDOW_S = 15 * 60.0

#: The longest window a recap will assemble. The phone's Home summary asks for
#: local midnight to now (docs/DEMO_UI_PRD.md "Daily summary"), so one day must
#: fit; a request for "the last week" would read a week of ticks, episodes,
#: decisions and evidence into memory and score them on one thread. Past this
#: the recap keeps the *most recent* day and says so.
MAX_WINDOW_S = 24 * 3600.0

RECAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS recaps (
    id           TEXT PRIMARY KEY,
    session_id   TEXT,
    from_t       REAL NOT NULL,
    to_t         REAL NOT NULL,
    generated_at REAL NOT NULL,
    body         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_recaps_generated ON recaps(generated_at);
"""

#: Display labels, where the metric id humanises badly. Anything not here falls
#: back to the §8 spec's own label and then to the id with its underscores out.
LABELS: dict[str, str] = {
    "screen_hours_daily": "Screen time",
    "social_episodes_daily": "Social contact",
    "meals_logged_daily": "Meals logged",
    "diet_pattern_daily": "Diet pattern",
    "caffeine_cutoff_daily": "Caffeine cutoff",
    "alcohol_daily": "Alcohol",
    "sleep_hours": "Sleep",
    "sleep_regularity_sri": "Sleep regularity",
    "hrv_rmssd_ratio": "HRV recovery",
    "daytime_light_minutes": "Daytime light",
    "evening_light_ok": "Evening light",
    "night_noise_db": "Night noise",
    "purpose_score": "Life purpose",
    "gait_speed_ms": "Gait speed",
    "balance_one_leg_s": "Balance",
    "breathwork_minutes": "Breathwork",
    "vilpa_minutes": "VILPA",
}

#: Short units for the score card. The §8 specs carry rate strings
#: ("conversations/day") which read as noise next to a session count.
UNITS: dict[str, str] = {
    "screen_hours_daily": "h",
    "social_episodes_daily": "count",
    "meals_logged_daily": "count",
    "diet_pattern_daily": "ratio",
    "caffeine_cutoff_daily": "count",
    "alcohol_daily": "count",
    "sleep_hours": "h",
    "sleep_regularity_sri": "SRI",
    "steps": "steps",
    "vilpa_minutes": "min",
    "gait_speed_ms": "m/s",
    "balance_one_leg_s": "s",
    "hrv_rmssd_ratio": "ratio",
    "breathwork_minutes": "min",
    "night_noise_db": "dB",
    "purpose_score": "1-5",
    "daytime_light_minutes": "min",
    "evening_light_ok": "0/1",
}


def label_for(metric: str) -> str:
    if metric in LABELS:
        return LABELS[metric]
    spec = THRESHOLDS.get(metric)
    if spec is not None:
        return spec.label
    stem = metric.removesuffix("_daily").removesuffix("_weekly").replace("_", " ")
    return stem[:1].upper() + stem[1:]


def unit_for(metric: str) -> str:
    if metric in UNITS:
        return UNITS[metric]
    spec = THRESHOLDS.get(metric)
    return spec.unit.split("/")[0].strip() if spec is not None else ""


# -- persistence ----------------------------------------------------------


class RecapStore:
    """The ``recaps`` table: one row per generated recap, body as JSON."""

    def __init__(self, db: Database) -> None:
        self.db = db
        with self._lock():
            self.db.conn.executescript(RECAP_SCHEMA)
            self.db.conn.commit()

    def _lock(self) -> Any:
        lock = getattr(self.db, "_lock", None)
        return lock if lock is not None else contextlib.nullcontext()

    def save(self, recap: dict[str, Any]) -> None:
        session = recap.get("session") or {}
        with self._lock():
            self.db.conn.execute(
                "INSERT OR REPLACE INTO recaps"
                " (id, session_id, from_t, to_t, generated_at, body)"
                " VALUES (?,?,?,?,?,?)",
                (
                    recap["id"],
                    session.get("id"),
                    float(session.get("from_t", 0.0)),
                    float(session.get("to_t", 0.0)),
                    float(recap.get("generated_at", 0.0)),
                    json.dumps(recap, separators=(",", ":")),
                ),
            )
            self.db.conn.commit()

    def latest(self) -> dict[str, Any] | None:
        with self._lock():
            row = self.db.conn.execute(
                "SELECT body FROM recaps ORDER BY generated_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first, without the bodies.

        The index page draws a row per recap, and a body is the whole report
        including every moment -- tens of kilobytes each. Listing twenty of
        them would ship a megabyte to render twenty dates, so the columns the
        list needs are read straight from the table and the body is fetched
        only when one is opened.
        """

        with self._lock():
            rows = self.db.conn.execute(
                "SELECT id, session_id, from_t, to_t, generated_at FROM recaps"
                " ORDER BY generated_at DESC, rowid DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {
                "id": row[0],
                "session_id": row[1],
                "from_t": float(row[2]),
                "to_t": float(row[3]),
                "generated_at": float(row[4]),
                "duration_s": max(0.0, float(row[3]) - float(row[2])),
            }
            for row in rows
        ]

    def get(self, recap_id: str) -> dict[str, Any] | None:
        with self._lock():
            row = self.db.conn.execute(
                "SELECT body FROM recaps WHERE id=?", (recap_id,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def count(self) -> int:
        with self._lock():
            return int(self.db.conn.execute("SELECT COUNT(*) FROM recaps").fetchone()[0])


def recap_store(pipeline) -> RecapStore:
    """One store per pipeline, created lazily -- wiring.py is not ours to edit."""

    store = getattr(pipeline, "_recap_store", None)
    if store is None:
        store = RecapStore(pipeline.db)
        pipeline._recap_store = store
    return store


def _narrative_client(pipeline):
    client = getattr(pipeline, "_recap_narrative_client", None)
    if client is None:
        client = make_narrative_client(pipeline.settings, pipeline.reasoner_mode)
        pipeline._recap_narrative_client = client
    return client


# -- the window -----------------------------------------------------------


def now_t(pipeline) -> float:
    """The tick clock if it is running, wall clock otherwise."""

    tick = getattr(pipeline, "last_tick", None)
    return tick.t if tick is not None else time.time()


class UnknownSession(LookupError):
    """Raised when a ``session_id`` names nothing this database knows about."""


def clamp_window(from_t: float, to_t: float) -> tuple[float, float]:
    """Normalise one window: endpoints in order, length inside the cap.

    Every component -- the scorer, the moments, the coverage read, the
    narrative -- is handed the result of this, so none of them can disagree
    about what the window was.
    """

    start, end = (from_t, to_t) if to_t >= from_t else (to_t, from_t)
    if end - start > MAX_WINDOW_S:
        log.warning(
            "recap window of %.0f s exceeds the %.0f s cap; keeping the most recent",
            end - start, MAX_WINDOW_S,
        )
        start = end - MAX_WINDOW_S
    return start, end


def resolve_window(
    pipeline, session_id: str | None, from_t: float | None, to_t: float | None
) -> tuple[str | None, str | None, float, float]:
    """``(session_id, name, from_t, to_t)`` for one recap request.

    A ``session_id`` wins over an explicit window; an explicit window wins over
    the default. The session lookup is a raw guarded query rather than a call
    into ``Database``: the sessions table is another worker's, and a recap must
    degrade to a 404 if their migration has not landed, never to a 500.

    Whichever branch answers, the window leaves here through
    :func:`clamp_window`: ordered, and no longer than :data:`MAX_WINDOW_S`.
    """

    now = now_t(pipeline)
    if session_id:
        row = _session_row(pipeline.db, session_id)
        if row is None:
            raise UnknownSession(session_id)
        started, ended, name = row
        # An open session is still running: the tick clock is its right edge.
        start, end = clamp_window(
            float(started), float(ended if ended is not None else now))
        return session_id, name, start, end
    if from_t is not None or to_t is not None:
        end = float(to_t) if to_t is not None else now
        start = float(from_t) if from_t is not None else end - DEFAULT_WINDOW_S
        return None, None, *clamp_window(start, end)
    return None, None, *clamp_window(now - DEFAULT_WINDOW_S, now)


def _session_row(db: Database, session_id: str) -> tuple[float, float | None, str | None] | None:
    try:
        with db._lock:
            row = db.conn.execute(
                "SELECT started_t, ended_t FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
    except Exception:  # noqa: BLE001 - no sessions table in this build
        log.warning("recap: no sessions table; treating %r as unknown", session_id)
        return None
    if row is None:
        return None
    name = None
    with contextlib.suppress(Exception):
        with db._lock:
            named = db.conn.execute(
                "SELECT name FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        name = None if named is None else named[0]
    return float(row[0]), (None if row[1] is None else float(row[1])), name


# -- assembly -------------------------------------------------------------


def _coverage(db: Database, from_t: float, to_t: float) -> tuple[int, float]:
    ticks = db.ticks_between(from_t, to_t)
    if not ticks:
        return 0, 0.0
    return len(ticks), sum(1 for t in ticks if t.ai is not None) / len(ticks)


def _decision_count(db: Database, from_t: float, to_t: float) -> int:
    return len(db.decisions_between(from_t, to_t))


def _summary_lines(db: Database, from_t: float, to_t: float) -> list[str]:
    days = {day_key(from_t), day_key(to_t)}
    lines = [row for day in sorted(days) for row in db.today_summary_lines(day)]
    return [row.line for row in sorted(lines, key=lambda r: r.t)
            if from_t <= row.t <= to_t]


def _subscore(score) -> dict[str, Any]:
    return {
        "metric": score.metric,
        "layer": score.layer,
        "label": label_for(score.metric),
        "value": score.value,
        "unit": unit_for(score.metric),
        "score": score.score,
        "target": score.target,
        "source": score.source,
        "grade": score.grade,
        "note": score.note,
    }


def _assemble(pipeline, start: float, end: float) -> dict[str, Any]:
    """Every synchronous read and computation one recap needs, in one place.

    Pulled out of :func:`build_recap` so it can run on a worker thread: it is
    pure SQLite and arithmetic, and over a four-hour window it is long enough
    that doing it on the event loop stalls the tick consumer behind it.
    """

    db: Database = pipeline.db
    rows = pipeline.scorer.score_window(start, end)
    moments = select_moments(db, pipeline.reasoner.evidence, start, end)
    tick_count, ai_coverage = _coverage(db, start, end)
    return {
        "moments": moments,
        "subscores": [_subscore(row) for row in rows],
        "overall": rollup(rows),
        "tick_count": tick_count,
        "ai_coverage": ai_coverage,
        "decision_count": _decision_count(db, start, end),
        "summary_lines": _summary_lines(db, start, end),
    }


async def build_recap(
    pipeline,
    *,
    session_id: str | None = None,
    from_t: float | None = None,
    to_t: float | None = None,
    speak: bool = True,
) -> dict[str, Any]:
    """Build, speak and persist one recap. Returns the frozen response shape."""

    sid, name, start, end = resolve_window(pipeline, session_id, from_t, to_t)

    assembled = await asyncio.to_thread(_assemble, pipeline, start, end)
    moments = assembled["moments"]
    subscores = assembled["subscores"]
    overall = assembled["overall"]

    reasoner = getattr(pipeline, "reasoner", None)
    context = RecapContext(
        duration_s=max(0.0, end - start),
        subscores=subscores,
        moments=[m.model_dump() for m in moments],
        summary_lines=assembled["summary_lines"],
        overall=overall,
        # The same brief the thinker and the voice agent get: operator persona
        # (or the built-in one), then the wearer's questionnaire paragraph.
        persona=effective_persona(pipeline.db, getattr(reasoner, "persona", "") or DEFAULT_PERSONA),
    )
    client = _narrative_client(pipeline)
    try:
        narrative, meta = await client.write(context)
    except Exception:  # noqa: BLE001 - a recap must not 500 on a model hiccup
        log.exception("recap narrative failed; falling back to the deterministic one")
        from .narrative import FakeNarrativeClient

        narrative, meta = await FakeNarrativeClient().write(context)

    did_speak = False
    if speak and narrative.spoken.strip():
        did_speak = _speak(narrative, end)

    recap: dict[str, Any] = {
        "id": f"r_{uuid.uuid4().hex[:8]}",
        "session": {
            "id": sid,
            "name": name,
            "from_t": start,
            "to_t": end,
            "duration_s": max(0.0, end - start),
            "tick_count": assembled["tick_count"],
            "ai_coverage": assembled["ai_coverage"],
            "decision_count": assembled["decision_count"],
        },
        "score": {"overall": overall, "subscores": subscores},
        "moments": [m.model_dump() for m in moments],
        "narrative": narrative.model_dump(),
        "spoken": did_speak,
        "generated_at": time.time(),
        "model": str(meta.get("model", "")),
    }
    recap_store(pipeline).save(recap)
    return recap


def _speak(narrative: RecapNarrative, t: float) -> bool:
    """Read the recap aloud through whatever ``speak`` hook is wired.

    Straight through ``get_speak_fn()``, exactly as ``POST /api/speak`` does and
    for the same reason: SPEC §4.6 governs the *model's* speech, and this is an
    operator asking for a summary. On ``sim`` the hook logs; on the glasses it
    is ElevenLabs.

    The hook's answer decides what we claim. ``False`` means it refused the
    utterance -- the glasses hook returns that when no phone is connected --
    and then nothing was said, so nothing goes in ``actions.speech.spoken`` and
    the recap reports ``spoken: false``. Anything else, ``None`` included (the
    older seam signature), is an acceptance and is logged: the dashboard's
    utterance log should not have a hole where the loudest thing the system
    said belongs.
    """

    try:
        result = get_speak_fn()(narrative.spoken, "normal")
    except Exception:  # noqa: BLE001 - the seam must never raise into us
        log.exception("speak() hook raised during recap; swallowing")
        return False
    did_speak = result is not False
    if did_speak:
        spoken.append((t, narrative.spoken, "normal"))
    else:
        log.info("recap: speak hook declined the utterance; not logging it as spoken")
    return did_speak
