"""The frame ring buffer (PERSON_A.md A7, SPEC §2.5).

Frames live here and nowhere else. Three rules from §2.5, in force for this module:

  1. "Frames are never written to disk from T0."  Nothing in this file opens a file.
  2. The only way a frame survives is being copied out at escalation, which is Person
     B's code reading `GET /frames` (A8) inside the 90 s window.
  3. "Ticks contain no pixels."  The tick carries a `frame_ref`; the bytes stay here.

That retention policy is also the privacy answer: raw imagery has a 90-second lifetime
in volatile memory and is never persisted except as consented evidence attached to a
logged event. Expired bytes are dropped, not merely hidden — every read path sweeps, so
a stalled capture loop cannot leave a frame resident past its TTL.

Sizing: at 1 Hz with ~40 KB JPEGs (§2.2), a 90 s window is ~90 entries and ~3.5 MB.
"""

from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass
from typing import Iterator

from .tick import FRAME_TTL_S, ref_is_live

# A safety bound, not the contract. The TTL is what governs at 1 Hz — 90 s of frames is
# ~90 entries, well under this. The cap only binds when frames arrive faster than
# real time, which happens under `replay --speed 10`, and it exists so a wrong
# timestamp can never grow the buffer without limit.
DEFAULT_MAX_FRAMES = 256


@dataclass(frozen=True, slots=True)
class StoredFrame:
    """One frame in the ring. `t` is the capture timestamp, not the insertion time."""

    ref: str
    t: float
    jpeg: bytes

    @property
    def nbytes(self) -> int:
        return len(self.jpeg)

    def age_s(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.t


@dataclass(slots=True)
class RingStats:
    """Health counters. `count` and `bytes` are what should be flat over a long run."""

    count: int
    bytes: int
    oldest_age_s: float | None
    newest_age_s: float | None
    puts: int
    evicted_ttl: int
    evicted_cap: int
    hits: int
    misses: int


class FrameRing:
    """A TTL-bounded, RAM-only store mapping `frame_ref` -> JPEG bytes.

    Two structures: a dict for lookup, and a min-heap of `(t, ref)` that keeps the
    oldest *capture* time at the front. Eviction only ever looks at that front, so a
    steady 1 Hz producer does O(log n) work per put and pops exactly one frame — it
    never walks the buffer. Ordering by capture time rather than insertion order costs
    the heap, and buys correctness when frames arrive out of order, which an unsorted
    directory listing in the replay adapter will do.

    Guarded by a lock. The T0 capture loop writes and the FastAPI handler reads, and
    those may end up on different threads depending on how the loop is driven. An
    uncontended lock costs tens of nanoseconds, which is nothing against the ~5 ms
    sensor budget, and it removes a whole class of bug that would only show up under
    load during the demo.
    """

    def __init__(
        self, ttl_s: float = FRAME_TTL_S, max_frames: int = DEFAULT_MAX_FRAMES
    ) -> None:
        self.ttl_s = ttl_s
        self.max_frames = max_frames
        self._frames: dict[str, StoredFrame] = {}
        self._order: list[tuple[float, str]] = []  # min-heap by capture time
        self._bytes = 0
        self._lock = threading.RLock()
        self._puts = 0
        self._evicted_ttl = 0
        self._evicted_cap = 0
        self._hits = 0
        self._misses = 0

    def _is_live(self, ref: str, t: float, now: float) -> bool:
        """§12.3's 90 s window, taken from `tick.ref_is_live` so the rule has one home.

        `ttl_s` differs from the contract only in tests and under `replay --speed`,
        where the same inequality runs against a shorter window.
        """
        if self.ttl_s == FRAME_TTL_S:
            return ref_is_live(ref, t, now)
        return now - t < self.ttl_s

    # --- writing ---------------------------------------------------------------

    def put(self, ref: str, jpeg: bytes, t: float, now: float | None = None) -> int:
        """Store a frame and evict whatever has aged out. Returns frames evicted.

        Bounded work: eviction touches only the oldest end, so a steady 1 Hz producer
        evicts at most one frame per put. Invariant 1 holds.
        """
        now = time.time() if now is None else now
        with self._lock:
            old = self._frames.pop(ref, None)
            if old is not None:
                self._bytes -= old.nbytes
            self._frames[ref] = StoredFrame(ref=ref, t=t, jpeg=jpeg)
            self._bytes += len(jpeg)
            heapq.heappush(self._order, (t, ref))
            self._puts += 1
            return self._evict_locked(now)

    def _drop_locked(self, ref: str) -> None:
        f = self._frames.pop(ref, None)
        if f is not None:
            self._bytes -= f.nbytes

    def _peek_locked(self) -> tuple[float, str] | None:
        """The oldest live entry, discarding heap entries left behind by overwrites."""
        while self._order:
            t, ref = self._order[0]
            f = self._frames.get(ref)
            if f is not None and f.t == t:
                return t, ref
            heapq.heappop(self._order)
        return None

    def _evict_locked(self, now: float) -> int:
        evicted = 0
        while (head := self._peek_locked()) is not None:
            t, ref = head
            if self._is_live(ref, t, now):
                break  # the oldest is live, so everything behind it is too
            heapq.heappop(self._order)
            self._drop_locked(ref)
            self._evicted_ttl += 1
            evicted += 1
        while len(self._frames) > self.max_frames:
            head = self._peek_locked()
            if head is None:
                break
            heapq.heappop(self._order)
            self._drop_locked(head[1])
            self._evicted_cap += 1
            evicted += 1
        return evicted

    def sweep(self, now: float | None = None) -> int:
        """Evict aged-out frames without inserting. For an idle or stopped producer."""
        now = time.time() if now is None else now
        with self._lock:
            return self._evict_locked(now)

    # --- reading ---------------------------------------------------------------

    def get(self, ref: str, now: float | None = None) -> StoredFrame | None:
        """Fetch one frame, or None if unknown or past its 90 s window (§12.3).

        Sweeps first. Eviction is otherwise driven by puts, so without this a stalled
        producer would both serve and *retain* frames the privacy story says are gone.
        """
        now = time.time() if now is None else now
        with self._lock:
            self._evict_locked(now)
            f = self._frames.get(ref)
            if f is None or not self._is_live(ref, f.t, now):
                self._misses += 1
                return None
            self._hits += 1
            return f

    def get_many(
        self, refs: list[str], now: float | None = None
    ) -> tuple[list[StoredFrame], list[str]]:
        """Fetch refs in the order asked. Returns (found, missing).

        Escalation wants four frames (§4.3) and would rather have three than none, so
        the caller gets both lists and decides.
        """
        now = time.time() if now is None else now
        found: list[StoredFrame] = []
        missing: list[str] = []
        with self._lock:
            for ref in refs:
                f = self.get(ref, now=now)
                (found.append(f) if f is not None else missing.append(ref))
        return found, missing

    # --- introspection ---------------------------------------------------------

    def stats(self, now: float | None = None) -> RingStats:
        """Health counters, after a sweep — `count` is what is live, not what is held."""
        now = time.time() if now is None else now
        with self._lock:
            self._evict_locked(now)
            ages = [now - f.t for f in self._frames.values()]
            return RingStats(
                count=len(self._frames),
                bytes=self._bytes,
                oldest_age_s=max(ages) if ages else None,
                newest_age_s=min(ages) if ages else None,
                puts=self._puts,
                evicted_ttl=self._evicted_ttl,
                evicted_cap=self._evicted_cap,
                hits=self._hits,
                misses=self._misses,
            )

    @property
    def nbytes(self) -> int:
        """Approximate footprint: JPEG payload only. Per-entry overhead (dict slot, heap
        entry, `StoredFrame`) is ~250 B, about 0.6% at the §2.2 frame size — ignored."""
        with self._lock:
            return self._bytes

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()
            self._order.clear()
            self._bytes = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def __contains__(self, ref: object) -> bool:
        return isinstance(ref, str) and self.get(ref) is not None

    def __iter__(self) -> Iterator[StoredFrame]:
        with self._lock:
            return iter(list(self._frames.values()))


# --- the process-wide ring -----------------------------------------------------

_DEFAULT: FrameRing | None = None


def default_ring() -> FrameRing:
    """The one ring T0 uses.

    There is a single capture loop (A6) writing and a single HTTP server (A8) reading,
    in one process, so they must hold the same instance. Tests and anything wanting
    isolation construct their own `FrameRing()` and pass it to `frames.attach`.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = FrameRing()
    return _DEFAULT
