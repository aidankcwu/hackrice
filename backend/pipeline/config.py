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

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Timings", "Settings", "get_settings"]


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
            t1_max_concurrent=1,
            watch_default_after_s=900.0,
            tick_interval_s=tick_interval_s,
        )

    @classmethod
    def demo(cls, tick_interval_s: float = 1.0) -> "Timings":
        return cls(
            trigger_cooldown_default=45.0,
            global_escalation_min_gap=10.0,
            screen_sustained_window=20.0,
            screen_sustained_min_hits=8,
            people_sustained_window=20.0,
            people_sustained_min_hits=6,
            outdoor_sustained_window=20.0,
            outdoor_min_hits=6,
            food_window=10.0,
            food_min_hits=2,
            stillness_window=30.0,
            biometric_window=20.0,
            biometric_hr_ratio=1.4,
            biometric_cooldown=60.0,
            speech_min_gap=30.0,
            speech_max_per_hour=20,
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
    demo_mode: bool = True
    db_path: Path = Path("./data/pipeline.db")
    #: Frame ring-buffer TTL in seconds (SPEC §2.5 / §12.3).
    frame_ttl_s: float = 90.0
    #: Seconds between ticks off the capture path. SPEC §2.1 describes a 1 Hz
    #: stream; Person A's glasses actually emit one tick every 1.5 s. Every
    #: "N hits in W seconds" threshold is scaled by this (``Timings.scaled_hits``).
    tick_interval_s: float = 1.5

    # -- live wearables (SPEC §15). Read by pipeline.wearables via os.environ
    # too; declared here so .env.example stays in sync with Settings. --------
    fitbit_client_id: str | None = None
    fitbit_client_secret: str | None = None
    fitbit_redirect_uri: str = "http://localhost:8010/api/wearables/fitbit/callback"
    fitbit_token_path: Path = Path("./data/fitbit_token.json")
    fitbit_poll_s: int = 300
    wearable_ingest_token: str | None = None

    @cached_property
    def timings(self) -> Timings:
        factory = Timings.demo if self.demo_mode else Timings.production
        return factory(tick_interval_s=self.tick_interval_s)


def get_settings(**overrides: object) -> Settings:
    """Build a :class:`Settings`. Keyword overrides win over the environment."""

    return Settings(**overrides)  # type: ignore[arg-type]
