"""The key moments of a session, each with the photo it was decided from.

One moment per non-dropped decision in the window. The decision already carries
the interpretation and the trigger; what this module adds is (a) the *best* of
the four evidence frames SPEC §2.5 saved for it, (b) a category and a severity
the dashboard can colour by, and (c) the insight the decision logged, if any.

Frame choice is by ``sensor.sharpness`` -- the variance-of-Laplacian focus
measure in the tick's sensor block (SPEC §12.1). It is unitless and only
comparable *within* one pipeline run, which is exactly why the blur test is
relative: a frame is blurry when it is far below the median sharpness of the
window it came from, not when it falls under some absolute number that means
different things to the simulator and to the glasses.
"""

from __future__ import annotations

import logging
from datetime import datetime
from statistics import median
from typing import Any, Iterable, Protocol

from pydantic import BaseModel

from ..db import Database, day_key
from ..models import HEALTHY_FOOD_TYPES, Decision, Episode

log = logging.getLogger(__name__)

__all__ = [
    "BLUR_MEDIAN_FRACTION",
    "CAFFEINE_FLAG_HOUR",
    "MAX_MOMENTS",
    "MIN_SHARPNESS",
    "OFF_PATTERN_FOOD_TYPES",
    "TICK_LOOKBACK_S",
    "TRIGGER_CATEGORIES",
    "Moment",
    "blur_threshold",
    "cap_moments",
    "category_for",
    "select_moments",
    "severity_for",
]

#: Absolute floor for "this frame is out of focus". Variance of the Laplacian at
#: a fixed 256 px working resolution (``src/longevity/sensors.py``): near zero is
#: blurred or a blank wall, hundreds is a crisp textured scene. The simulator's
#: placeholder JPEGs land in the 55-95 band, so this floor alone never flags them.
MIN_SHARPNESS = 25.0

#: ...and the relative rule that actually does the work. A frame below this
#: fraction of the window's median sharpness is blurry *for this run*, whatever
#: absolute scale the adapter happens to produce.
BLUR_MEDIAN_FRACTION = 0.40

#: Ticks are read from a little before the window so an evidence frame captured
#: just ahead of its decision still finds its sharpness.
TICK_LOOKBACK_S = 120.0

#: Most moments one recap shows. The strip is scannable at a couple of dozen and
#: a wall of thumbnails at a hundred; it is also what the narrative model reads,
#: and a four-hour window can escalate far past what fits in either.
MAX_MOMENTS = 24

#: Local hour at or after which a caffeine sighting is a flag rather than a
#: neutral observation -- the SPEC §8 cutoff of bedtime (23:00) minus 9 h.
CAFFEINE_FLAG_HOUR = 14

#: ``trigger`` name -> the dashboard's category. ``watch:*`` follow-ups keep
#: their own bucket; anything unrecognised is ``other`` rather than a guess.
TRIGGER_CATEGORIES: dict[str, str] = {
    "food_in_frame": "diet",
    "caffeine_seen": "caffeine",
    "alcohol_seen": "alcohol",
    "screen_sustained": "screen",
    "people_sustained": "social",
    "outdoor_sustained": "nature",
    "biometric_anomaly": "stress",
}

#: Off-pattern foods. ``pipeline.models`` owns the list; this is the fallback
#: for a build where that family has not landed yet (SPEC §9 keeps moving).
OFF_PATTERN_FOOD_TYPES: frozenset[str] = frozenset({
    "red_meat", "processed", "sweets", "chips", "candy", "fast_food",
    "dessert", "baked_goods",
})

try:  # pragma: no cover - exercised by whichever branch this build has
    from ..models import UNHEALTHY_FOOD_TYPES as _UNHEALTHY
except ImportError:  # pragma: no cover
    _UNHEALTHY = OFF_PATTERN_FOOD_TYPES


class EvidenceLister(Protocol):
    """Just the slice of :class:`~pipeline.reasoner.evidence.EvidenceStore` used here."""

    def list(self, decision_id: str) -> list[dict[str, Any]]:
        ...


class Moment(BaseModel):
    """One decision, rendered for the recap strip."""

    decision_id: str
    t: float
    trigger: str
    category: str
    severity: str
    caption: str
    frame_ref: str
    #: Served by the existing ``GET /api/evidence/{decision_id}/{ref}`` route.
    frame_url: str
    sharpness: float | None = None
    blurry: bool = False
    insight: str | None = None


# -- classification -------------------------------------------------------


def category_for(trigger: str) -> str:
    """The dashboard category for a trigger name."""

    name = (trigger or "").strip()
    if name.startswith("watch:"):
        return "followup"
    return TRIGGER_CATEGORIES.get(name, "other")


def _hour_of_day(t: float) -> int:
    return datetime.fromtimestamp(t).astimezone().hour


def severity_for(
    category: str, t: float, food_type: str | None = None
) -> str:
    """``good`` | ``neutral`` | ``flag`` for one moment.

    Good is for the things worth repeating: an on-pattern meal, time outside,
    time with people. Flag is for alcohol (SPEC §8: no safe level), off-pattern
    food, a stress reading, and caffeine past the cutoff. Everything else --
    including a meal whose ``food_type`` the VLM never resolved -- is neutral,
    because "we could not tell" is not a verdict.
    """

    if category == "alcohol":
        return "flag"
    if category == "stress":
        return "flag"
    if category == "caffeine":
        return "flag" if _hour_of_day(t) >= CAFFEINE_FLAG_HOUR else "neutral"
    if category == "diet":
        if food_type in HEALTHY_FOOD_TYPES:
            return "good"
        if food_type in _UNHEALTHY or food_type in OFF_PATTERN_FOOD_TYPES:
            return "flag"
        return "neutral"
    if category in ("nature", "social"):
        return "good"
    return "neutral"


def _humanise(trigger: str) -> str:
    name = (trigger or "").split(":", 1)[-1] if trigger.startswith("watch:") else trigger
    text = (name or "moment").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Moment"


# -- window reads ---------------------------------------------------------


def blur_threshold(sharpnesses: Iterable[float]) -> float:
    """The sharpness below which a frame counts as blurry, for this window."""

    values = [float(s) for s in sharpnesses if s is not None]
    if not values:
        return MIN_SHARPNESS
    return max(MIN_SHARPNESS, median(values) * BLUR_MEDIAN_FRACTION)


def _sharpness_by_frame(db: Database, from_t: float, to_t: float
                        ) -> tuple[dict[str, float], float]:
    """``{frame_ref: sharpness}`` over the window, plus the blur threshold."""

    ticks = db.ticks_between(from_t - TICK_LOOKBACK_S, to_t)
    by_ref: dict[str, float] = {}
    for tick in ticks:
        value = getattr(tick.sensor, "sharpness", None)
        if value is not None:
            by_ref[tick.frame_ref] = float(value)
    return by_ref, blur_threshold(by_ref.values())


def _episodes_by_id(db: Database, from_t: float, to_t: float) -> dict[str, Episode]:
    days = {day_key(from_t), day_key(to_t)}
    return {e.id: e for day in sorted(days) for e in db.list_episodes(day)}


def _insight_by_decision(db: Database, from_t: float, to_t: float) -> dict[str, str]:
    """The *first* insight logged against each decision, oldest wins."""

    out: dict[str, str] = {}
    for insight in db.insights_between(from_t, to_t):
        if insight.decision_id and insight.decision_id not in out:
            out[insight.decision_id] = insight.text
    return out


def _food_type(decision: Decision, episodes: dict[str, Episode]) -> str | None:
    episode = episodes.get(decision.episode_id or "")
    if episode is not None:
        food = episode.dominant.get("food_type")
        if isinstance(food, str) and food not in ("", "none"):
            return food
    # Fallback: the interpretation names the meal ("meal, rice bowl") when the
    # episode builder has not closed the run yet.
    text = (decision.interpretation or "").lower()
    for candidate in sorted(HEALTHY_FOOD_TYPES | _UNHEALTHY | OFF_PATTERN_FOOD_TYPES,
                            key=len, reverse=True):
        if candidate.replace("_", " ") in text:
            return candidate
    return None


# -- the selection --------------------------------------------------------


def cap_moments(moments: list[Moment]) -> list[Moment]:
    """At most :data:`MAX_MOMENTS`, chronological, ``neutral`` dropped first.

    What survives is what a reader would keep: the flags and the things worth
    repeating. Only if those alone still overflow does the cap bite into them,
    and then it keeps the most recent -- the end of a session is the part a
    recap is about.
    """

    if len(moments) <= MAX_MOMENTS:
        return moments

    over = len(moments) - MAX_MOMENTS
    dropped: set[str] = set()
    for moment in moments:  # chronological, so the oldest neutrals go first
        if over <= 0:
            break
        if moment.severity == "neutral":
            dropped.add(moment.decision_id)
            over -= 1
    kept = [m for m in moments if m.decision_id not in dropped][-MAX_MOMENTS:]
    log.info(
        "recap: %d moments over the cap of %d; dropped %d (%d neutral)",
        len(moments), MAX_MOMENTS, len(moments) - len(kept), len(dropped),
    )
    return kept


def select_moments(
    db: Database, evidence: EvidenceLister, from_t: float, to_t: float
) -> list[Moment]:
    """Every non-dropped decision in ``[from_t, to_t]`` that saved a frame.

    A decision with no evidence frame is skipped rather than shown without a
    photo: the whole point of the strip is that each moment is backed by the
    pixels the decision was actually made from. The result is capped at
    :data:`MAX_MOMENTS` by :func:`cap_moments`.
    """

    sharpness_by_ref, threshold = _sharpness_by_frame(db, from_t, to_t)
    episodes = _episodes_by_id(db, from_t, to_t)
    insights = _insight_by_decision(db, from_t, to_t)

    moments: list[Moment] = []
    for decision in db.decisions_between(from_t, to_t):
        if decision.dropped:
            continue
        frames = evidence.list(decision.id)
        if not frames:
            continue
        best = max(
            frames,
            key=lambda f: (sharpness_by_ref.get(f["frame_ref"], -1.0), f.get("t", 0.0)),
        )
        ref = best["frame_ref"]
        sharpness = sharpness_by_ref.get(ref)
        category = category_for(decision.trigger)
        moments.append(Moment(
            decision_id=decision.id,
            t=decision.t,
            trigger=decision.trigger,
            category=category,
            severity=severity_for(category, decision.t, _food_type(decision, episodes)),
            caption=(decision.interpretation or "").strip() or _humanise(decision.trigger),
            frame_ref=ref,
            frame_url=f"/api/evidence/{decision.id}/{ref}",
            sharpness=sharpness,
            blurry=sharpness is not None and sharpness < threshold,
            insight=insights.get(decision.id),
        ))
    moments.sort(key=lambda m: m.t)
    return cap_moments(moments)
