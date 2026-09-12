"""Durable evidence frames.

SPEC §2.5: frames live 90 seconds in the ring buffer and are never written to
disk from T0. **The only path by which a frame survives is being copied out at
escalation time.** This module is that path, and the copy happens at admission
-- before any model call -- so a slow or failing inference can never cost us
the pixels the decision was made from.

The table is created here rather than in :mod:`pipeline.db` because the
escalated-frame store is the reasoner's concern; ``db.conn`` and ``db._lock``
are the only things it borrows.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..db import Database
from ..frames import FrameStore

log = logging.getLogger(__name__)

__all__ = ["EvidenceStore", "SCHEMA"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS escalated_frames (
    decision_id TEXT NOT NULL,
    frame_ref   TEXT NOT NULL,
    t           REAL NOT NULL,
    jpeg        BLOB NOT NULL,
    PRIMARY KEY (decision_id, frame_ref)
);
"""


class EvidenceStore:
    """Copies escalation frames out of the ring buffer into SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.copied = 0
        self.missing = 0
        with self._lock():
            self.db.conn.executescript(SCHEMA)
            self.db.conn.commit()

    def _lock(self) -> Any:
        lock = getattr(self.db, "_lock", None)
        if lock is not None:
            return lock
        import contextlib

        return contextlib.nullcontext()

    # -- write -----------------------------------------------------------

    def copy(
        self,
        decision_id: str,
        refs: list[tuple[str, float]] | list[str],
        frame_store: FrameStore,
        t: float = 0.0,
    ) -> dict[str, bytes]:
        """Copy ``refs`` out of ``frame_store`` and persist them.

        ``refs`` may be plain refs (stamped with ``t``) or ``(ref, t)`` pairs.
        Missing or already-expired refs are skipped and logged at WARNING --
        SPEC §12.3 calls an expired ref a sign the pipeline has fallen behind,
        not a crash.

        Returns the frames actually copied, keyed by ref, in the order asked
        for. That dict is what the envelope builder reads: nothing downstream
        touches the ring buffer again.
        """

        pairs: list[tuple[str, float]] = [
            (r, t) if isinstance(r, str) else (r[0], r[1]) for r in refs
        ]
        wanted = [ref for ref, _ in pairs]
        if not wanted:
            return {}

        fetched = frame_store.get(wanted)
        rows: list[tuple[str, str, float, bytes]] = []
        out: dict[str, bytes] = {}
        for ref, stamp in pairs:
            jpeg = fetched.get(ref)
            if jpeg is None:
                self.missing += 1
                log.warning(
                    "escalation %s: frame %s missing from the ring buffer "
                    "(expired before the copy?)",
                    decision_id,
                    ref,
                )
                continue
            out[ref] = jpeg
            rows.append((decision_id, ref, stamp, jpeg))

        if rows:
            with self._lock():
                self.db.conn.executemany(
                    "INSERT OR REPLACE INTO escalated_frames"
                    " (decision_id, frame_ref, t, jpeg) VALUES (?,?,?,?)",
                    rows,
                )
                self.db.conn.commit()
            self.copied += len(rows)
        return out

    # -- read ------------------------------------------------------------

    def list(self, decision_id: str) -> list[dict[str, Any]]:
        """Frame metadata for one decision -- no bytes, safe for the dashboard."""

        with self._lock():
            rows = self.db.conn.execute(
                "SELECT decision_id, frame_ref, t, LENGTH(jpeg) AS bytes"
                " FROM escalated_frames WHERE decision_id = ? ORDER BY t ASC",
                (decision_id,),
            ).fetchall()
        return [self._meta(r) for r in rows]

    def get(self, decision_id: str, ref: str) -> bytes | None:
        with self._lock():
            row = self.db.conn.execute(
                "SELECT jpeg FROM escalated_frames"
                " WHERE decision_id = ? AND frame_ref = ?",
                (decision_id, ref),
            ).fetchone()
        return None if row is None else bytes(row[0])

    def count(self, decision_id: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM escalated_frames"
        args: tuple[Any, ...] = ()
        if decision_id is not None:
            sql += " WHERE decision_id = ?"
            args = (decision_id,)
        with self._lock():
            return int(self.db.conn.execute(sql, args).fetchone()[0])

    @staticmethod
    def _meta(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        try:
            return {
                "decision_id": row["decision_id"],
                "frame_ref": row["frame_ref"],
                "t": row["t"],
                "bytes": row["bytes"],
            }
        except (TypeError, IndexError):  # pragma: no cover - row_factory off
            return {
                "decision_id": row[0],
                "frame_ref": row[1],
                "t": row[2],
                "bytes": row[3],
            }
