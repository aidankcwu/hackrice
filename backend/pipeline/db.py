"""SQLite storage.

Synchronous and deliberately simple (SPEC §6: in-memory state plus SQLite; the
system must survive 15 minutes, not 16 hours). Called from async code, so every
public method holds a :class:`threading.Lock` and the connection is opened with
``check_same_thread=False``.

**Ticks contain no pixels** (SPEC §2.5 rule 3). This module stores the tick's
JSON blocks and nothing that came out of a frame buffer; frames live only in the
ring buffer (:mod:`pipeline.frames`) until they are copied out at escalation.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Decision,
    Episode,
    Insight,
    PendingCheck,
    Score,
    SeededRow,
    Tick,
    TodaySummaryLine,
)

log = logging.getLogger(__name__)

__all__ = ["Database", "day_key"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    tick_id   TEXT PRIMARY KEY,
    t         REAL NOT NULL,
    seq       INTEGER NOT NULL,
    frame_ref TEXT NOT NULL,
    has_ai    INTEGER NOT NULL,
    v         INTEGER NOT NULL DEFAULT 1,
    sensor    TEXT NOT NULL,
    device    TEXT,
    ai        TEXT
);
CREATE INDEX IF NOT EXISTS ix_ticks_t ON ticks(t);
CREATE INDEX IF NOT EXISTS ix_ticks_seq ON ticks(seq);

CREATE TABLE IF NOT EXISTS episodes (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    start_t    REAL NOT NULL,
    end_t      REAL,
    duration_s REAL NOT NULL DEFAULT 0,
    dominant   TEXT NOT NULL DEFAULT '{}',
    tick_count INTEGER NOT NULL DEFAULT 0,
    open       INTEGER NOT NULL DEFAULT 1,
    day        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_episodes_day ON episodes(day);

CREATE TABLE IF NOT EXISTS decisions (
    id             TEXT PRIMARY KEY,
    t              REAL NOT NULL,
    "trigger"      TEXT NOT NULL,
    trigger_tick_id TEXT NOT NULL,
    episode_id     TEXT,
    interpretation TEXT NOT NULL DEFAULT '',
    confidence     REAL NOT NULL DEFAULT 0,
    actions        TEXT NOT NULL DEFAULT '[]',
    spoke          INTEGER NOT NULL DEFAULT 0,
    dropped        INTEGER NOT NULL DEFAULT 0,
    drop_reason    TEXT,
    latency_ms     INTEGER,
    model          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_decisions_t ON decisions(t);

CREATE TABLE IF NOT EXISTS insights (
    id          TEXT PRIMARY KEY,
    t           REAL NOT NULL,
    category    TEXT NOT NULL,
    text        TEXT NOT NULL,
    decision_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_insights_t ON insights(t);

CREATE TABLE IF NOT EXISTS pending_checks (
    id          TEXT PRIMARY KEY,
    created_t   REAL NOT NULL,
    due_t       REAL,
    condition   TEXT,
    reason      TEXT NOT NULL DEFAULT '',
    decision_id TEXT,
    fired       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pending_due ON pending_checks(fired, due_t);

CREATE TABLE IF NOT EXISTS scores (
    metric     TEXT NOT NULL,
    layer      TEXT NOT NULL DEFAULT '',
    period     TEXT NOT NULL,
    period_key TEXT NOT NULL,
    value      REAL,
    target     TEXT NOT NULL DEFAULT '',
    score      REAL NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'live',
    grade      TEXT NOT NULL DEFAULT '',
    note       TEXT,
    PRIMARY KEY (metric, period, period_key)
);

CREATE TABLE IF NOT EXISTS seeded (
    day    TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL,
    unit   TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (day, metric)
);

CREATE TABLE IF NOT EXISTS biometric_series (
    t      REAL NOT NULL,
    metric TEXT NOT NULL,
    value  REAL NOT NULL,
    source TEXT NOT NULL,
    -- 'seed' (the SPEC §14.2 demo series) or 'live' (a real wearable, pushed
    -- in over /api/wearables/ingest). Added after the table shipped, so
    -- init_schema also applies it to existing files via ALTER TABLE.
    origin TEXT NOT NULL DEFAULT 'seed',
    PRIMARY KEY (t, metric)
);
CREATE INDEX IF NOT EXISTS ix_biometric_metric ON biometric_series(metric, t);

CREATE TABLE IF NOT EXISTS today_summary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,
    t           REAL NOT NULL,
    line        TEXT NOT NULL,
    decision_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_summary_day ON today_summary(day, t);

CREATE TABLE IF NOT EXISTS escalated_frames (
    decision_id TEXT NOT NULL,
    frame_ref   TEXT NOT NULL,
    t           REAL NOT NULL,
    jpeg        BLOB NOT NULL,
    PRIMARY KEY (decision_id, frame_ref)
);
"""


def day_key(t: float) -> str:
    """``YYYY-MM-DD`` in local time for a unix timestamp."""

    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


class Database:
    """Thin synchronous repository over SQLite."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.path = str(path)

    # -- lifecycle -------------------------------------------------------

    def connect(self, path: str | Path | None = None) -> "Database":
        if path is not None:
            self.path = str(path)
        if self.path not in (":memory:", ""):
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._conn = conn
        log.debug("sqlite connected: %s", self.path)
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._conn

    def init_schema(self) -> "Database":
        with self._lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()
        return self

    def _migrate(self) -> None:
        """Additive column migrations for databases created by older builds.

        ``CREATE TABLE IF NOT EXISTS`` leaves an existing file alone, so a
        column added after a table shipped has to be applied by hand. Each step
        is guarded by ``PRAGMA table_info`` and is a no-op on a fresh database.
        """

        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(biometric_series)")
        }
        if "origin" not in columns:
            self.conn.execute(
                "ALTER TABLE biometric_series"
                " ADD COLUMN origin TEXT NOT NULL DEFAULT 'seed'"
            )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.commit()
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "Database":
        if self._conn is None:
            self.connect()
        return self.init_schema()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- ticks -----------------------------------------------------------

    def insert_tick(self, tick: Tick) -> None:
        """Persist a tick. Never stores pixels -- only the JSON blocks."""

        row = (
            tick.tick_id,
            tick.t,
            tick.seq,
            tick.frame_ref,
            1 if tick.ai is not None else 0,
            tick.v,
            _json(tick.sensor.model_dump()),
            _json(tick.device.model_dump()) if tick.device is not None else None,
            _json(tick.ai.model_dump()) if tick.ai is not None else None,
        )
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO ticks"
                " (tick_id, t, seq, frame_ref, has_ai, v, sensor, device, ai)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                row,
            )
            self.conn.commit()

    @staticmethod
    def _tick_from_row(row: sqlite3.Row) -> Tick:
        return Tick.model_validate(
            {
                "v": row["v"],
                "tick_id": row["tick_id"],
                "t": row["t"],
                "seq": row["seq"],
                "sensor": json.loads(row["sensor"]),
                "device": json.loads(row["device"]) if row["device"] else None,
                "ai": json.loads(row["ai"]) if row["ai"] else None,
                "frame_ref": row["frame_ref"],
            }
        )

    def recent_ticks(self, n: int = 60) -> list[Tick]:
        """The ``n`` most recent ticks, oldest first."""

        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM (SELECT * FROM ticks ORDER BY t DESC LIMIT ?)"
                " ORDER BY t ASC",
                (n,),
            ).fetchall()
        return [self._tick_from_row(r) for r in rows]

    def ticks_between(self, t0: float, t1: float) -> list[Tick]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM ticks WHERE t >= ? AND t <= ? ORDER BY t ASC",
                (t0, t1),
            ).fetchall()
        return [self._tick_from_row(r) for r in rows]

    # -- episodes --------------------------------------------------------

    def upsert_episode(self, episode: Episode) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO episodes"
                " (id, kind, start_t, end_t, duration_s, dominant, tick_count, open, day)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    episode.id,
                    episode.kind,
                    episode.start_t,
                    episode.end_t,
                    episode.duration_s,
                    _json(episode.dominant),
                    episode.tick_count,
                    1 if episode.open else 0,
                    day_key(episode.start_t),
                ),
            )
            self.conn.commit()

    def list_episodes(self, day: str | None = None) -> list[Episode]:
        sql = "SELECT * FROM episodes"
        args: tuple[Any, ...] = ()
        if day is not None:
            sql += " WHERE day = ?"
            args = (day,)
        sql += " ORDER BY start_t ASC"
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [
            Episode(
                id=r["id"],
                kind=r["kind"],
                start_t=r["start_t"],
                end_t=r["end_t"],
                duration_s=r["duration_s"],
                dominant=json.loads(r["dominant"]),
                tick_count=r["tick_count"],
                open=bool(r["open"]),
            )
            for r in rows
        ]

    # -- decisions -------------------------------------------------------

    def insert_decision(self, decision: Decision) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO decisions"
                " (id, t, \"trigger\", trigger_tick_id, episode_id, interpretation,"
                "  confidence, actions, spoke, dropped, drop_reason, latency_ms, model)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision.id,
                    decision.t,
                    decision.trigger,
                    decision.trigger_tick_id,
                    decision.episode_id,
                    decision.interpretation,
                    decision.confidence,
                    _json(decision.actions),
                    1 if decision.spoke else 0,
                    1 if decision.dropped else 0,
                    decision.drop_reason,
                    decision.latency_ms,
                    decision.model,
                ),
            )
            self.conn.commit()

    def list_decisions(self, limit: int = 50) -> list[Decision]:
        """Most recent decisions first -- this is the dashboard's silent feed."""

        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM decisions ORDER BY t DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Decision(
                id=r["id"],
                t=r["t"],
                trigger=r["trigger"],
                trigger_tick_id=r["trigger_tick_id"],
                episode_id=r["episode_id"],
                interpretation=r["interpretation"],
                confidence=r["confidence"],
                actions=json.loads(r["actions"]),
                spoke=bool(r["spoke"]),
                dropped=bool(r["dropped"]),
                drop_reason=r["drop_reason"],
                latency_ms=r["latency_ms"],
                model=r["model"],
            )
            for r in rows
        ]

    # -- insights --------------------------------------------------------

    def insert_insight(self, insight: Insight) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO insights (id, t, category, text, decision_id)"
                " VALUES (?,?,?,?,?)",
                (
                    insight.id,
                    insight.t,
                    insight.category,
                    insight.text,
                    insight.decision_id,
                ),
            )
            self.conn.commit()

    def list_insights(self, limit: int = 100) -> list[Insight]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM insights ORDER BY t DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Insight(
                id=r["id"],
                t=r["t"],
                category=r["category"],
                text=r["text"],
                decision_id=r["decision_id"],
            )
            for r in rows
        ]

    # -- pending checks (`watch`) ----------------------------------------

    def insert_pending_check(self, check: PendingCheck) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO pending_checks"
                " (id, created_t, due_t, condition, reason, decision_id, fired)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    check.id,
                    check.created_t,
                    check.due_t,
                    check.condition,
                    check.reason,
                    check.decision_id,
                    1 if check.fired else 0,
                ),
            )
            self.conn.commit()

    def due_pending_checks(self, now: float) -> list[PendingCheck]:
        """Unfired checks whose ``due_t`` has passed, plus condition-only ones."""

        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM pending_checks WHERE fired = 0"
                " AND (due_t IS NULL OR due_t <= ?) ORDER BY created_t ASC",
                (now,),
            ).fetchall()
        return [
            PendingCheck(
                id=r["id"],
                created_t=r["created_t"],
                due_t=r["due_t"],
                condition=r["condition"],
                reason=r["reason"],
                decision_id=r["decision_id"],
                fired=bool(r["fired"]),
            )
            for r in rows
        ]

    def mark_pending_fired(self, check_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE pending_checks SET fired = 1 WHERE id = ?", (check_id,)
            )
            self.conn.commit()

    # -- scores ----------------------------------------------------------

    def upsert_score(self, score: Score) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO scores"
                " (metric, layer, period, period_key, value, target, score, source,"
                "  grade, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    score.metric,
                    score.layer,
                    score.period,
                    score.period_key,
                    score.value,
                    score.target,
                    score.score,
                    score.source,
                    score.grade,
                    score.note,
                ),
            )
            self.conn.commit()

    def list_scores(
        self, period: str | None = None, period_key: str | None = None
    ) -> list[Score]:
        sql = "SELECT * FROM scores"
        clauses: list[str] = []
        args: list[Any] = []
        if period is not None:
            clauses.append("period = ?")
            args.append(period)
        if period_key is not None:
            clauses.append("period_key = ?")
            args.append(period_key)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY layer, metric"
        with self._lock:
            rows = self.conn.execute(sql, tuple(args)).fetchall()
        return [Score(**dict(r)) for r in rows]

    # -- seeded rows -----------------------------------------------------

    def insert_seeded_rows(self, rows: Iterable[SeededRow]) -> int:
        payload = [(r.day, r.metric, r.value, r.unit, r.source) for r in rows]
        if not payload:
            return 0
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO seeded (day, metric, value, unit, source)"
                " VALUES (?,?,?,?,?)",
                payload,
            )
            self.conn.commit()
        return len(payload)

    def list_seeded(self, day_from: str, day_to: str) -> list[SeededRow]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM seeded WHERE day >= ? AND day <= ?"
                " ORDER BY day ASC, metric ASC",
                (day_from, day_to),
            ).fetchall()
        return [SeededRow(**dict(r)) for r in rows]

    def insert_biometric_series(
        self, rows: Iterable[tuple[float, str, float, str]], origin: str = "seed"
    ) -> int:
        """Store ``(t, metric, value, source)`` rows. Idempotent on ``(t, metric)``.

        ``origin`` is ``'seed'`` for the SPEC §14.2 demo series and ``'live'``
        for anything a real wearable pushed in.
        """

        payload = [(t, metric, value, source, origin)
                   for t, metric, value, source in rows]
        if not payload:
            return 0
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO biometric_series"
                " (t, metric, value, source, origin) VALUES (?,?,?,?,?)",
                payload,
            )
            self.conn.commit()
        return len(payload)

    def _biometric_rows(
        self, metric: str, t0: float, t1: float, origin: str | None = None
    ) -> list[sqlite3.Row]:
        """Rows for one metric and window, with live winning over seed.

        With ``origin=None`` (the normal read) a window that contains even one
        ``live`` row returns *only* its live rows: a connected wearable
        replaces the seeded demo series rather than interleaving with it, which
        would otherwise draw a sawtooth between two different people's hearts.
        Pass ``origin`` explicitly to read one layer regardless.
        """

        sql = ("SELECT t, value, source, origin FROM biometric_series"
               " WHERE metric = ? AND t >= ? AND t <= ?")
        args: list[object] = [metric, t0, t1]
        if origin is not None:
            sql += " AND origin = ?"
            args.append(origin)
        with self._lock:
            rows = self.conn.execute(sql + " ORDER BY t ASC", tuple(args)).fetchall()
        if origin is None and any(row["origin"] == "live" for row in rows):
            return [row for row in rows if row["origin"] == "live"]
        return rows

    def biometric_series(
        self, metric: str, t0: float, t1: float, origin: str | None = None
    ) -> list[tuple[float, float]]:
        return [(row["t"], row["value"])
                for row in self._biometric_rows(metric, t0, t1, origin)]

    def biometric_window(
        self, metric: str, t0: float, t1: float, origin: str | None = None
    ) -> dict[str, object]:
        """``biometric_series`` plus the source and origin the points came from."""

        rows = self._biometric_rows(metric, t0, t1, origin)
        return {
            "source": rows[-1]["source"] if rows else "",
            "origin": rows[-1]["origin"] if rows else (origin or "seed"),
            "points": [(row["t"], row["value"]) for row in rows],
        }

    def latest_biometric(
        self, metric: str
    ) -> tuple[float, float, str, str] | None:
        """Newest ``(t, value, source, origin)`` for one metric, or ``None``.

        Live wins on ties: if a wearable and the seed both stamped the same
        second, the wearable is the one that measured it.
        """

        with self._lock:
            row = self.conn.execute(
                "SELECT t, value, source, origin FROM biometric_series"
                " WHERE metric = ? ORDER BY t DESC,"
                " CASE origin WHEN 'live' THEN 0 ELSE 1 END ASC LIMIT 1",
                (metric,),
            ).fetchone()
        if row is None:
            return None
        return (row["t"], row["value"], row["source"], row["origin"])

    def biometric_metrics_present(self) -> list[dict[str, object]]:
        """One row per ``(metric, origin)`` actually stored, newest source first."""

        with self._lock:
            rows = self.conn.execute(
                "SELECT metric, origin, COUNT(*) AS n, MAX(t) AS last_t,"
                " (SELECT source FROM biometric_series b2"
                "  WHERE b2.metric = b.metric AND b2.origin = b.origin"
                "  ORDER BY t DESC LIMIT 1) AS source"
                " FROM biometric_series b GROUP BY metric, origin"
                " ORDER BY metric ASC, origin ASC"
            ).fetchall()
        return [
            {"metric": row["metric"], "source": row["source"],
             "origin": row["origin"], "count": row["n"], "last_t": row["last_t"]}
            for row in rows
        ]

    # -- today's summary (part 4 of the envelope) ------------------------

    def append_summary_line(self, line: TodaySummaryLine) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO today_summary (day, t, line, decision_id)"
                " VALUES (?,?,?,?)",
                (day_key(line.t), line.t, line.line, line.decision_id),
            )
            self.conn.commit()

    def today_summary_lines(self, day: str | None = None) -> list[TodaySummaryLine]:
        import time as _time

        key = day if day is not None else day_key(_time.time())
        with self._lock:
            rows = self.conn.execute(
                "SELECT t, line, decision_id FROM today_summary WHERE day = ?"
                " ORDER BY t ASC",
                (key,),
            ).fetchall()
        return [
            TodaySummaryLine(t=r["t"], line=r["line"], decision_id=r["decision_id"])
            for r in rows
        ]

    # -- stats -----------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT (SELECT COUNT(*) FROM ticks),"
                " (SELECT COUNT(*) FROM ticks WHERE has_ai = 1),"
                " (SELECT COUNT(*) FROM decisions),"
                " (SELECT COUNT(*) FROM decisions WHERE dropped = 1),"
                " (SELECT COUNT(*) FROM biometric_series)"
            )
            ticks, ai_ticks, decisions, dropped, biometrics = cur.fetchone()
        return {
            "tick_count": ticks,
            "ai_tick_count": ai_ticks,
            "decision_count": decisions,
            "dropped_count": dropped,
            "biometric_count": biometrics,
        }
