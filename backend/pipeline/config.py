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

from pydantic import AliasChoices, BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

__all__ = ["DEFAULT_KEYWORD_TRIGGERS", "Timings", "Settings", "get_settings"]

log = logging.getLogger(__name__)

#: No keyword triggers by default. Set KEYWORD_TRIGGERS_JSON to add some:
#: [{"name": ..., "keywords": [...], "note": ..., "min_hits": 2, "cooldown_s": 20}].
DEFAULT_KEYWORD_TRIGGERS: list[dict[str, Any]] = []


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


def _parse_max_in_flight(value: Any) -> int:
    """1 or 2, leniently. A typo in a switch set at the venue must fall back to
    the rehearsed default with a warning, not stop the backend from starting."""

    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        log.warning("Invalid VLM_MAX_IN_FLIGHT %r; using 2", value)
        return 2
    if n not in (1, 2):
        log.warning("VLM_MAX_IN_FLIGHT=%d out of range; clamping to 1..2", n)
    return min(2, max(1, n))


_SWITCH_OFF = frozenset({"0", "false", "no", "off"})
_SWITCH_ON = frozenset({"", "1", "true", "yes", "on"})


def _parse_switch(value: Any) -> bool:
    """A boolean stage switch, leniently. Same rule as VLM_MAX_IN_FLIGHT: a
    typo typed in a hurry at the venue (FAST_PATH=O, CUE_TRIGGER=flase) must
    fall back to the rehearsed default (ON) with a warning, not raise a
    ValidationError that stops the backend from starting."""

    if isinstance(value, bool):
        return value
    raw = "" if value is None else str(value).strip().lower()
    if raw in _SWITCH_OFF:
        return False
    if raw in _SWITCH_ON:
        return True
    log.warning("Invalid stage switch value %r; using the default (on)", value)
    return True


#: A bool field that parses like a stage switch (see ``_parse_switch``).
Switch = Annotated[bool, BeforeValidator(_parse_switch)]

_DEFAULT_WIND_DOWN = "21:30"


def _parse_hhmm(value: Any) -> str:
    """Local ``HH:MM``, leniently: a typo falls back to 21:30 with a warning,
    same rule as the stage switches, never a startup failure."""

    raw = "" if value is None else str(value).strip()
    try:
        hour, minute = (int(part) for part in raw.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except ValueError:
        pass
    log.warning("Invalid WIND_DOWN_HHMM %r; using %s", value, _DEFAULT_WIND_DOWN)
    return _DEFAULT_WIND_DOWN


@dataclass(frozen=True, slots=True)
class Timings:
    """All pipeline durations, in seconds (counts where noted).

    Construct with :meth:`production` or :meth:`demo` rather than by hand.
    """

    # Trigger gate -------------------------------------------------------
    #: Default per-trigger cooldown: a sustained condition escalates once.
    trigger_cooldown_default: float
    #: Minimum gap between *any* two escalations, across all triggers. A
    #: persona cue (the ``cue`` trigger, ``bypass_gap``) is exempt: it goes to
    #: the voice agent, not into the clerk's queue.
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
    #: synthesis (<= 2.5 s, ``capture.speak.SYNTH_TIMEOUT_S``) + playback
    #: (~5 s) + ``ask_listen_s`` + slack (§8.4).
    ask_expire_s: float
    #: Follow-up questions allowed per root question (§8.5).
    ask_followup_max: int

    # The voice agent (docs/CONVERSATION_DESIGN.md §2) --------------------
    #: Questions one conversation may ask before a question is coerced to a
    #: statement (§4). Two is the whole budget: the wearer answers twice.
    conversation_max_questions: int
    #: Hard ceiling on a conversation's life, from open to close. Whatever
    #: state it is in when this runs out, it closes (§2).
    conversation_lifetime_s: float
    #: Quiet window after a conversation closes. Hand-offs arriving inside it
    #: are dropped with ``conversation_cooldown`` (§1). Zero in the demo: the
    #: one-conversation-at-a-time guard already stops two lines overlapping,
    #: and the agent's repeat check stops the same moment twice, so a quiet
    #: window on top only loses the next prop (seen live: a cucumber dropped
    #: 1.7 s after the coffee conversation closed).
    conversation_cooldown_s: float

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
            conversation_max_questions=2,
            conversation_lifetime_s=90.0,
            conversation_cooldown_s=60.0,
            t1_max_concurrent=1,
            watch_default_after_s=900.0,
            tick_interval_s=tick_interval_s,
        )

    @classmethod
    def demo(cls, tick_interval_s: float = 1.0) -> "Timings":
        """The frozen stage preset.

        Every later latency number was measured against exactly these values
        (plus TICK_INTERVAL_S unset = 1.5 s), so treat them as frozen: in
        particular do not drop the tick to 1.0 s (the VLM budget follows it and
        coverage collapses) and leave ``trigger_cooldown_default`` at 20 s.
        ``test_config.test_the_demo_preset_is_frozen`` pins the whole set.
        """

        return cls(
            trigger_cooldown_default=20.0,
            # 2 s, not 5: at 1.5 s ticks 5 s rounded up to a 6 s dead window
            # behind every wake-up, and T1 contention is already the slot's job
            # (drop, never queue). Persona cues skip the gap entirely.
            global_escalation_min_gap=2.0,
            # 8 s, main's audited value. Props no longer ride this trigger:
            # the one-tick `cue` trigger has its own zero cooldown and skips the
            # gap, so `change` only wakes the clerk on scene/activity flips, and
            # every wake-up it does not make frees the single T1 slot. The
            # earlier 4 s was never audited and roughly doubled those wake-ups.
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
            speech_max_per_hour=120,
            ask_min_gap=0.0,
            ask_speech_gap=0.0,
            ask_max_per_hour=60,
            # 6 s of open microphone. The only real "yes" on record finished
            # its last partial ~4.6 s after the mic opened, so 5 s left ~0.4 s
            # of margin; 6 s keeps ~1.4 s while still releasing the one
            # conversation slot 2 s sooner than production's 8 s. The phone
            # reads listen_s off each ask message, so no rebuild is needed.
            ask_listen_s=6.0,
            ask_expire_s=25.0,
            ask_followup_max=1,
            conversation_max_questions=2,
            conversation_lifetime_s=60.0,
            # 0.0 is only safe with the playback-length mouth-busy guard in
            # the voice agent (audit 2d): the phone's player never stops the
            # previous clip, so without the guard a line can land ~1.8 s after
            # the last close while that 2.2 s clip is still playing. If the
            # guard is not in, this must be 2.0.
            conversation_cooldown_s=0.0,
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
    #: A session opens on the first frame of a stream and closes when the
    #: frames stop (env AUTO_SESSION=0 to drive sessions by hand instead).
    auto_session: bool = True
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
    #: T0 VLM budget in seconds (SPEC §2.4). None = tick_interval_s.
    vlm_budget_s: float | None = None

    # -- stage kill switches. Each new reactive behaviour can be turned off at
    # the venue with an env var and a restart, never a code edit. All default
    # ON (the behaviour the demo is rehearsed with); OFF restores the old path.
    #: FAST_PATH=0: persona cues are not handed straight to the voice agent;
    #: they wake the clerk like any other trigger (gate.fast_path is None).
    #: The cue then also stops skipping the global escalation gap: on the
    #: clerk's path it competes for the one T1 slot like any other trigger.
    fast_path: Switch = True
    #: CUE_TRIGGER=0: no one-tick ``cue`` trigger at all, only the old
    #: triggers (``change`` then also stops deferring the hand to it).
    cue_trigger: Switch = True
    #: PUBLISH_ON_LANDING=0: a landed Gemini result waits for the next frame
    #: again instead of re-sending the newest tick (same tick_id) at once.
    #: This is the switch for the re-send path the gate, the episode builder
    #: and the DB each de-duplicate on their own.
    publish_on_landing: Switch = True
    #: MOUTH_BUSY_GUARD=0: hand-offs no longer wait for the last clip to end.
    #: The conversation cooldown then goes back to at least 2 s
    #: (``conversation.agent.UNGUARDED_COOLDOWN_S``), because the demo's 0 s
    #: is only safe while the guard is on.
    mouth_busy_guard: Switch = True
    #: VOICE_OPEN_SCHEMA=0: every voice turn uses the full schema again, not
    #: the short {utterance, kind} one on the opening turn.
    voice_open_schema: Switch = True
    #: VLM_MAX_IN_FLIGHT (alias T0_MAX_IN_FLIGHT): Gemini calls allowed in
    #: flight at once. 2 = overlapping tagger; 1 = the old serial tagger (a call
    #: is still only cancelled at the ~3 s ceiling, never at the tick budget).
    vlm_max_in_flight: Annotated[int, BeforeValidator(_parse_max_in_flight)] = Field(
        default=2,
        validation_alias=AliasChoices("vlm_max_in_flight", "t0_max_in_flight"),
    )

    # -- air quality (pipeline/wearables/air.py). Unset lat/lon is the honest
    # default: no coordinates means no air layer, never an invented number. ----
    air_lat: float | None = None
    air_lon: float | None = None
    #: OpenAQ v3 key. The API also answers a few unauthenticated calls, so a
    #: missing key is a degraded source, not a broken one.
    air_openaq_key: str | None = None
    #: Seconds one OpenAQ reading is reused for. PM2.5 is a daily exposure on an
    #: hourly reference network; polling faster buys nothing and costs quota.
    air_poll_s: int = 3600

    # -- healthspan profile (scoring/brian_score.Profile). Bedtime comes from the
    # day's seeded bed_time, else thresholds.DEFAULT_BEDTIME_H. -------------------
    profile_age: int = 20
    #: "M" or "F"; any case accepted (the engine does sex.upper().startswith("F")).
    profile_sex: str = "M"
    profile_goal: Literal["average", "athlete", "shift", "genetic_risk"] = "average"
    #: Reserved for the engine's cadence -> gait model; unused by the adapter today.
    profile_height_m: float | None = None
    profile_cyp1a2_slow: bool = False

    # -- autopilot (PLAN 4.1, pipeline/actions/autopilot.py): the system acts
    # instead of nagging. Sunset for the walk comes from AIR_LAT/AIR_LON. -------
    #: OUTDOOR_TARGET_MIN: outdoor minutes wanted by 16:00 local; fewer, and a
    #: 20 min walk goes on the calendar before sunset.
    outdoor_target_min: int = 30
    #: WIND_DOWN_HHMM: local time the phone's screen shield goes up until 07:00.
    wind_down_hhmm: Annotated[str, BeforeValidator(_parse_hhmm)] = _DEFAULT_WIND_DOWN

    def switches_line(self) -> str:
        """The effective kill-switch values, for one startup log line."""

        return (f"FAST_PATH={int(self.fast_path)} CUE_TRIGGER={int(self.cue_trigger)} "
                f"VLM_MAX_IN_FLIGHT={self.vlm_max_in_flight} "
                f"PUBLISH_ON_LANDING={int(self.publish_on_landing)} "
                f"MOUTH_BUSY_GUARD={int(self.mouth_busy_guard)} "
                f"VOICE_OPEN_SCHEMA={int(self.voice_open_schema)}")

    @cached_property
    def timings(self) -> Timings:
        factory = Timings.demo if self.demo_mode else Timings.production
        return factory(tick_interval_s=self.tick_interval_s)


def get_settings(**overrides: object) -> Settings:
    """Build a :class:`Settings`. Keyword overrides win over the environment."""

    return Settings(**overrides)  # type: ignore[arg-type]
