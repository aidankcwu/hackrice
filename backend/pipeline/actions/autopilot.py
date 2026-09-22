"""Autopilot (PLAN 4.1): two rules that act on the phone instead of nagging.

Both are checked on the tick clock, like the adherence matcher's dose windows,
and each fires at most once per local day:

* **16:00, outdoor short.** Today's outdoor minutes (the ``outdoor_block``
  episodes the builder already writes) under ``OUTDOOR_TARGET_MIN`` put a
  20 min walk on the calendar: ``act calendar_block {minutes: 20, earliest:
  now, latest: sunset - 30 min}``.
* **Wind-down.** At ``WIND_DOWN_HHMM`` the phone shields the chosen apps:
  ``act screen_shield {until: "07:00"}``.

Only a crossing the autopilot saw counts: a backend started at 16:05 does not
block a walk for a check it never ran. Each firing is one decision row with a
single ``act`` action; :meth:`ActionHandler.on_act_result` flips it to
``acted`` or ``act_failed`` when the phone answers.

What may act is configuration, not persona: :func:`config_act_veto` holds back
a kind missing from ``AUTOPILOT_ACTS`` (``vetoed:disabled``) and every act on
an ``AUTOPILOT_QUIET_DAYS`` weekday (``vetoed:quiet_day``). The persona text
only reaches the LLM prompt; it never vetoes an act.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, time
from math import acos, asin, cos, degrees, radians, sin
from typing import Any
from uuid import uuid4

from ..db import Database, day_key
from ..models import Decision
from .handlers import ActionHandler, ActVetoFn

log = logging.getLogger(__name__)

__all__ = ["Autopilot", "OUTDOOR_CHECK_HHMM", "SHIELD_UNTIL", "WALK_MINUTES",
           "config_act_veto", "sunset_t"]

#: When the outdoor minutes are checked, local time.
OUTDOOR_CHECK_HHMM = "16:00"
#: Length of the walk the calendar block asks for.
WALK_MINUTES = 20
#: The walk ends this long before sunset.
BEFORE_SUNSET_S = 30 * 60
#: The screen shield holds until this local time the next morning.
SHIELD_UNTIL = "07:00"
#: Sunset when no coordinates are configured (AIR_LAT/AIR_LON unset).
FALLBACK_SUNSET_HHMM = "19:00"


def _at(day: str, hhmm: str) -> float:
    """Unix time of local ``HH:MM`` on local ``day`` (``YYYY-MM-DD``)."""

    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), time(hour, minute)).timestamp()


def sunset_t(day: str, lat: float | None, lon: float | None) -> float:
    """Unix time of sunset on local ``day`` (the sunrise equation, ~1 min).

    Without coordinates, :data:`FALLBACK_SUNSET_HHMM` local. Polar day and
    night clamp to the latest and earliest the equation allows.
    """

    if lat is None or lon is None:
        return _at(day, FALLBACK_SUNSET_HHMM)
    n = (date.fromisoformat(day) - date(2000, 1, 1)).days
    j_star = n - lon / 360.0
    m = (357.5291 + 0.98560028 * j_star) % 360.0
    c = (1.9148 * sin(radians(m)) + 0.0200 * sin(radians(2 * m))
         + 0.0003 * sin(radians(3 * m)))
    lam = (m + c + 180.0 + 102.9372) % 360.0
    transit = (2451545.0 + j_star + 0.0053 * sin(radians(m))
               - 0.0069 * sin(radians(2 * lam)))
    dec = asin(sin(radians(lam)) * sin(radians(23.4397)))
    cos_w = ((sin(radians(-0.833)) - sin(radians(lat)) * sin(dec))
             / (cos(radians(lat)) * cos(dec)))
    w = degrees(acos(min(1.0, max(-1.0, cos_w))))
    return (transit + w / 360.0 - 2440587.5) * 86400.0


def config_act_veto(acts: Iterable[str], quiet_days: Iterable[int]) -> ActVetoFn:
    """``ActionHandler.act_veto`` from ``AUTOPILOT_ACTS`` / ``AUTOPILOT_QUIET_DAYS``.

    ``disabled`` for a kind not in ``acts`` (an empty ``acts`` is autopilot
    off), else ``quiet_day`` when the local day of ``t`` is a quiet weekday
    (0 = Monday .. 6 = Sunday), else ``None``: the act goes out.
    """

    enabled = frozenset(acts)
    quiet = frozenset(quiet_days)

    def veto(kind: str, args: dict[str, Any], t: float) -> str | None:
        if kind not in enabled:
            return "disabled"
        if quiet and date.fromisoformat(day_key(t)).weekday() in quiet:
            return "quiet_day"
        return None

    return veto


class Autopilot:
    """Fires the two rule-based acts through the :class:`ActionHandler`."""

    def __init__(
        self, db: Database, handler: ActionHandler, *,
        outdoor_target_min: int = 30, wind_down_hhmm: str = "21:30",
        lat: float | None = None, lon: float | None = None,
    ) -> None:
        self.db = db
        self.handler = handler
        self.outdoor_target_min = int(outdoor_target_min)
        self.wind_down_hhmm = wind_down_hhmm
        self.lat = lat
        self.lon = lon
        self._last_t: float | None = None
        #: ``(rule, day)`` pairs already checked by this process: a replayed
        #: clock or a re-sent tick never fires a rule twice in one day.
        self._done: set[tuple[str, str]] = set()

    def on_tick(self, t: float) -> list[Decision]:
        """Run every rule whose local time the clock crossed since the last tick."""

        last, self._last_t = self._last_t, t
        if last is None or t <= last:
            return []
        fired: list[Decision] = []
        for day in sorted({day_key(last), day_key(t)}):
            for rule, hhmm in (("outdoor", OUTDOOR_CHECK_HHMM),
                               ("wind_down", self.wind_down_hhmm)):
                if not last < _at(day, hhmm) <= t or (rule, day) in self._done:
                    continue
                self._done.add((rule, day))
                try:
                    decision = (self._outdoor(day, t) if rule == "outdoor"
                                else self._wind_down(t))
                except Exception:  # never let a rule cost the tick loop
                    log.exception("autopilot rule %s failed on %s", rule, day)
                    continue
                if decision is not None:
                    fired.append(decision)
        return fired

    # -- rules ---------------------------------------------------------------

    def outdoor_minutes(self, day: str) -> float:
        """Minutes of ``outdoor_block`` episodes on local ``day``."""

        return sum(e.duration_s for e in self.db.list_episodes(day)
                   if e.kind == "outdoor_block") / 60.0

    def _outdoor(self, day: str, t: float) -> Decision | None:
        minutes = self.outdoor_minutes(day)
        if minutes >= self.outdoor_target_min:
            log.info("autopilot: %.0f outdoor min by %s, target met", minutes,
                     OUTDOOR_CHECK_HHMM)
            return None
        latest = sunset_t(day, self.lat, self.lon) - BEFORE_SUNSET_S
        return self._fire(
            "calendar_block",
            {"minutes": WALK_MINUTES, "earliest": round(t), "latest": round(latest)},
            t, trigger="autopilot:outdoor",
            interpretation=(f"{minutes:.0f} of {self.outdoor_target_min} min outside "
                            f"by {OUTDOOR_CHECK_HHMM}"),
        )

    def _wind_down(self, t: float) -> Decision:
        return self._fire(
            "screen_shield", {"until": SHIELD_UNTIL}, t,
            trigger="autopilot:wind_down",
            interpretation=f"Wind-down at {self.wind_down_hhmm}",
        )

    def _fire(self, kind: str, args: dict, t: float, *, trigger: str,
              interpretation: str) -> Decision:
        decision_id = f"d_auto_{uuid4().hex[:8]}"
        row = self.handler.act(decision_id, t, kind, args)
        decision = Decision(
            id=decision_id, t=t, trigger=trigger, trigger_tick_id="",
            interpretation=interpretation, confidence=1.0, actions=[row],
            model="autopilot",
        )
        self.db.insert_decision(decision)
        log.info("autopilot: %s -> act %s (%s)", trigger, kind, row.get("outcome"))
        return decision
