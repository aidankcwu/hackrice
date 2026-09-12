"""Scripted scenarios for the synthetic tick source.

A scenario is an ordered list of segments, each a stretch of wall-clock time
with fixed VLM tags. :data:`DEFAULT_SCENARIO` follows the shape SPEC §13.4 asks
the replay corpus to have -- seated, food appears, go outdoors, sit at a screen --
plus the caffeine and alcohol sightings the demo metric set needs (SPEC §10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from ..models import Activity, FoodType, Scene

__all__ = ["Segment", "Scenario", "DEFAULT_SCENARIO"]


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch of the script."""

    name: str
    duration_s: float
    scene: Scene
    activity: Activity
    #: The VLM booleans plus ``food_type``; anything omitted defaults false/none.
    flags: dict[str, object] = field(default_factory=dict)
    #: RGB for the placeholder JPEG.
    bg_color: tuple[int, int, int] = (40, 40, 48)
    #: 0.0 still .. 1.0 vigorous; drives frame_delta, flow_mag and accel_rms.
    motion_level: float = 0.05

    # -- derived tag helpers ---------------------------------------------

    @property
    def food_present(self) -> bool:
        return bool(self.flags.get("food_present", False))

    @property
    def food_type(self) -> FoodType:
        return self.flags.get("food_type", "none")  # type: ignore[return-value]

    @property
    def caffeine_visible(self) -> bool:
        return bool(self.flags.get("caffeine_visible", False))

    @property
    def alcohol_visible(self) -> bool:
        return bool(self.flags.get("alcohol_visible", False))

    @property
    def screen_present(self) -> bool:
        return bool(self.flags.get("screen_present", False))

    @property
    def vegetation_visible(self) -> bool:
        return bool(self.flags.get("vegetation_visible", False))

    @property
    def people_present(self) -> bool:
        return bool(self.flags.get("people_present", False))

    @property
    def outdoor(self) -> bool:
        return self.scene in ("park", "trail", "street")


@dataclass(frozen=True, slots=True)
class Scenario:
    """An ordered script of segments."""

    name: str
    segments: list[Segment]

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments)

    def segment_at(self, elapsed_s: float) -> tuple[int, Segment, float]:
        """Return ``(index, segment, offset_into_segment)`` for elapsed script time.

        The script loops, so a long run keeps producing plausible input.
        """

        total = self.total_duration_s
        if total <= 0:
            raise ValueError("scenario has zero duration")
        pos = elapsed_s % total
        for i, seg in enumerate(self.segments):
            if pos < seg.duration_s:
                return i, seg, pos
            pos -= seg.duration_s
        # Floating point fall-through.
        return len(self.segments) - 1, self.segments[-1], 0.0

    def __iter__(self) -> Iterator[Segment]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)


DEFAULT_SCENARIO = Scenario(
    name="default_6min",
    segments=[
        Segment(
            name="office_screen",
            duration_s=60,
            scene="office",
            activity="seated",
            flags={"screen_present": True},
            bg_color=(48, 54, 72),
            motion_level=0.04,
        ),
        Segment(
            name="desk_coffee",
            duration_s=30,
            scene="office",
            activity="seated",
            flags={"screen_present": True, "caffeine_visible": True},
            bg_color=(72, 58, 46),
            motion_level=0.08,
        ),
        Segment(
            name="office_screen_2",
            duration_s=45,
            scene="office",
            activity="seated",
            flags={"screen_present": True},
            bg_color=(48, 54, 72),
            motion_level=0.04,
        ),
        Segment(
            name="lunch_restaurant",
            duration_s=60,
            scene="restaurant",
            activity="eating",
            flags={
                "food_present": True,
                "food_type": "mixed",
                "people_present": True,
            },
            bg_color=(120, 92, 64),
            motion_level=0.18,
        ),
        Segment(
            name="walk_street",
            duration_s=30,
            scene="street",
            activity="walking",
            flags={"people_present": True},
            bg_color=(130, 136, 142),
            motion_level=0.75,
        ),
        Segment(
            name="park",
            duration_s=60,
            scene="park",
            activity="walking",
            flags={"vegetation_visible": True},
            bg_color=(84, 140, 74),
            motion_level=0.6,
        ),
        Segment(
            name="home_screen",
            duration_s=45,
            scene="home",
            activity="seated",
            flags={"screen_present": True},
            bg_color=(58, 48, 56),
            motion_level=0.05,
        ),
        Segment(
            name="home_wine",
            duration_s=30,
            scene="home",
            activity="seated",
            flags={"screen_present": True, "alcohol_visible": True},
            bg_color=(88, 40, 52),
            motion_level=0.07,
        ),
        Segment(
            name="home_screen_2",
            duration_s=30,
            scene="home",
            activity="seated",
            flags={"screen_present": True},
            bg_color=(58, 48, 56),
            motion_level=0.05,
        ),
    ],
)
