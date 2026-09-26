"""What this session has already said, and what has been constant in it.

Two blocks of context the models were not getting (docs/PERSONA_PHILOSOPHY.md
§2.4, §2.5). Each wake-up used to see about a minute of tags and today's memory
lines, so "I already said that" had to be dug out of the log and "that bottle
has been there since he sat down" had to be inferred. Both are facts the system
holds; they are handed over as facts here.

Both readers are bounded (one session, at most ``CONSTANT_LOOKBACK_S`` of
ticks) and never raise: a missing session or an empty table means an empty
block, not a lost wake-up.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from ..db import Database
from .envelope import local_time

log = logging.getLogger(__name__)

__all__ = ["session_start", "said_this_session", "said_block", "constant_block",
           "CONSTANT_LOOKBACK_S", "CONSTANT_MIN_TICKS", "CONSTANT_FRACTION"]

#: How far back the constancy scan reads ticks. Long enough for the whole of
#: a demo session; bounded so a full day never scans a full day.
CONSTANT_LOOKBACK_S = 30 * 60.0
#: Fewer tagged ticks than this and nothing is "constant" yet.
CONSTANT_MIN_TICKS = 20
#: Share of tagged ticks an object or flag must appear in to count as furniture.
CONSTANT_FRACTION = 0.5

#: Flags worth naming when constant, in plain words.
_FLAG_WORDS = (
    ("screen_present", "a screen in view"),
    ("people_present", "people in view"),
    ("outdoor_visible", "the outdoors in view"),
    ("food_present", "food in view"),
)


def session_start(db: Database, now: float) -> float | None:
    """When the open session began, or ``None`` when none is open."""

    try:
        session = db.current_session()
    except Exception:  # pragma: no cover - defensive
        log.debug("could not read the current session", exc_info=True)
        return None
    if session is None or session.started_t > now:
        return None
    return float(session.started_t)


def said_this_session(db: Database, now: float) -> list[tuple[float, str, str]]:
    """``(t, kind, text)`` of every line the voice agent spoke since the session
    began, oldest first. With no session open, nothing is returned: the 45 s
    code window is then the only repeat memory, as before."""

    start = session_start(db, now)
    if start is None:
        return []
    try:
        rows = db.list_conversations(limit=200)
    except Exception:  # pragma: no cover - defensive
        log.debug("could not read conversations", exc_info=True)
        return []
    out: list[tuple[float, str, str]] = []
    for row in rows:
        if float(row.get("opened_t") or 0) < start:
            continue
        for turn in row.get("turns") or []:
            text = str(turn.get("text") or "").strip()
            if turn.get("role") == "agent" and text:
                out.append((float(turn.get("t") or row["opened_t"]),
                            str(turn.get("kind") or "statement"), text))
    out.sort()
    return out


def said_block(db: Database, now: float) -> str:
    """The block both agents read first: what was already said out loud."""

    said = said_this_session(db, now)
    if not said:
        return "Said aloud this session:\nnone so far"
    body = "\n".join(
        f"  {local_time(t, '%H:%M')} {'asked' if kind == 'question' else 'said'}: \"{text}\""
        for t, kind, text in said[-12:]
    )
    return ("Said aloud this session:\n" + body
            + "\nEach of these was said once and is not said again, however the "
              "thing it was about comes and goes.")


def constant_block(db: Database, now: float) -> str | None:
    """What has been in view for most of the session: the furniture.

    Objects named in at least ``CONSTANT_FRACTION`` of the tagged ticks since
    the session began (bounded by ``CONSTANT_LOOKBACK_S``), and the flags that
    were true that often. ``None`` until there are enough tagged ticks to say.
    """

    start = session_start(db, now)
    if start is None:
        return None
    since = max(start, now - CONSTANT_LOOKBACK_S)
    try:
        ticks = db.ticks_between(since, now)
    except Exception:  # pragma: no cover - defensive
        log.debug("could not read ticks for the constancy scan", exc_info=True)
        return None
    tagged = [t for t in ticks if t.ai is not None]
    if len(tagged) < CONSTANT_MIN_TICKS:
        return None
    n = len(tagged)
    objects: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    for tick in tagged:
        ai: Any = tick.ai
        for obj in {str(o).strip().casefold() for o in (ai.objects or []) if str(o).strip()}:
            objects[obj] += 1
        for name, _ in _FLAG_WORDS:
            if getattr(ai, name, False) is True:
                flags[name] += 1
    threshold = CONSTANT_FRACTION * n
    constant_objects = [o for o, c in objects.most_common() if c >= threshold][:8]
    constant_flags = [word for name, word in _FLAG_WORDS if flags[name] >= threshold]
    if not constant_objects and not constant_flags:
        return None
    minutes = max(1, int(round((now - start) / 60.0)))
    parts: list[str] = []
    if constant_objects:
        parts.append(", ".join(constant_objects))
    if constant_flags:
        parts.append("; ".join(constant_flags))
    return (
        f"Constant since the session began at {local_time(start, '%H:%M')} "
        f"({minutes} min, {n} tagged ticks): " + "; ".join(parts) + ".\n"
        "These are the furniture of this session. Their moving in and out of "
        "frame, or into the hand, is not an event and not a reason to speak."
    )
