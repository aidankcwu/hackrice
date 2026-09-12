"""The tick object (SPEC §12) — the contract between Person A and Person B.

"A may add fields freely; B must tolerate any field being absent." (§12)

Three guarantees this module enforces (§12.1):
  sensor  always present
  device  absent under the `webcam` and `replay` adapters
  ai      may be absent entirely — when the T0 VLM call overran its 1 s budget

On that last one, §2.4 and §11.7 disagree: §2.4 allows carrying the last known AI
values forward with a bumped `age_ms`, §11.7 says the block must be absent. §11.7 wins
(CLAUDE.md invariant 3 agrees) — never stale, always absent.
"""

from __future__ import annotations

import time
from typing import Any

SCHEMA_VERSION = 1

# §12.3: a frame_ref is "valid for 90 seconds from the tick's timestamp".
FRAME_TTL_S = 90.0

# §2.4 / invariant 3.
VLM_BUDGET_S = 1.0


def tick_id(seq: int) -> str:
    """`t_00001742` — 8-digit zero-padded, matching the numeric part of `seq` (§12)."""
    return f"t_{seq:08d}"


def frame_ref(seq: int) -> str:
    """`f_00001742` — same counter as the tick that produced it (§12)."""
    return f"f_{seq:08d}"


def ai_block(fields: dict[str, Any], as_of: float, now: float) -> dict[str, Any]:
    """Wrap a coerced §9 field set with its staleness metadata.

    `age_ms` is how old the *inference* is relative to this tick, which is what §12.2
    tells B to decay confidence against — "a 4 s-old food_present is weaker evidence
    than a 200 ms-old one."
    """
    return {
        "as_of": round(as_of, 3),
        "age_ms": max(0, int(round((now - as_of) * 1000))),
        **fields,
    }


def build_tick(
    *,
    seq: int,
    t: float,
    sensor: dict[str, Any],
    device: dict[str, Any] | None = None,
    ai: dict[str, Any] | None = None,
    has_frame: bool = True,
) -> dict[str, Any]:
    """Assemble one §12 tick.

    `device` and `ai` are omitted entirely when None — not set to null. B checks for
    absence, and a present-but-null block would read as "we looked and there was
    nothing there" rather than "this adapter does not produce this".
    """
    tick: dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "tick_id": tick_id(seq),
        "t": round(t, 3),
        "seq": seq,
        "sensor": sensor,
    }
    if device is not None:
        tick["device"] = device
    if ai is not None:
        tick["ai"] = ai
    if has_frame:
        tick["frame_ref"] = frame_ref(seq)
    return tick


def ref_is_live(ref: str, tick_t: float, now: float | None = None) -> bool:
    """Whether a frame_ref is still inside its 90 s window (§12.3)."""
    return (time.time() if now is None else now) - tick_t < FRAME_TTL_S
