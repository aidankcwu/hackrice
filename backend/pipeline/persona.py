"""The persona every prompt is briefed with: the wearer, then the operator persona.

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

#: Introduces the wearer paragraph, above the persona.
WEARER_HEADING = "About the wearer:"


def join_persona(persona: str, wearer: str | None) -> str:
    """The wearer's paragraph first, then the persona.

    The questionnaire paragraph is who the model is working for; the persona
    is how it behaves. Who comes first, so the behaviour text reads as
    instructions about that person. With no wearer paragraph the persona
    stands alone. The wearer slot holds one paragraph, so the next person's
    questionnaire replaces the previous one rather than stacking under it.
    """

    base = (persona or "").strip()
    extra = (wearer or "").strip()
    if not extra:
        return base
    head = f"{WEARER_HEADING}\n{extra}"
    return f"{head}\n\n{base}" if base else head


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
