"""Tick delivery to Person B (PERSON_A.md A6, decision #1).

PERSON_A.md leaves the transport open — "in-process async callback, SQLite polling, or
WebSocket push?" — and recommends the first: "Both halves are Python on one laptop, so
an in-process subscription is simplest, with SQLite as the durable mirror." That is
what this module implements. Both paths are live at once, so B can switch without a
change on this side.

The governing rule is the same one that governs every other stage: **a slow consumer
must never slow the producer.** If B's gate blocks, T0 keeps its 1 Hz and B loses
ticks. That is the correct trade — CLAUDE.md invariant 1 says T0 never blocks, and a
gate that has fallen behind is reading a world that no longer exists anyway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

log = logging.getLogger(__name__)

Tick = dict[str, Any]


class TickBus:
    """In-process fan-out from T0 to whoever is listening.

    Two ways to consume, because B may want either:

      bus.subscribe(fn)   -> fn(tick) called inline. Must be fast and must not await.
      bus.stream()        -> async iterator, bounded, drops oldest under backpressure.
    """

    def __init__(self, maxsize: int = 64) -> None:
        self._maxsize = maxsize
        self._callbacks: list[Callable[[Tick], None]] = []
        self._queues: list[asyncio.Queue[Tick]] = []
        self.published = 0
        self.dropped = 0

    # -- producer side --

    def publish(self, tick: Tick) -> None:
        """Fan a tick out to every consumer. Never raises, never blocks."""
        self.published += 1

        for cb in self._callbacks:
            try:
                cb(tick)
            except Exception:  # noqa: BLE001
                # A bug in B's gate must not stop A's clock. Log and carry on; this is
                # the seam between two people's code and it has to be one-way.
                log.exception("tick subscriber raised; continuing")

        for q in self._queues:
            if q.full():
                # Drop the OLDEST, not the newest: a consumer that has fallen behind
                # wants the current world, not the backlog. Invariant 2.
                try:
                    q.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(tick)
            except asyncio.QueueFull:
                self.dropped += 1

    # -- consumer side --

    def subscribe(self, callback: Callable[[Tick], None]) -> Callable[[], None]:
        """Register a synchronous callback. Returns an unsubscribe function."""
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    async def stream(self) -> AsyncIterator[Tick]:
        """Yield ticks as they are published. Drops rather than backs up."""
        q: asyncio.Queue[Tick] = asyncio.Queue(maxsize=self._maxsize)
        self._queues.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._queues.remove(q)


class SQLiteMirror:
    """Durable mirror of the tick stream (SPEC §2.5).

    Writes happen on a background thread so a disk stall can never appear as tick
    jitter. §2.5 budgets ~300 bytes per tick and ~900 ticks for a 15-minute demo, so
    this is well under a megabyte and no rotation or downsampling is implemented —
    SPEC §6 is explicit that "the system must survive 15 minutes, not 16 hours."

    Note this stores ticks only. **Frames never touch the disk** (invariant 4); the
    tick's `frame_ref` is a handle into the RAM ring and nothing more.
    """

    def __init__(self, path: str = "ticks.db") -> None:
        self._path = path
        self._q: queue.Queue[Tick | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="tick-mirror", daemon=True)
        self._started = False
        self.written = 0

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def write(self, tick: Tick) -> None:
        """Hand a tick to the writer thread. Returns immediately."""
        if self._started:
            self._q.put(tick)

    def close(self) -> None:
        if self._started:
            self._q.put(None)
            self._thread.join(timeout=5.0)
            self._started = False

    def _run(self) -> None:
        conn = sqlite3.connect(self._path)
        # WAL + NORMAL keeps the writer off the reader's back and avoids an fsync per
        # commit. Person B polls this table, so concurrent read while we write matters.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ticks (
                   seq     INTEGER PRIMARY KEY,
                   tick_id TEXT NOT NULL,
                   t       REAL NOT NULL,
                   has_ai  INTEGER NOT NULL,
                   json    TEXT NOT NULL
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ticks_t ON ticks(t)")
        conn.commit()

        batch: list[tuple[Any, ...]] = []
        while True:
            item = self._q.get()
            if item is None:
                break
            batch.append(
                (item["seq"], item["tick_id"], item["t"], 1 if "ai" in item else 0,
                 json.dumps(item, separators=(",", ":")))
            )
            # Drain anything else already waiting, then commit once.
            while not self._q.empty():
                nxt = self._q.get_nowait()
                if nxt is None:
                    item = None
                    break
                batch.append(
                    (nxt["seq"], nxt["tick_id"], nxt["t"], 1 if "ai" in nxt else 0,
                     json.dumps(nxt, separators=(",", ":")))
                )
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO ticks VALUES (?,?,?,?,?)", batch
                )
                conn.commit()
                self.written += len(batch)
            except sqlite3.Error:
                log.exception("tick mirror write failed; dropping %d ticks", len(batch))
            batch.clear()
            if item is None:
                break
        conn.close()


class JSONLWriter:
    """Append ticks to a .jsonl file. The zero-setup way to hand B a session."""

    def __init__(self, path: str) -> None:
        self._fh = open(path, "a", buffering=1)  # line buffered

    def write(self, tick: Tick) -> None:
        self._fh.write(json.dumps(tick, separators=(",", ":")) + "\n")

    def close(self) -> None:
        self._fh.close()
