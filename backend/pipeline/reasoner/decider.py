"""The decider (docs/PERCEPTION.md, "Decider and writers").

Jev cannot see a frame, so its input is a compact JSON state built from text
the pipeline already holds: the last few ticks' tags and captions, the open
episodes, today's summary, the persona and the seven-day trends. No frames, no
embeddings, no raw sensor numbers, no phash.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from longevity.ai_fields import BOOL_FIELDS

from ..models import Episode, Escalation, Tick
from . import envelope

__all__ = ["build_state", "state_size_ok"]

CAPTION_MAX = 120
OBJECT_MAX = 40
SUMMARY_MAX = 160
PERSONA_MAX = 1200
TRENDS_MAX = 1200

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _cut(text: object, limit: int) -> str:
    return str(text or "")[:limit]


def _hot(tick: Tick) -> list[str]:
    """``watch.hot`` read leniently: ``Tick`` has no ``watch`` field yet, so the
    block arrives as an extra (``extra="allow"``) and may be absent or malformed."""

    watch = getattr(tick, "watch", None) or (tick.model_extra or {}).get("watch")
    hot = watch.get("hot") if isinstance(watch, dict) else getattr(watch, "hot", None)
    return [_cut(name, OBJECT_MAX) for name in hot] if isinstance(hot, list) else []


def _tick_entry(tick: Tick, now: float) -> dict[str, Any]:
    age = round(now - tick.t, 1)
    if tick.ai is None or not tick.ai_fresh(envelope.AI_MAX_AGE_MS):
        return {"age_s": age, "true": [], "no_ai": True}
    ai = tick.ai
    return {
        "age_s": age,
        "true": [name for name in BOOL_FIELDS if getattr(ai, name, None) is True],
        "scene": tick.enum("scene", envelope.AI_MAX_AGE_MS),
        "activity": tick.enum("activity", envelope.AI_MAX_AGE_MS),
        "caption": _cut(ai.caption, CAPTION_MAX),
        "objects": [_cut(o, OBJECT_MAX) for o in ai.objects or []],
        "hot": _hot(tick),
    }


def _episode_label(ep: Episode) -> str:
    """Episode labels live in the db, not the model; fall back to its dominant tags."""

    label = getattr(ep, "label", None)
    if label:
        return _cut(label, OBJECT_MAX)
    return _cut(", ".join(f"{k} {v}" for k, v in ep.dominant.items()), OBJECT_MAX)


def state_size_ok(state: dict, budget_tokens: int = 3000) -> bool:
    """Rough token estimate: four characters of JSON per token."""

    return len(json.dumps(state)) / 4 <= budget_tokens


def build_state(
    esc: Escalation,
    window: Sequence[Tick],
    summary_lines: Sequence[str],
    open_episodes: Sequence[Episode],
    persona: str,
    seven_day: str,
    now: float,
    *,
    max_ticks: int = 6,
    max_summary: int = 12,
) -> dict:
    """The decider's JSON state. Never raises on size: over budget it drops the
    oldest ``today`` lines, then the oldest ``recent`` ticks, down to one of each."""

    local = datetime.fromtimestamp(now).astimezone()
    newest_first = sorted(window, key=lambda tk: tk.t, reverse=True)[:max(max_ticks, 0)]
    state: dict[str, Any] = {
        "trigger": {"name": esc.trigger, "reason": esc.reason},
        "recent": [_tick_entry(tk, now) for tk in newest_first],
        "episodes": [
            {
                "kind": ep.kind,
                "minutes_open": round((now - ep.start_t) / 60, 1),
                "label": _episode_label(ep),
            }
            for ep in open_episodes
        ],
        "today": [_cut(line, SUMMARY_MAX) for line in summary_lines][-max_summary:]
        if max_summary > 0 else [],
        "persona": _cut(persona, PERSONA_MAX),
        "trends": _cut(seven_day, TRENDS_MAX),
        "clock": {"local": local.strftime("%H:%M"), "weekday": _WEEKDAYS[local.weekday()]},
    }
    while not state_size_ok(state):
        if len(state["today"]) > 1:
            state["today"].pop(0)
        elif len(state["recent"]) > 1:
            state["recent"].pop()
        else:
            break
    return state
