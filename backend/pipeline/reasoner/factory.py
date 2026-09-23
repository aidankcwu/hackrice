"""The single place the decider is switched on (docs/PERCEPTION.md, "Decider and writers").

``DECIDER`` (see :class:`DeciderSettings`) picks between Jev and the clerk;
this module turns that choice into the three keyword arguments
:class:`~pipeline.reasoner.reasoner.Reasoner` takes. Wiring calls
:func:`build_reasoner_extras` once and passes ``**extras.as_kwargs()``;
nothing else constructs a decider or writers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .decider import Decider, make_decider
from .decider_settings import DeciderSettings
from .writers import Writers, make_writers

__all__ = ["ReasonerExtras", "build_reasoner_extras"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReasonerExtras:
    """What the Reasoner needs for the decider path. ``decider=None`` is the clerk."""

    decider: Decider | None
    writers: Writers | None
    settings: DeciderSettings

    def as_kwargs(self) -> dict[str, Any]:
        return {
            "decider": self.decider,
            "writers": self.writers,
            "decider_settings": self.settings,
        }


def build_reasoner_extras(
    settings: DeciderSettings | None = None, reasoner_client: Any = None
) -> ReasonerExtras:
    """Settings in, decider + writers out. No decider means no writers."""

    settings = settings if settings is not None else DeciderSettings()
    decider = make_decider(settings)
    writers = None
    if decider is not None:
        writers = make_writers(settings, reasoner_client)
        if writers is None:
            log.info("DECIDER=jev on the fake reasoner client: writers are off, "
                     "only annotate will fire")
    return ReasonerExtras(decider=decider, writers=writers, settings=settings)
