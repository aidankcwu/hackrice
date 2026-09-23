"""Pydantic models for the tick stream and everything derived from it.

:class:`Tick` is the contract between the two halves of the system (SPEC §12).
Person A emits ticks; we consume them and touch nothing upstream. A may add
fields freely -- hence ``extra="allow"`` on the tick blocks -- and we must
tolerate any optional field being absent, in particular the whole ``ai`` block
(SPEC §12.2).
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Scene",
    "Activity",
    "FoodType",
    "Drink",
    "SCENES",
    "ACTIVITIES",
    "FOOD_TYPES",
    "DRINKS",
    "PeopleCount",
    "PEOPLE_COUNTS",
    "OUTDOOR_SCENES",
    "HOME_SCENES",
    "EXERTION_ACTIVITIES",
    "HEALTHY_FOOD_TYPES",
    "UNHEALTHY_FOOD_TYPES",
    "MEDICATION_HELD_WORDS",
    "EpisodeKind",
    "SensorBlock",
    "DeviceBlock",
    "AiBlock",
    "Tick",
    "Escalation",
    "Episode",
    "Decision",
    "Insight",
    "PendingCheck",
    "PendingQuestion",
    "Score",
    "SeededRow",
    "TodaySummaryLine",
    "Session",
    "phash_distance",
]

# The four ``ai`` enums mirror Person A's ``longevity.ai_fields`` exactly -- his
# module is the single source of truth, these Literals are the validating copy on
# this side of the seam. ``tests/test_models.py`` imports his lists and asserts the
# two agree member for member, so a menu change upstream fails here loudly rather
# than silently coercing a new value to ``unknown``.

Scene = Literal[
    "home", "office", "classroom", "library", "lab",
    "restaurant", "cafe", "bar", "gym", "store", "grocery_store",
    "hospital", "hotel",
    "park", "trail", "campus", "street", "parking_lot", "beach",
    "nature", "sports_venue", "construction_site",
    "car", "public_transit", "airport",
    "sauna", "cold_plunge",
    "indoor_other", "outdoor_other",
    "unknown",
]

Activity = Literal[
    "seated", "standing", "walking", "running", "cycling", "driving",
    "exercising", "lifting_weights", "stretching",
    "eating", "drinking", "cooking",
    "reading", "computer_use", "phone_use",
    "talking", "shopping", "cleaning",
    "lying_down", "sleeping",
    "personal_care", "commuting", "other",
    "unknown",
]

FoodType = Literal[
    "vegetables", "fruit", "grains", "beans_legumes", "fish", "seafood",
    "poultry", "red_meat", "eggs", "dairy", "nuts",
    "salad", "sandwich", "burger", "pizza", "pasta", "rice_bowl", "noodles",
    "soup", "wrap_taco", "breakfast",
    "processed", "fried_food", "fast_food", "snack", "chips", "candy",
    "baked_goods", "cereal", "protein_bar", "sweets", "dessert",
    "bread", "potatoes",
    "mixed", "none",
]

Drink = Literal[
    "none",
    "water",
    "coffee",
    "tea",
    "energy_drink",
    "soda",
    "alcohol",
    "juice",
    "smoothie",
    "milk",
    "sports_drink",
    "boba",
    "beer",
    "wine",
    "cocktail",
    "unknown",
]

#: Bucketed head count; mirrors ``ai_fields.PEOPLE_COUNT`` (the crowd cue).
PeopleCount = Literal["0", "1-2", "3-5", "6+", "unknown"]

#: The enum menus as tuples, for anything that needs to iterate rather than validate.
SCENES: tuple[str, ...] = get_args(Scene)
ACTIVITIES: tuple[str, ...] = get_args(Activity)
FOOD_TYPES: tuple[str, ...] = get_args(FoodType)
DRINKS: tuple[str, ...] = get_args(Drink)
PEOPLE_COUNTS: tuple[str, ...] = get_args(PeopleCount)

# -- named families ------------------------------------------------------
#
# Consumers switch on these enums by family, never by a single literal: the gate
# and the episode builder must agree on what "outdoors" means (SPEC §10 requires
# an episode and its escalation to share boundaries), and both must keep agreeing
# when one more outdoor scene joins the menu. Defined here, once, and mirrored
# from ``longevity.ai_fields``.

#: Scenes that count as being outside.
OUTDOOR_SCENES: frozenset[str] = frozenset({
    "park", "trail", "campus", "street", "parking_lot", "beach",
    "nature", "sports_venue", "construction_site", "outdoor_other",
})

#: Scenes that count as being at home -- every room of one.
HOME_SCENES: frozenset[str] = frozenset({"home"})

#: Activities that explain an elevated heart rate on their own (SPEC §14.3).
EXERTION_ACTIVITIES: frozenset[str] = frozenset({
    "exercising",
    "walking",
    "running",
    "lifting_weights",
    "cycling",
    "stretching",
})

#: ``food_type`` values counted as on-pattern for PREDIMED-style diet scoring.
HEALTHY_FOOD_TYPES: frozenset[str] = frozenset({
    "vegetables", "fruit", "grains", "beans_legumes", "fish", "seafood",
    "poultry", "salad", "rice_bowl", "soup", "eggs", "nuts", "mixed",
})

#: Explicitly off-pattern. Everything in neither set (``sandwich``, ``pasta``,
#: ``red_meat``, ``dairy``, ``cereal``, ``noodles``, ``protein_bar``, ``none``)
#: is neutral and counts towards neither share.
UNHEALTHY_FOOD_TYPES: frozenset[str] = frozenset({
    "processed", "fried_food", "sweets", "chips", "candy", "baked_goods",
    "fast_food", "dessert", "burger", "pizza",
})

#: Words in ``in_hand`` that make the held thing a dose (PLAN 2.2). Whole words,
#: a plural ``s`` allowed, so "pill bottle" and "insulin pens" count and
#: "pencil" does not. The ``medication_seen`` trigger and the
#: ``medication_sighting`` episode both read them through
#: :meth:`Tick.medication_in_view`.
MEDICATION_HELD_WORDS: frozenset[str] = frozenset({
    "vial", "pen", "syringe", "injector", "pill", "capsule", "tablet", "bottle",
})

_HELD_WORD = re.compile(r"[a-z]+")


def _names_medication(text: str) -> bool:
    return any(
        word in MEDICATION_HELD_WORDS
        or (word.endswith("s") and word[:-1] in MEDICATION_HELD_WORDS)
        for word in _HELD_WORD.findall(text.casefold())
    )

EpisodeKind = Literal[
    "meal",
    "food_sighting",
    "conversation",
    "outdoor_block",
    "screen_block",
    "gym_session",
    "sauna_session",
    # Point observations (short episodes), not sustained states.
    "caffeine_sighting",
    "alcohol_sighting",
    "medication_sighting",
]

_TICK_BLOCK_CONFIG = ConfigDict(extra="allow")


class SensorBlock(BaseModel):
    """Pixel statistics computed laptop-side. Always present (SPEC §12.1)."""

    model_config = _TICK_BLOCK_CONFIG

    #: Relative luminance from an auto-exposed JPEG -- NOT absolute lux.
    lux_proxy: float | None = None
    cct: float | None = None
    hist_spread: float | None = None
    frame_delta: float | None = None
    flow_mag: float | None = None
    sharpness: float | None = None
    #: 16 hex chars = 64-bit perceptual hash.
    phash: str | None = Field(
        default=None, min_length=16, max_length=16, pattern=r"^[0-9a-fA-F]{16}$"
    )


class DeviceBlock(BaseModel):
    """Phone sensors. Absent under the ``webcam`` / ``replay`` adapters."""

    model_config = _TICK_BLOCK_CONFIG

    accel_rms: float | None = None
    gps_speed: float | None = None


class AiBlock(BaseModel):
    """Small-VLM output (SPEC §9). May be absent entirely (SPEC §12.2)."""

    model_config = _TICK_BLOCK_CONFIG

    as_of: float | None = None
    age_ms: int | None = None
    scene: Scene = "unknown"
    activity: Activity = "unknown"
    #: Booleans are tri-state: ``None`` means the VLM did not report it.
    #: A missing flag is *unknown*, never a negative observation.
    food_present: bool | None = None
    food_type: FoodType | None = None
    caffeine_visible: bool | None = None
    alcohol_visible: bool | None = None
    screen_present: bool | None = None
    vegetation_visible: bool | None = None
    people_present: bool | None = None
    #: Booleans are tri-state: ``None`` means the VLM did not report it.
    people_interacting: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    direct_sunlight_visible: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    outdoor_visible: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    smoking_or_vaping_visible: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    medication_visible: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    caption: str | None = Field(default=None, exclude_if=lambda value: value is None)
    objects: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    drink: Drink | None = Field(default=None, exclude_if=lambda value: value is None)
    conf: float | None = None
    #: What the wearer is holding, as a short lowercase noun phrase (brand or
    #: product when legible). ``None`` = empty hands, hands out of view, or an
    #: older producer that does not report it -- never "nothing is held".
    in_hand: str | None = Field(default=None, exclude_if=lambda value: value is None)
    #: Tri-state like the other flags: ``None`` means the hands were not visible.
    phone_in_hand: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    people_count: PeopleCount | None = Field(default=None, exclude_if=lambda value: value is None)


class Tick(BaseModel):
    """One second of ground truth (SPEC §12)."""

    model_config = _TICK_BLOCK_CONFIG

    v: int = 1
    tick_id: str
    t: float
    seq: int
    sensor: SensorBlock
    device: DeviceBlock | None = None
    ai: AiBlock | None = None
    #: Opaque handle into the frame ring buffer, valid 90 s (SPEC §12.3).
    frame_ref: str

    # -- tri-state helpers for consumers (gate, episodes, reasoner) ---------

    def ai_fresh(self, max_age_ms: int = 3000) -> bool:
        """True when an ``ai`` block is present and not older than ``max_age_ms``.

        A block with no ``age_ms`` is treated as fresh (it was produced for
        this tick). Stale AI is *unknown*, not evidence.
        """

        if self.ai is None:
            return False
        if self.ai.age_ms is None:
            return True
        return self.ai.age_ms <= max_age_ms

    def flag(self, name: str, max_age_ms: int = 3000) -> bool | None:
        """Tri-state read of an ``ai`` boolean: True / False / None (unknown).

        Returns ``None`` when the block is absent, stale, or the field was not
        reported. Consumers must never coerce ``None`` to ``False``.
        """

        if not self.ai_fresh(max_age_ms):
            return None
        value = getattr(self.ai, name, None)
        return value if isinstance(value, bool) else None

    def enum(self, name: str, max_age_ms: int = 3000) -> str | None:
        """Tri-state read of an ``ai`` enum (``scene``, ``activity``, ``food_type``,
        ``people_count``)."""

        if not self.ai_fresh(max_age_ms):
            return None
        value = getattr(self.ai, name, None)
        if value in (None, "unknown"):
            return None
        return value

    def held(self, max_age_ms: int = 3000) -> str | None:
        """Tri-state read of ``in_hand``: the held item, or ``None`` (unknown).

        ``None`` covers absent/stale ai *and* empty or unseen hands -- a consumer
        must not read it as "the wearer put the item down" on one tick alone.
        """

        if not self.ai_fresh(max_age_ms):
            return None
        value = getattr(self.ai, "in_hand", None)
        return value if isinstance(value, str) and value else None

    def medication_in_view(self, max_age_ms: int = 3000) -> bool | None:
        """Tri-state: is a dose in view on this tick?

        True when ``medication_visible`` is, or when ``in_hand`` names one of
        :data:`MEDICATION_HELD_WORDS`. False when the flag says no or the hands
        hold something else; ``None`` when neither was reported. The gate's
        ``medication_seen`` and the ``medication_sighting`` episode both read
        this, so a sighting and its escalation agree (SPEC §10).
        """

        visible = self.flag("medication_visible", max_age_ms)
        held = self.held(max_age_ms)
        if visible is True or (held is not None and _names_medication(held)):
            return True
        if visible is False or held is not None:
            return False
        return None


class Escalation(BaseModel):
    """What the trigger gate hands to the reasoner (SPEC §3, §4).

    The gate never awaits the reasoner. ``Reasoner.try_escalate(esc) -> bool``
    atomically claims the single T1 slot; ``False`` means dropped on
    contention (SPEC §5.4) and the reasoner has already logged a dropped
    :class:`Decision`.
    """

    trigger: str
    t: float
    tick: Tick
    #: Recent tick history, oldest first, ending with ``tick`` (~60 s).
    window: list[Tick] = Field(default_factory=list)
    episode_id: str | None = None
    reason: str = ""
    #: Extra context lines the gate attaches when the frames alone cannot
    #: explain the trigger -- the wearable HR series behind a
    #: ``biometric_anomaly`` (SPEC §14.3). Rendered into the envelope right
    #: after the tick table and before the frames.
    extra_text: list[str] = Field(default_factory=list)
    #: The persona cue this moment carries, as a stable key (``food:treat``,
    #: ``food:healthy``, ``drink``, ``caffeine``, ``phone``, ``crowd``) -- set by the gate
    #: on the ``cue`` trigger and on any other escalation whose tick shows one.
    #: The voice agent keys "say it once" on it, so a clerk wake-up about the
    #: same rice krispy treat cannot open a second conversation.
    cue: str | None = None
    #: The thing itself, in T0's words (``rice krispies treat``): the agent
    #: tells a second treat from the same treat relabelled by comparing these.
    cue_item: str = ""
    #: Set by the gate's stamp when ``cue``/``cue_item`` is a moment the voice
    #: agent already spoke and that is still live (the prop is still in the
    #: hand). The agent then refuses a hand-off naming that item for as long as
    #: the moment lasts, not only inside its own repeat window.
    cue_spent: bool = False
    #: What the voice agent is handed for a ``cue`` escalation, written by code
    #: from the tick (held item, caption, objects) rather than by the clerk.
    cue_topic: str = ""
    cue_mode: Literal["statement", "question"] = "statement"
    #: Conversation id when the gate handed this moment straight to the voice
    #: agent (the fast path). The clerk still runs on it for the memory line and
    #: the episode label, but its own ``speak``/``ask`` are dropped with outcome
    #: ``fast_pathed``: the moment is already being spoken.
    handed_off: str | None = None


class Episode(BaseModel):
    """A run of ticks with a stable tag, collapsed into one row (SPEC §10)."""

    id: str
    kind: EpisodeKind
    start_t: float
    end_t: float | None = None
    duration_s: float = 0.0
    #: Dominant tag values over the run, e.g. ``{"scene": "park"}``.
    dominant: dict[str, Any] = Field(default_factory=dict)
    tick_count: int = 0
    open: bool = True


class Decision(BaseModel):
    """One T1 escalation and its outcome -- including the silent ones (SPEC §6)."""

    id: str
    t: float
    trigger: str
    trigger_tick_id: str
    episode_id: str | None = None
    interpretation: str = ""
    confidence: float = 0.0
    #: Selected actions, e.g. ``[{"type": "annotate", "text": "..."}]`` (SPEC §4.4).
    actions: list[dict[str, Any]] = Field(default_factory=list)
    spoke: bool = False
    #: True when the escalation was dropped on contention (SPEC §5.4).
    dropped: bool = False
    drop_reason: str | None = None
    latency_ms: int | None = None
    model: str = ""


class Insight(BaseModel):
    """Persistent record feeding daily and weekly reports (SPEC §4.4)."""

    id: str
    t: float
    category: str
    text: str
    decision_id: str | None = None


class PendingCheck(BaseModel):
    """A ``watch`` row the trigger gate polls (SPEC §4.4)."""

    id: str
    created_t: float
    due_t: float | None = None
    condition: str | None = None
    reason: str = ""
    decision_id: str | None = None
    fired: bool = False


class PendingQuestion(BaseModel):
    """One question asked of the wearer and whatever came back (ASK_DESIGN §5).

    The answer lives here and nowhere else. :class:`~pipeline.episodes.builder.
    EpisodeBuilder` recomputes ``Episode.dominant`` on every tick, so a field
    patched onto the episode would be erased within seconds -- and it would
    also conflate what the camera saw with what the wearer said. The API
    projects these rows onto episodes as ``reported`` (ASK_DESIGN §8.3).
    """

    id: str
    created_t: float
    #: ``sent_t + ask_expire_s``. ``None`` until the ask actually goes out --
    #: a row that was never sent cannot have run out of time (ASK_DESIGN §8.4).
    expires_t: float | None = None
    decision_id: str | None = None
    episode_id: str | None = None
    question: str
    answer_kind: str = "yes_no"
    fills: str = "confirmed"
    status: Literal["open", "answered", "expired", "suppressed"] = "open"
    answer_text: str | None = None
    answer_t: float | None = None
    #: ``AnswerParse.model_dump()``; ``{}`` until the parser finishes.
    parsed: dict[str, Any] = Field(default_factory=dict)
    #: The question this one follows up on. At most one level (§8.5).
    followup_of: str | None = None
    #: Mac clock when the ask message went out; ``None`` while sending.
    sent_t: float | None = None
    #: Why the ask never happened, for a ``suppressed`` row (§8.6).
    suppressed_reason: str | None = None
    #: Whether the phone heard anything at all in the answer window.
    heard: bool | None = None
    #: The conversation that asked this, when the voice agent owns the
    #: exchange (docs/CONVERSATION_DESIGN.md §5). ``None`` for the clerk's own
    #: questions and for anything asked by hand through ``/api/ask``. When it
    #: is set, the answer is routed to the voice agent instead of the answer
    #: parser -- the agent is holding the thread and will settle it itself.
    conversation_id: str | None = None


class Score(BaseModel):
    """One metric scored against a §8 threshold."""

    metric: str
    layer: str
    period: Literal["daily", "weekly"]
    #: ``YYYY-MM-DD`` for daily, ISO week ``YYYY-Www`` for weekly.
    period_key: str
    value: float | None = None
    target: str = ""
    #: Normalised 0..1.
    score: float = 0.0
    source: Literal["live", "seeded"] = "live"
    #: Evidence grade from SPEC §8 (A strongest).
    grade: str = ""
    note: str | None = None


class SeededRow(BaseModel):
    """A synthetic wearable/phone integration row (SPEC §7)."""

    day: str  # YYYY-MM-DD
    metric: str
    value: float
    unit: str = ""
    source: str = "whoop"


class TodaySummaryLine(BaseModel):
    """One ``annotate`` line, accumulating into part 4 of the envelope (§4.2)."""

    t: float
    line: str
    decision_id: str | None = None


class Session(BaseModel):
    """A named judging window on the pipeline's tick clock."""

    id: str
    name: str = ""
    started_t: float
    ended_t: float | None = None

    def duration_s(self, now: float | None = None) -> float:
        """Length on the caller's clock: pass the tick clock under ``--speed N``."""
        end = self.ended_t if self.ended_t is not None else (
            now if now is not None else time.time())
        return max(0.0, end - self.started_t)


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two hex perceptual hashes.

    Used for frame subsampling by change rather than by even time spacing
    (SPEC §4.3), computed from ``sensor.phash`` in the tick history (SPEC §12.3).
    """

    if len(a) != len(b):
        raise ValueError(f"phash length mismatch: {len(a)} != {len(b)}")
    return bin(int(a, 16) ^ int(b, 16)).count("1")
