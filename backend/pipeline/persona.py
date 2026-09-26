"""The persona every prompt is briefed with: operator persona, then the wearer.

Two slots in the ``profile`` table (:class:`pipeline.db.Database`):

* the **persona** -- how Bryan behaves. The built-in text from
  ``reasoner.prompts``, or ``PERSONA_FILE`` / the dashboard's editor when set.
* the **wearer profile** -- one short paragraph about the person, written by
  the phone's first-launch questionnaire (``PUT /api/persona/wearer``).

They are joined here, and only here, so the questionnaire can never erase the
behaviour instructions (which it did when both wrote the same slot).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

__all__ = ["WEARER_HEADING", "effective_persona", "join_persona"]

#: Introduces the wearer paragraph under the persona.
WEARER_HEADING = "About the wearer:"


def join_persona(persona: str, wearer: str | None) -> str:
    """``persona`` alone, or ``persona`` + a blank line + heading + ``wearer``."""

    base = (persona or "").strip()
    extra = (wearer or "").strip()
    if not extra:
        return base
    return f"{base}\n\n{WEARER_HEADING}\n{extra}" if base else f"{WEARER_HEADING}\n{extra}"


def effective_persona(db, base: str) -> str:
    """What a model is briefed with right now.

    The operator's override (dashboard, ``PERSONA_FILE``) when there is one, else
    ``base``; then the wearer paragraph when there is one. A database error falls
    back to ``base`` alone rather than failing the prompt.
    """

    try:
        persona = db.get_persona() or base
        wearer = db.get_wearer_profile()
    except Exception:  # pragma: no cover - defensive
        log.exception("could not read the persona; using the built-in one")
        return base
    return join_persona(persona, wearer)
