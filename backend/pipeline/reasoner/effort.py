"""The lowest reasoning effort each model accepts, learned once per process.

Shared by every Responses API caller: the clerk (T1), the answer parser and the
voice agent all run on ``t1_model``. gpt-5.1 and later (the t1 model is
gpt-5.4-mini) speak ``none``/``low``/...; only the original gpt-5 family took
``minimal``. The old default was ``minimal``, and gpt-5.4-mini answered it with
a 400 on EVERY call -- 73 of 151 ``/v1/responses`` requests on 13 Sep were that
400 -- and the retry dropped ``reasoning`` altogether, so each line paid a
wasted round trip and then ran at the server's default effort, not the lowest.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "EFFORT_LADDER",
    "DEFAULT_EFFORT",
    "create_with_effort",
    "reset_effort_memory",
]

#: Lowest effort first.
EFFORT_LADDER: tuple[str, ...] = ("none", "minimal", "low")
DEFAULT_EFFORT = "none"

#: Model -> the effort it last accepted (``None`` = send no ``reasoning``).
#: Process-wide on purpose: a rejection is a fact about the model, not about
#: one client object, so it is paid for at most once per step of the ladder
#: per process rather than once per call -- and the clerk, the parser and the
#: voice agent learn it from each other.
_ACCEPTED_EFFORT: dict[str, str | None] = {}


def reset_effort_memory() -> None:
    """Forget every remembered effort (tests; a model swap mid-process)."""

    _ACCEPTED_EFFORT.clear()


def _next_effort(effort: str) -> str | None:
    """The next rung down the ladder, or ``None`` (omit the parameter)."""

    if effort in EFFORT_LADDER:
        rest = EFFORT_LADDER[EFFORT_LADDER.index(effort) + 1:]
        return rest[0] if rest else None
    # An effort outside the ladder (someone passed "medium"): fall to the
    # cheapest rung rather than straight to the server default.
    return EFFORT_LADDER[0]


def _is_effort_rejection(exc: Exception) -> bool:
    return "reasoning" in str(exc).lower()


def effort_for(model: str, requested: str | None) -> str | None:
    """What to send: the remembered rung for this model, else ``requested``.

    ``requested=None`` means the caller asked for no ``reasoning`` at all.
    """

    if requested and model in _ACCEPTED_EFFORT:
        return _ACCEPTED_EFFORT[model]
    return requested


async def create_with_effort(
    client: Any, model: str, requested: str | None, kwargs: dict[str, Any],
    *, label: str,
) -> tuple[Any, str | None]:
    """``client.responses.create(**kwargs)`` at the lowest accepted effort.

    Steps down the ladder on an effort rejection, remembers where it landed,
    and returns ``(response, effort used)``. Any other error is raised as is.
    """

    effort = effort_for(model, requested)
    while True:
        call = dict(kwargs)
        if effort:
            call["reasoning"] = {"effort": effort}
        try:
            response = await client.responses.create(**call)
            break
        except Exception as exc:  # noqa: BLE001
            if not effort or not _is_effort_rejection(exc):
                raise
            # "Unsupported value: 'reasoning.effort' ..." is about this rung,
            # so try the next; "Unsupported parameter: 'reasoning'" says the
            # model takes no reasoning at all, and walking the whole ladder
            # would pay three 400s to learn what one already said.
            fallback = _next_effort(effort) if "effort" in str(exc).lower() else None
            # WARNING, with the body: on demo night this was a silent retry,
            # and a 400 on every line went unnoticed for a week. Now it is
            # logged once per rung per process, because it is remembered.
            log.warning(
                "%s: %s rejected reasoning.effort=%r (%s); using %r for the "
                "rest of this process",
                label, model, effort, str(exc)[:300], fallback,
            )
            _ACCEPTED_EFFORT[model] = fallback
            effort = fallback
    if requested:
        _ACCEPTED_EFFORT.setdefault(model, effort)
    return response, effort
