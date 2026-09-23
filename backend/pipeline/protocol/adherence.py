"""Adherence matcher (PLAN 2.2): sightings become ``seen``, silence becomes ``missed``.

Two inputs, both on the tick clock:

* **Evidence.** :meth:`AdherenceMatcher.on_evidence` is the reasoner's
  ``on_evidence`` hook, called the moment an escalation's frames are copied
  (``Reasoner._admit``). A ``medication_seen`` escalation inside an open
  ``dose`` window marks that item ``seen`` with ``evidence_ref =
  <decision_id>/<frame_ref>`` -- the thumbnail is ``GET /api/evidence/<ref>``.
  Meal, walk and wind-down items get ``seen`` the same way from the triggers
  that already see them (``food_in_frame``, ``outdoor_sustained``,
  ``screen_sustained``). Outside every window a sighting is an episode only.
* **Ticks.** :meth:`AdherenceMatcher.on_tick` watches the clock cross each
  ``dose`` window's end. A dose still unsighted then is ``missed``, and the
  wearer hears one line, exactly once per close. A missed dose is the one
  line that must land, so it bypasses the speech limiter's gap and hourly cap
  (``SpeechLimiter.grant``, STATE §8) but still stamps it: the next ordinary
  line waits its gap. Only a crossing the matcher saw counts: a backend
  started at 23:00 does not tell the wearer the morning window "just closed".

The matcher only writes over a day with nothing on it (``waiting``). What the
wearer set by hand -- ``done``, or ``undone`` on a sighting they rejected --
is never overwritten by a sighting. ``undone`` still becomes ``missed`` when
its dose window closes: the wearer said it was not taken.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time

from ..actions.speech import SpeechLimiter
from ..db import Database, day_key
from ..models import Escalation

log = logging.getLogger(__name__)

__all__ = ["AdherenceMatcher", "MISSED_LINE", "SIGHTING_TRIGGERS"]

#: Which trigger's escalation counts as a sighting of which protocol ``kind``.
SIGHTING_TRIGGERS: dict[str, str] = {
    "medication_seen": "dose",
    "food_in_frame": "meal",
    "outdoor_sustained": "walk",
    "screen_sustained": "winddown",
}

#: Spoken once when a dose window closes with no sighting.
MISSED_LINE = "Your {name} window just closed. Take it now, or mark it skipped in Brian."

#: Statuses a sighting may turn into ``seen``. ``None`` is a day with no row.
_SEEABLE = (None, "waiting")
#: Statuses a closing dose window turns into ``missed``.
_MISSABLE = (None, "waiting", "undone")


def _at(day: str, hhmm: str) -> float:
    """Unix time of local ``HH:MM`` on local ``day`` (``YYYY-MM-DD``)."""

    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), time(hour, minute)).timestamp()


class AdherenceMatcher:
    """Marks protocol items ``seen`` / ``missed`` for the local day."""

    def __init__(self, db: Database, speech: SpeechLimiter) -> None:
        self.db = db
        self.speech = speech
        self._last_t: float | None = None
        #: ``(item_id, day)`` pairs already closed by this process: a second
        #: close event (a replayed clock, a re-send) never speaks again.
        self._closed: set[tuple[str, str]] = set()

    # -- inputs ------------------------------------------------------------

    def on_evidence(self, esc: Escalation, decision_id: str,
                    frames: dict[str, bytes]) -> dict | None:
        """``Reasoner.on_evidence``: a sighting escalation's frames were saved."""

        kind = SIGHTING_TRIGGERS.get(esc.trigger)
        if kind is None:
            return None
        ref = esc.tick.frame_ref if esc.tick.frame_ref in frames else next(
            reversed(frames), None)
        evidence_ref = f"{decision_id}/{ref}" if ref is not None else None
        return self.on_sighting(kind, esc.t, evidence_ref)

    def on_sighting(self, kind: str, t: float,
                    evidence_ref: str | None) -> dict | None:
        """Mark the first unsighted ``kind`` item whose window is open at ``t``.

        Returns the status row written, or ``None`` when no window was open
        (the sighting stays an episode only).
        """

        day = day_key(t)
        for item in self._scheduled(day, kind):
            if not _at(day, item["window_start"]) <= t < _at(day, item["window_end"]):
                continue
            current = self.db.protocol_status(item["id"], day)
            if (current or {}).get("status") not in _SEEABLE:
                continue
            log.info("protocol: %s seen at %s (%s)", item["name"], t, evidence_ref)
            return self.db.set_protocol_status(
                item["id"], day, "seen", t=t, seen_t=t, evidence_ref=evidence_ref)
        return None

    def on_tick(self, t: float) -> list[dict]:
        """Close every dose window whose end the clock crossed since the last tick."""

        last, self._last_t = self._last_t, t
        if last is None or t <= last:
            return []
        closed: list[dict] = []
        for day in sorted({day_key(last), day_key(t)}):
            for item in self._scheduled(day, "dose"):
                if last < _at(day, item["window_end"]) <= t:
                    row = self.close_window(item, day, t)
                    if row is not None:
                        closed.append(row)
        return closed

    def close_window(self, item: dict, day: str, t: float) -> dict | None:
        """One dose window closed. Unsighted: ``missed``, and one line spoken."""

        key = (item["id"], day)
        if key in self._closed:
            return None
        self._closed.add(key)
        current = self.db.protocol_status(item["id"], day)
        if (current or {}).get("status") not in _MISSABLE:
            return None
        row = self.db.set_protocol_status(item["id"], day, "missed", t=t)
        log.info("protocol: %s missed on %s", item["name"], day)
        # The one line that must land: not held to the gap or the hourly cap,
        # but stamped on the limiter so the next ordinary line waits its gap.
        self.speech.grant(t)
        self.speech.speak(MISSED_LINE.format(name=item["name"]), "normal", t=t)
        return row

    # -- helpers -------------------------------------------------------------

    def _scheduled(self, day: str, kind: str) -> list[dict]:
        """``kind`` items scheduled on ``day``, earliest window first."""

        weekday = date.fromisoformat(day).weekday()
        return [item for item in self.db.list_protocol_items()
                if item["kind"] == kind and weekday in item["days"]]
