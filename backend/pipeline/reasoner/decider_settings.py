"""Every knob for the decider and its writers (docs/PERCEPTION.md, "Decider and writers").

Separate from :mod:`pipeline.config` for two reasons. The decider is a phase-2
experiment behind one switch (``DECIDER=clerk`` restores the old path), so its
knobs stay in one file that can be deleted with it. And these settings read the
process environment only, never ``.env`` directly: ``capture.bridge`` already
loads ``backend/.env`` into the environment at runtime, and tests must never
pick up a developer's file.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline.config import Settings

__all__ = ["DEFAULT_TOPICS", "DeciderSettings"]

log = logging.getLogger(__name__)

#: The persona's topics, the choice Jev picks the writer's topic from.
DEFAULT_TOPICS: list[str] = [
    "caffeine", "food", "alcohol", "screen", "people",
    "outdoors", "medication", "sleep", "movement", "other",
]

def _default_writer_model() -> str:
    """The clerk's model unless told otherwise, so one T1_MODEL moves both."""

    return os.environ.get("T1_MODEL") or Settings.model_fields["t1_model"].default


class DeciderSettings(BaseSettings):
    """Decider and writer configuration, from environment variables only."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    #: DECIDER=jev turns the decider on. Clerk by default so nothing changes
    #: until someone switches it.
    decider: Literal["jev", "clerk"] = "clerk"
    typesafe_api_key: str | None = None
    jev_model: str = "jev-latest"
    #: Jev answers in 70-500 ms; 2 s is generous, and a timeout falls back to
    #: the clerk rather than failing the escalation.
    jev_timeout_s: float = 2.0

    # One threshold per action (annotate always fires, so it has none). Acts
    # that interrupt the wearer sit higher than ones that only write a row.
    decide_log_insight: float = Field(0.60, ge=0.0, le=1.0)
    decide_remember: float = Field(0.70, ge=0.0, le=1.0)
    decide_watch: float = Field(0.60, ge=0.0, le=1.0)
    decide_speak: float = Field(0.70, ge=0.0, le=1.0)
    decide_ask: float = Field(0.75, ge=0.0, le=1.0)
    decide_act: float = Field(0.80, ge=0.0, le=1.0)
    decide_look: float = Field(0.60, ge=0.0, le=1.0)

    #: A speak/ask/act score inside [low, high] hands the whole decision to
    #: the clerk: Jev is not sure enough either way to interrupt on its own.
    decide_uncertain_low: float = Field(0.40, ge=0.0, le=1.0)
    decide_uncertain_high: float = Field(0.60, ge=0.0, le=1.0)

    decide_topics_json: str = json.dumps(DEFAULT_TOPICS)

    writer_model: str = Field(default_factory=_default_writer_model)
    writer_timeout_s: float = 6.0
    act_sound_max_per_hour: int = 6
    quiet_max_s: float = 45.0

    @model_validator(mode="after")
    def _band_is_ordered(self) -> "DeciderSettings":
        if not self.decide_uncertain_low < self.decide_uncertain_high:
            raise ValueError(
                f"DECIDE_UNCERTAIN_LOW ({self.decide_uncertain_low}) must be below "
                f"DECIDE_UNCERTAIN_HIGH ({self.decide_uncertain_high})"
            )
        return self

    def thresholds(self) -> dict[str, float]:
        """Threshold per action name. ``annotate`` always fires and is absent."""

        return {
            "log_insight": self.decide_log_insight,
            "remember": self.decide_remember,
            "watch": self.decide_watch,
            "speak": self.decide_speak,
            "ask": self.decide_ask,
            "act": self.decide_act,
            "look": self.decide_look,
        }

    def topics(self) -> list[str]:
        """The topic list, leniently: a bad DECIDE_TOPICS_JSON warns and falls
        back to the default rather than stopping the backend from starting."""

        try:
            parsed = json.loads(self.decide_topics_json)
        except json.JSONDecodeError as exc:
            log.warning("Invalid DECIDE_TOPICS_JSON; using default: %s", exc)
            return list(DEFAULT_TOPICS)
        if (not isinstance(parsed, list) or not parsed
                or not all(isinstance(item, str) for item in parsed)):
            log.warning("DECIDE_TOPICS_JSON must be a non-empty list of strings; "
                        "using default")
            return list(DEFAULT_TOPICS)
        return parsed
