"""Backend FrameStore view over Person A's RAM-only FrameRing."""

from __future__ import annotations

from longevity.ring import FrameRing


class RingFrameStore:
    def __init__(self, ring: FrameRing) -> None:
        self.ring = ring

    def get(self, refs: list[str]) -> dict[str, bytes]:
        found, _missing = self.ring.get_many(refs)
        return {frame.ref: frame.jpeg for frame in found}

    def put(self, ref: str, jpeg_bytes: bytes, t: float) -> None:
        self.ring.put(ref, jpeg_bytes, t)

    def expire(self, now: float) -> int:
        return self.ring.sweep(now)

    def __len__(self) -> int:
        return len(self.ring)
