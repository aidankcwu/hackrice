"""Pydantic models for the tick stream and everything derived from it.

:class:`Tick` is the contract between the two halves of the system (SPEC §12).
Person A emits ticks; we consume them and touch nothing upstream. A may add
fields freely -- hence ``extra="allow"`` on the tick blocks -- and we must
tolerate any optional field being absent, in particular the whole ``ai`` block
(SPEC §12.2).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Scene",
    "Activity",
    "FoodType",
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
    "Score",
    "SeededRow",
    "TodaySummaryLine",
    "phash_distance",
]

Scene = Literal[
    "home",
    "office",
    "restaurant",
    "gym",
    "sauna",
    "cold_plunge",
    "park",
    "trail",
    "vehicle",
    "street",
    "unknown",
]

Activity = Literal[
    "seated",
    "standing",
    "walking",
    "exercising",
    "eating",
    "unknown",
]

FoodType = Literal[
    "vegetables",
    "fruit",
    "grains",
    "fish",
    "poultry",
    "red_meat",
    "processed",
    "sweets",
    "mixed",
    "none",
]

EpisodeKind = Literal[
    "meal",
    "conversation",
    "outdoor_block",
    "screen_block",
    "gym_session",
    "sauna_session",
    # Point observations (short episodes), not sustained states.
    "caffeine_sighting",
    "alcohol_sighting",
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
    conf: float | None = None


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
        """Tri-state read of an ``ai`` enum (``scene``, ``activity``, ``food_type``)."""

        if not self.ai_fresh(max_age_ms):
            return None
        value = getattr(self.ai, name, None)
        if value in (None, "unknown"):
            return None
        return value


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


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two hex perceptual hashes.

    Used for frame subsampling by change rather than by even time spacing
    (SPEC §4.3), computed from ``sensor.phash`` in the tick history (SPEC §12.3).
    """

    if len(a) != len(b):
        raise ValueError(f"phash length mismatch: {len(a)} != {len(b)}")
    return bin(int(a, 16) ^ int(b, 16)).count("1")
