"""The `CaptureSource` interface (PERSON_A.md A3).

All three adapters hang off this — `replay`, `webcam`, `glasses` (SPEC §11.3) — so the
shape is frozen here before any of them are written. A wrong shape costs three rewrites.

A source is an async iterator of `Frame`. It yields the newest frame available and
never buffers: "Drop, never queue — at every stage. A stale frame has negative value."
(CLAUDE.md invariant 2.)
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Frame:
    """One captured frame, as every adapter produces it.

    `device` carries the raw phone sensor payload and is None for `webcam` and
    `replay` — §12.1 says the tick's `device` block is absent under those adapters.
    The values here are raw (§11.2: "The phone computes nothing from them"); deriving
    `accel_rms` from the accelerometer vector is the glasses adapter's job, Mac-side.
    """

    t: float
    """Capture timestamp, unix epoch seconds. Set by whoever observed the frame."""

    jpeg: bytes
    """Encoded JPEG, ~512 px, q70, ~40 KB (§2.2). Adapters encode; T0 never re-encodes."""

    device: dict[str, Any] | None = None
    """Raw phone sensors, or None. See §11.2 for the capture packet shape."""

    meta: dict[str, Any] = field(default_factory=dict)
    """Adapter-local debug info. Never reaches the tick."""


class CaptureSource(abc.ABC):
    """Base class for the three adapters selected by `--source`."""

    name: str = "base"

    @abc.abstractmethod
    def frames(self) -> AsyncIterator[Frame]:
        """Yield frames at roughly 1 Hz until the source is exhausted or closed.

        Implementations must not block the event loop: any decode, disk read, or
        socket wait belongs in a thread or an await. Invariant 1 — T0 never blocks.
        """

    async def aclose(self) -> None:
        """Release adapter resources. Safe to call twice."""

    async def __aenter__(self) -> "CaptureSource":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
