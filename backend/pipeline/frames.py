"""Frame ring buffer.

SPEC §2.5: frames live in laptop RAM for 90 seconds and are never written to
disk from T0. The only path by which a frame survives is being copied out at
escalation time.

Person A owns the real ring buffer on the capture side (SPEC §13.1). This module
is the reference implementation we develop against, and it MUST stay swappable:
consumers depend on the :class:`FrameStore` protocol, never on
:class:`InMemoryFrameStore` directly.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

__all__ = ["FrameStore", "InMemoryFrameStore"]


@runtime_checkable
class FrameStore(Protocol):
    """The contract between the escalation path and whoever holds the pixels."""

    def put(self, ref: str, jpeg_bytes: bytes, t: float) -> None:
        """Store a JPEG under ``ref``, stamped at tick time ``t``."""
        ...

    def get(self, refs: list[str]) -> dict[str, bytes]:
        """Fetch frames by ref. Missing or expired refs are simply omitted.

        SPEC §12.3: an expired ref is a signal the pipeline has fallen behind --
        log it, don't crash.
        """
        ...

    def expire(self, now: float) -> int:
        """Evict everything older than the TTL. Returns the number evicted."""
        ...

    def __len__(self) -> int:
        ...


class InMemoryFrameStore:
    """An ordered ring keyed by frame ref, with a wall-clock TTL.

    Thread-safe: the capture side may fill it from a socket thread while the
    escalation path reads from the event loop.
    """

    def __init__(self, ttl_s: float = 90.0, max_frames: int | None = None) -> None:
        self.ttl_s = ttl_s
        #: Hard cap as a belt-and-braces guard against a clock that never advances.
        self.max_frames = max_frames if max_frames is not None else int(ttl_s * 4) + 16
        self._frames: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._lock = threading.Lock()
        self.evicted = 0
        self.misses = 0

    def put(self, ref: str, jpeg_bytes: bytes, t: float) -> None:
        with self._lock:
            self._frames[ref] = (t, jpeg_bytes)
            self._frames.move_to_end(ref)
            self._expire_locked(t)
            while len(self._frames) > self.max_frames:
                self._frames.popitem(last=False)
                self.evicted += 1

    def get(self, refs: list[str]) -> dict[str, bytes]:
        out: dict[str, bytes] = {}
        with self._lock:
            for ref in refs:
                entry = self._frames.get(ref)
                if entry is None:
                    self.misses += 1
                    log.warning("frame ref %s missing or expired", ref)
                    continue
                out[ref] = entry[1]
        return out

    def expire(self, now: float) -> int:
        with self._lock:
            return self._expire_locked(now)

    def _expire_locked(self, now: float) -> int:
        cutoff = now - self.ttl_s
        n = 0
        while self._frames:
            ref, (t, _) = next(iter(self._frames.items()))
            if t > cutoff:
                break
            self._frames.popitem(last=False)
            self.evicted += 1
            n += 1
        return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def __contains__(self, ref: object) -> bool:
        with self._lock:
            return ref in self._frames

    @property
    def bytes_held(self) -> int:
        with self._lock:
            return sum(len(b) for _, b in self._frames.values())
