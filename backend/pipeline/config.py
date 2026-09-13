"""Runtime configuration and timing constants.

Two things live here:

* :class:`Settings` -- environment-driven configuration (``.env`` / process env).
* :class:`Timings` -- every cooldown, window and rate limit in the pipeline,
  in two flavours. SPEC §6 requires a ``DEMO_MODE`` flag that shortens every
  cooldown and rate limit, because production timings will not let three
  triggers fire inside a four-minute demo.

Nothing downstream should hardcode a duration: read it off ``settings.timings``
so the demo/production switch stays in one place.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

__all__ = ["DEFAULT_KEYWORD_TRIGGERS", "Timings", "Settings", "get_settings"]

log = logging.getLogger(__name__)

DEFAULT_KEYWORD_TRIGGERS = [
    {
        "name": "rice_krispy",
        "keywords": [
            "rice krispy", "rice krispie", "krispy treat", "krispie treat",
            "crispy treat", "rice crispy",
        ],
        "note": "the wearer wants the glasses to ask a question whenever someone is holding or eating a Rice Krispy treat",
        "cooldown_s": 60,
    }
]


def _parse_keyword_triggers(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError("expected a JSON list")
        return parsed
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Invalid KEYWORD_TRIGGERS_JSON; using default: %s", exc)
        return [dict(entry) for entry in DEFAULT_KEYWORD_TRIGGERS]


@dataclass(frozen=True, slots=True)
class Timings:
    """All pipeline durations, in seconds (counts where noted).

    Construct with :meth:`production` or :meth:`demo` rather than by hand.
    """

    # Trigger gate -------------------------------------------------------
    #: Default per-trigger cooldown: a sustained condition escalates once.
    trigger_cooldown_default: float
    #: Minimum gap between *any* two escalations, across all triggers.
    global_escalation_min_gap: float
    #: Cooldown and rolling one-minute cap for meaningful visual changes.
    change_cooldown_s: float
    change_max_per_min: int

    # Sustained-condition windows (SPEC §12.2: evaluate over windows, never
    # a single tick, because the `ai` block is only present 50-80% of ticks).
    #
    # Every ``*_min_hits`` below is a **1 Hz reference count**: how many
    # positive ticks the condition needs when one tick arrives per second.
    # Person A's glasses emit a tick every ``tick_interval_s`` seconds, so a
    # window holds fewer ticks than that and the raw number can become
    # unreachable. Consumers must therefore never read these fields directly --
    # pass them through :meth:`scaled_hits`, which divides by the cadence.
    screen_sustained_window: float
    screen_sustained_min_hits: int
    people_sustained_window: float
    people_sustained_min_hits: int
    outdoor_sustained_window: float
    outdoor_min_hits: int
    food_window: float
    food_min_hits: int
    #: Window over which low motion counts as "still".
    stillness_window: float

    # Wearable biometrics (SPEC §14.3) -----------------------------------
    #: Window the intraday HR series must stay elevated across.
    biometric_window: float
    #: Multiple of resting HR above which a sample counts as elevated.
    biometric_hr_ratio: float
    #: Cooldown for the `biometric_anomaly` trigger (it has no episode kind,
    #: so this is the only thing stopping a long spike re-escalating).
    biometric_cooldown: float

    # Speech gating (SPEC §4.6) -----------------------------------------
    speech_min_gap: float
    speech_max_per_hour: int

    # Asking the wearer (docs/ASK_DESIGN.md §4) --------------------------
    #: Seconds after an ask fires before another root question may.
    ask_min_gap: float
    #: Hourly cap on questions, counted like utterances.
    ask_max_per_hour: int
    #: An ask bypasses the speech gap (it is the point of the exchange) but
    #: still waits this long after the last utterance so audio never overlaps.
    ask_speech_gap: float
    #: Answer window the phone is told to open, in seconds.
    ask_listen_s: float
    #: Seconds after the ask went out before an unanswered row expires.
    #: Measured from ``sent_t``, not from the escalation, and sized for
    #: synthesis (<= 8 s) + playback (~5 s) + ``ask_listen_s`` + slack (§8.4).
    ask_expire_s: float
    #: Follow-up questions allowed per root question (§8.5).
    ask_followup_max: int

    # T1 reasoner (SPEC §5.4: drop on contention, never queue) -----------
    t1_max_concurrent: int

    # Pending checks (SPEC §4.4 `watch`) ---------------------------------
    watch_default_after_s: float

    # Tick cadence -------------------------------------------------------
    #: Seconds between ticks on the stream these timings are tuned against.
    #: Defaults to the 1 Hz SPEC §2.1 baseline so every existing caller and
    #: fixture keeps its meaning; the running pipeline passes
    #: ``Settings.tick_interval_s``.
    tick_interval_s: float = 1.0

    # -- cadence-aware derivations ---------------------------------------

    def scaled_hits(self, n_at_1hz: int) -> int:
        """Convert a 1 Hz positive-tick count to this cadence.

        A "N hits in W seconds" threshold is really "N/W of the window was
        positive". At 1.5 s per tick a 20 s window only holds ~13 ticks, so a
        raw count of 20 can never be reached. Always at least 1 -- a threshold
        that rounds to zero would fire on nothing at all.
        """

        return max(1, round(n_at_1hz / self.tick_interval_s))

    @property
    def ai_max_age_ms(self) -> int:
        """Freshness budget for an ``ai`` block, in milliseconds (SPEC §12.2).

        A block older than ~2.5 ticks is unknown, not evidence. Never below the
        3 s the 1 Hz contract assumed, so a slower stream widens the window and
        a faster one does not narrow it.
        """

        return max(3000, int(2.5 * self.tick_interval_s * 1000))

    @classmethod
    def production(cls, tick_interval_s: float = 1.0) -> "Timings":
        return cls(
            trigger_cooldown_default=1200.0,
            global_escalation_min_gap=60.0,
            change_cooldown_s=20.0,
            change_max_per_min=3,
            screen_sustained_window=60.0,
            screen_sustained_min_hits=20,
            people_sustained_window=60.0,
            people_sustained_min_hits=15,
            outdoor_sustained_window=60.0,
            outdoor_min_hits=15,
            food_window=10.0,
            food_min_hits=3,
            stillness_window=300.0,
            biometric_window=180.0,
            biometric_hr_ratio=1.4,
            biometric_cooldown=1800.0,
            speech_min_gap=600.0,
            speech_max_per_hour=6,
            ask_min_gap=300.0,
            ask_speech_gap=5.0,
            ask_max_per_hour=6,
            ask_listen_s=8.0,
            ask_expire_s=25.0,
            ask_followup_max=1,
            t1_max_concurrent=1,
            watch_default_after_s=900.0,
            tick_interval_s=tick_interval_s,
        )

    @classmethod
    def demo(cls, tick_interval_s: float = 1.0) -> "Timings":
        return cls(
            trigger_cooldown_default=20.0,
            global_escalation_min_gap=5.0,
            change_cooldown_s=8.0,
            change_max_per_min=6,
            screen_sustained_window=20.0,
            screen_sustained_min_hits=3,
            people_sustained_window=20.0,
            people_sustained_min_hits=2,
            outdoor_sustained_window=20.0,
            outdoor_min_hits=2,
            food_window=10.0,
            food_min_hits=1,
            stillness_window=30.0,
            biometric_window=20.0,
            biometric_hr_ratio=1.4,
            biometric_cooldown=60.0,
            speech_min_gap=0.0,
            speech_max_per_hour=20,
            ask_min_gap=30.0,
            ask_speech_gap=0.0,
            ask_max_per_hour=10,
            ask_listen_s=8.0,
            ask_expire_s=25.0,
            ask_followup_max=1,
            t1_max_concurrent=1,
            watch_default_after_s=60.0,
            tick_interval_s=tick_interval_s,
        )


class Settings(BaseSettings):
    """Environment configuration. Reads ``.env`` from the working directory."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    openai_api_key: str | None = None
    t1_model: str = "gpt-5.4-mini"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "SAz9YHcvj6GT2YYXdXww"
    speech_mode: Literal["auto", "text", "elevenlabs"] = "auto"
    demo_mode: bool = True
    db_path: Path = Path("./data/pipeline.db")
    #: Frame ring-buffer TTL in seconds (SPEC §2.5 / §12.3).
    frame_ttl_s: float = 90.0
    #: Seconds between ticks off the capture path. SPEC §2.1 describes a 1 Hz
    #: stream; Person A's glasses actually emit one tick every 1.5 s. Every
    #: "N hits in W seconds" threshold is scaled by this (``Timings.scaled_hits``).
    tick_interval_s: float = 1.5
    keyword_triggers: Annotated[
        list[dict[str, Any]], NoDecode, BeforeValidator(_parse_keyword_triggers)
    ] = Field(
        default_factory=lambda: [dict(entry) for entry in DEFAULT_KEYWORD_TRIGGERS],
        validation_alias="KEYWORD_TRIGGERS_JSON",
    )
    # Declared so the literal .env key remains part of the documented Settings
    # surface; parsing and application happen through ``keyword_triggers``.
    keyword_triggers_json: str | None = Field(default=None, exclude=True)

    # -- live wearables (SPEC §15). Read by pipeline.wearables via os.environ
    # too; declared here so .env.example stays in sync with Settings. --------
    fitbit_client_id: str | None = None
    fitbit_client_secret: str | None = None
    fitbit_redirect_uri: str = "http://localhost:8010/api/wearables/fitbit/callback"
    fitbit_token_path: Path = Path("./data/fitbit_token.json")
    fitbit_poll_s: int = 300
    google_health_client_id: str | None = None
    google_health_client_secret: str | None = None
    google_health_redirect_uri: str = "http://localhost:8010/api/wearables/google-health/callback"
    google_health_token_path: Path = Path("./data/google_health_token.json")
    google_health_poll_s: int = 300
    wearable_ingest_token: str | None = None
    #: T0 VLM budget in seconds (SPEC §2.4). None = tick_interval_s - 0.1.
    vlm_budget_s: float | None = None

    # -- healthspan profile (scoring/brian_score.Profile). Bedtime comes from the
    # day's seeded bed_time, else thresholds.DEFAULT_BEDTIME_H. -------------------
    profile_age: int = 20
    #: "M" or "F"; any case accepted (the engine does sex.upper().startswith("F")).
    profile_sex: str = "M"
    profile_goal: Literal["average", "athlete", "shift", "genetic_risk"] = "average"
    #: Reserved for the engine's cadence -> gait model; unused by the adapter today.
    profile_height_m: float | None = None
    profile_cyp1a2_slow: bool = False

    @cached_property
    def timings(self) -> Timings:
        factory = Timings.demo if self.demo_mode else Timings.production
        return factory(tick_interval_s=self.tick_interval_s)


def get_settings(**overrides: object) -> Settings:
    """Build a :class:`Settings`. Keyword overrides win over the environment."""

    return Settings(**overrides)  # type: ignore[arg-type]
