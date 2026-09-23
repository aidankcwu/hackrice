"""Watcher and labeler configuration (docs/PERCEPTION.md "Watcher", "Labeler").

Every knob of the tiered perception path lives in :class:`CaptureSettings`, so
nothing in the watcher or labeler hard-codes a threshold, rate or cadence.

It is separate from :mod:`pipeline.config` on purpose: the perception work lands
on its own branch, and a second settings object means the merge touches no line
of ``Settings``. It also reads the process environment only (no ``env_file``):
``capture.bridge`` already loads ``backend/.env`` with ``load_dotenv``, and a test
must never pick up a developer's ``.env``.
"""

from __future__ import annotations

import json
import logging

from typing import Annotated, Any

from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

from longevity.ai_fields import BOOL_FIELDS
from pipeline.config import Switch

__all__ = ["CaptureSettings", "OffSwitch", "POINT_CONCEPTS", "DEFAULT_ENTER", "DEFAULT_EXIT"]

log = logging.getLogger(__name__)


def _parse_off_switch(value: Any) -> bool:
    """Like :data:`pipeline.config.Switch`, but a typo falls back to *off*.

    ``Switch`` is for stage switches that default on, so an unreadable value
    keeps the feature running. A switch that defaults off must fail closed: a
    typo in GATE_READS_WATCH must not quietly turn on a gate path nobody tested.
    """

    if isinstance(value, bool):
        return value
    raw = "" if value is None else str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"", "0", "false", "no", "off"}:
        return False
    log.warning("Unreadable switch value %r; treating as off", value)
    return False


#: A bool from the environment that defaults off and fails closed.
OffSwitch = Annotated[bool, BeforeValidator(_parse_off_switch)]

#: Default hysteresis per concept. A watcher score is a softmaxed similarity, not
#: a probability; these are placeholders until tools/probe_watcher.py calibrates
#: them (exit about two thirds of enter, PERCEPTION.md "Calibration").
DEFAULT_ENTER = 0.60
DEFAULT_EXIT = 0.40

#: Concepts that are a moment, not a state: a sip of coffee, a pill. They matter
#: when they appear, not while they stay in frame.
POINT_CONCEPTS: frozenset[str] = frozenset({
    "caffeine_visible",
    "alcohol_visible",
    "smoking_or_vaping_visible",
    "medication_visible",
})


class CaptureSettings(BaseSettings):
    """Watcher and labeler knobs, from the process environment only."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # -- watcher ---------------------------------------------------------------
    #: WATCHER=0 turns the watcher off: the labeler falls back to today's
    #: every-tick cadence.
    watcher: Switch = True
    watcher_model: str = "mobileclip2-s0"
    #: Frames per second the watcher scores at most. 7 is the DAT stream rate
    #: phase 3 samples down from; faster frames are dropped, never queued.
    watcher_fps_max: float = 7.0
    #: k of n frames above enter to go hot. 2 of 3 at 7 fps is ~300 ms to a
    #: wake-up; at 2 fps set 1 of 1 and let the exit rule debounce.
    watch_k: int = 2
    watch_n: int = 3
    #: Cooling -> cold after this long without re-entering hot.
    watch_cooldown_s: float = 30.0
    #: Novelty (1 - cosine to the running mean embedding) that counts as a spike.
    watch_novelty_enter: float = 0.35
    #: Window of the running mean embedding novelty is measured against.
    watch_novelty_ema_s: float = 30.0
    #: Quality gate on the sensor block. sharpness is variance-of-Laplacian at a
    #: fixed 256 px (longevity.sensors): near zero = blurred or blank, hundreds =
    #: crisp. 20 only rejects frames that are clearly smeared, since a plain but
    #: legitimate scene (a plate on a white table) can sit well under 100.
    watch_min_sharpness: float = 20.0
    #: lux_proxy is relative luminance x 1000 (0 black, 1000 clipped white); a
    #: seated indoor scene reads ~200. 10 (1 % luminance) rejects only a covered
    #: lens or a dark room, where no concept is recognisable anyway.
    watch_min_lux: float = 10.0
    #: Per-concept overrides, a JSON object: {"food_present": {"enter": 0.7, "exit": 0.5}}.
    watch_thresholds_json: str = "{}"

    # -- labeler ---------------------------------------------------------------
    #: One Gemini call this often when idle (refreshes scene/activity).
    labeler_heartbeat_s: float = 60.0
    #: First seconds after a concept goes hot that run at the full tick cadence.
    labeler_transition_s: float = 60.0
    #: Call interval once a concept has settled into steady hot.
    labeler_steady_s: float = 10.0
    #: The cooling window, also run at the full tick cadence.
    labeler_cooling_s: float = 30.0
    #: Cost ceiling (~$0.12/h at 600). Past it only wake-ups and the heartbeat run.
    labeler_max_per_hour: int = 600
    #: Idle this long and the heartbeat slows to labeler_dormant_heartbeat_s.
    labeler_dormant_after_s: float = 300.0
    labeler_dormant_heartbeat_s: float = 300.0
    #: Consecutive Gemini errors before the heartbeat backs off (wake-ups still try).
    labeler_error_backoff_n: int = 5

    # -- gate ------------------------------------------------------------------
    #: GATE_READS_WATCH=1 lets the trigger gate read the tick's watch block.
    #: Off by default so the gate behaves exactly as today until it is trusted.
    gate_reads_watch: OffSwitch = False

    def thresholds(self) -> dict[str, tuple[float, float]]:
        """(enter, exit) for every §9 boolean, with WATCH_THRESHOLDS_JSON applied.

        A bad override is logged and skipped, never raised: a typo typed at the
        venue must not stop the backend from starting.
        """

        out = {name: (DEFAULT_ENTER, DEFAULT_EXIT) for name in BOOL_FIELDS}
        try:
            overrides = json.loads(self.watch_thresholds_json)
        except json.JSONDecodeError as exc:
            log.warning("Invalid WATCH_THRESHOLDS_JSON; using defaults: %s", exc)
            return out
        if not isinstance(overrides, dict):
            log.warning("WATCH_THRESHOLDS_JSON is not a JSON object; using defaults")
            return out
        for name, value in overrides.items():
            if name.startswith("_"):
                # Metadata from tools/probe_watcher.py --out (model, date, ms), not a concept.
                continue
            if name not in out:
                log.warning("WATCH_THRESHOLDS_JSON: unknown concept %r ignored", name)
                continue
            try:
                enter = float(value.get("enter", DEFAULT_ENTER))
                exit_ = float(value.get("exit", DEFAULT_EXIT))
            except (AttributeError, TypeError, ValueError):
                log.warning("WATCH_THRESHOLDS_JSON: bad value for %r ignored: %r", name, value)
                continue
            if exit_ >= enter:
                log.warning("WATCH_THRESHOLDS_JSON: %r exit %.2f >= enter %.2f ignored",
                            name, exit_, enter)
                continue
            out[name] = (enter, exit_)
        return out
