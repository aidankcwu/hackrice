"""Session recap: moments, session-window scores, and a spoken narrative.

A judge wears the glasses for two minutes; the pipeline tags, escalates, saves
evidence frames and builds episodes as usual. Then someone taps "generate
recap" and this package turns that window into one object the dashboard can
render whole -- a longevity score with subscores, the key moments with the
actual photo of each, and a short narrative that is also read aloud through the
glasses.

Nothing here is on the hot path. Every module is read-only over
:class:`~pipeline.db.Database` except :mod:`pipeline.recap.builder`, which owns
its own ``recaps`` table (created the way
:mod:`pipeline.reasoner.evidence` creates ``escalated_frames``).
"""

from __future__ import annotations

from .builder import RECAP_SCHEMA, RecapStore, build_recap
from .moments import (
    BLUR_MEDIAN_FRACTION,
    MIN_SHARPNESS,
    TRIGGER_CATEGORIES,
    Moment,
    category_for,
    select_moments,
    severity_for,
)
from .narrative import (
    FakeNarrativeClient,
    NarrativeClient,
    OpenAINarrativeClient,
    RecapContext,
    RecapNarrative,
    make_narrative_client,
)
from .routes import router

__all__ = [
    "BLUR_MEDIAN_FRACTION",
    "FakeNarrativeClient",
    "MIN_SHARPNESS",
    "Moment",
    "NarrativeClient",
    "OpenAINarrativeClient",
    "RECAP_SCHEMA",
    "RecapContext",
    "RecapNarrative",
    "RecapStore",
    "TRIGGER_CATEGORIES",
    "build_recap",
    "category_for",
    "make_narrative_client",
    "router",
    "select_moments",
    "severity_for",
]
