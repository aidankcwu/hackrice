"""The scorer (SPEC §10).

Reads episodes (live) and seeded integration rows and evaluates both against the
§8 thresholds **through the same code path** -- one :class:`~.thresholds.MetricSpec`
table, one ``evaluate``, one :class:`~pipeline.models.Score` row shape. The
dashboard labels a metric ``live`` or ``seeded`` and otherwise treats them
identically.

Synchronous, deterministic, and does no I/O beyond the injected
:class:`~pipeline.db.Database`. Calling :meth:`Scorer.score_day` twice for the
same day with the same inputs produces the same rows.

The model itself is rough on purpose -- see :mod:`pipeline.scoring.thresholds`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from ..db import Database, day_key
from ..models import Episode, Score
from .thresholds import (
    CAFFEINE_CUTOFF_LEAD_H,
    DEFAULT_BEDTIME_H,
    MEDITERRANEAN_FOOD_TYPES,
    MetricSpec,
    THRESHOLDS,
    by_period,
)

__all__ = ["Scorer", "rollup"]

#: SPEC §8: sauna sessions count only above 19 minutes.
SAUNA_MIN_SECONDS = 19 * 60
#: SPEC §8 HRV: flag a 7d/60d ln RMSSD ratio below this.
HRV_FLAG_BELOW = 0.9


def _hour_of_day(t: float) -> float:
    """Local wall-clock hour of a unix timestamp, as a float (13.5 = 13:30)."""

    dt = datetime.fromtimestamp(t)
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


def _fmt_hour(h: float) -> str:
    total = int(round((h % 24.0) * 60.0))
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _join(*parts: str | None) -> str | None:
    kept = [p for p in parts if p]
    return "; ".join(kept) if kept else None


def rollup(scores: Iterable[Score]) -> float:
    """Grade-weighted mean of the scores that actually have data.

    A=1.0, B=0.7, C=0.3 (SPEC §8: grade is evidence quality). Metrics with no
    value are excluded rather than counted as zeros -- a missing WHOOP sync is
    not a bad night.
    """

    num = 0.0
    den = 0.0
    for score in scores:
        if score.value is None:
            continue
        spec = THRESHOLDS.get(score.metric)
        weight = spec.weight if spec is not None else 0.5
        num += weight * score.score
        den += weight
    return num / den if den else 0.0


class Scorer:
    """Turns a day (or a week) of episodes and seeded rows into §8 scores."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- time helpers ----------------------------------------------------

    @staticmethod
    def local_day(t: float) -> str:
        """``YYYY-MM-DD`` in local time -- the same key episodes are stored under."""

        return day_key(t)

    @staticmethod
    def iso_week(day: str) -> str:
        """``YYYY-Www`` for a ``YYYY-MM-DD`` day."""

        year, week, _ = date.fromisoformat(day).isocalendar()
        return f"{year}-W{week:02d}"

    @staticmethod
    def week_days(end_day: str, n: int = 7) -> list[str]:
        """The ``n`` days ending at ``end_day`` inclusive, oldest first."""

        end = date.fromisoformat(end_day)
        return [(end.fromordinal(end.toordinal() - i)).isoformat() for i in range(n - 1, -1, -1)]

    # -- reads -----------------------------------------------------------

    def _seeded(self, day: str) -> dict[str, float]:
        return {row.metric: row.value for row in self.db.list_seeded(day, day)}

    def _episodes(self, day: str) -> list[Episode]:
        return self.db.list_episodes(day)

    # -- row construction ------------------------------------------------

    def _emit(
        self,
        spec: MetricSpec,
        period_key: str,
        value: float | None,
        note: str | None = None,
    ) -> Score:
        score, base_note = spec.evaluate(value)
        if value is None:
            final_note = base_note  # "no data" stands alone
        else:
            final_note = _join(note, base_note)
        return Score(
            metric=spec.metric,
            layer=spec.layer,
            period=spec.period,
            period_key=period_key,
            value=value,
            target=spec.target_text,
            score=score,
            source=spec.source,
            grade=spec.grade,
            note=final_note,
        )

    # -- live aggregation ------------------------------------------------

    @staticmethod
    def _of_kind(episodes: Iterable[Episode], kind: str) -> list[Episode]:
        return [e for e in episodes if e.kind == kind]

    @staticmethod
    def _screen_hours(episodes: Iterable[Episode]) -> float:
        return sum(e.duration_s for e in episodes if e.kind == "screen_block") / 3600.0

    def _caffeine(
        self, episodes: list[Episode], seeded: dict[str, float]
    ) -> tuple[float, str]:
        """``(late sightings, note)`` against the bedtime - 9 h cutoff."""

        bed = seeded.get("bed_time")
        if bed is None:
            bed_h = DEFAULT_BEDTIME_H
            provenance = "no seeded bed_time, assumed 23:00"
        else:
            # Seeded bedtimes are hours after local midnight; a value before
            # noon means the small hours of the next morning.
            bed_h = float(bed) + (24.0 if bed < 12.0 else 0.0)
            provenance = f"bedtime {_fmt_hour(bed_h)}"
        cutoff_h = bed_h - CAFFEINE_CUTOFF_LEAD_H
        late = [
            e
            for e in self._of_kind(episodes, "caffeine_sighting")
            if _hour_of_day(e.start_t) > cutoff_h
        ]
        note = f"cutoff {_fmt_hour(cutoff_h)} ({provenance} − 9 h)"
        if late:
            note += f"; last sighting {_fmt_hour(_hour_of_day(late[-1].start_t))}"
        return float(len(late)), note

    @staticmethod
    def _diet_pattern(meals: list[Episode]) -> tuple[float | None, str | None]:
        if not meals:
            return None, None
        on_pattern = sum(
            1 for m in meals if m.dominant.get("food_type") in MEDITERRANEAN_FOOD_TYPES
        )
        return on_pattern / len(meals), f"{on_pattern}/{len(meals)} meals on-pattern"

    # -- daily -----------------------------------------------------------

    def score_day(self, day: str) -> list[Score]:
        """Score every daily metric for ``day`` and upsert the rows."""

        episodes = self._episodes(day)
        seeded = self._seeded(day)
        meals = self._of_kind(episodes, "meal")
        diet_value, diet_note = self._diet_pattern(meals)
        late_caffeine, caffeine_note = self._caffeine(episodes, seeded)

        live_values: dict[str, tuple[float | None, str | None]] = {
            "social_episodes_daily": (
                float(len(self._of_kind(episodes, "conversation"))),
                None,
            ),
            "screen_hours_daily": (self._screen_hours(episodes), None),
            "meals_logged_daily": (float(len(meals)), None),
            "diet_pattern_daily": (diet_value, diet_note),
            "caffeine_cutoff_daily": (late_caffeine, caffeine_note),
            "alcohol_daily": (
                float(len(self._of_kind(episodes, "alcohol_sighting"))),
                None,
            ),
        }

        scores: list[Score] = []
        for spec in by_period("daily"):
            if spec.source == "live":
                value, note = live_values.get(spec.metric, (None, None))
            else:
                raw = seeded.get(spec.metric)
                value = None if raw is None else float(raw)
                note = None
                if (
                    spec.metric == "hrv_rmssd_ratio"
                    and value is not None
                    and value < HRV_FLAG_BELOW
                ):
                    note = "below baseline (<0.90) — recovery flag"
            score = self._emit(spec, day, value, note)
            self.db.upsert_score(score)
            scores.append(score)
        return scores

    # -- weekly ----------------------------------------------------------

    def score_week(self, week_key: str, days: list[str]) -> list[Score]:
        """Score every weekly metric over ``days`` and upsert the rows.

        ``week_key`` is the ISO ``YYYY-Www`` key the rows are filed under; it is
        not re-derived from ``days`` so a caller can score a trailing 7-day
        window under the current week.
        """

        episodes: list[Episode] = []
        screen_hours_by_day: list[float] = []
        for day in days:
            day_episodes = self._episodes(day)
            episodes.extend(day_episodes)
            # A day with no screen_block at all is missing coverage, not a
            # zero-hour work day; it must not drag the weekly mean down.
            if any(e.kind == "screen_block" for e in day_episodes):
                screen_hours_by_day.append(self._screen_hours(day_episodes))

        nature_minutes = (
            sum(e.duration_s for e in self._of_kind(episodes, "outdoor_block")) / 60.0
        )
        sauna_all = self._of_kind(episodes, "sauna_session")
        # The episode builder has no cold_plunge kind; SPEC §7 separates the two
        # scenes, so split sauna_session rows on their dominant scene tag.
        cold = [e for e in sauna_all if e.dominant.get("scene") == "cold_plunge"]
        sauna = [
            e
            for e in sauna_all
            if e.dominant.get("scene") != "cold_plunge"
            and e.duration_s > SAUNA_MIN_SECONDS
        ]
        mean_screen = (
            sum(screen_hours_by_day) / len(screen_hours_by_day)
            if screen_hours_by_day
            else None
        )

        live_values: dict[str, tuple[float | None, str | None]] = {
            "nature_minutes_weekly": (nature_minutes, f"over {len(days)} days"),
            "work_hours_weekly": (
                None if mean_screen is None else mean_screen * 7.0,
                None
                if mean_screen is None
                else f"{mean_screen:.1f} h/day over {len(screen_hours_by_day)} days × 7",
            ),
            "resistance_sessions_weekly": (
                float(len(self._of_kind(episodes, "gym_session"))),
                None,
            ),
            "sauna_sessions_weekly": (
                float(len(sauna)),
                f"{len(sauna_all) - len(cold) - len(sauna)} session(s) under 19 min ignored"
                if len(sauna_all) - len(cold) - len(sauna) > 0
                else None,
            ),
            "cold_plunge_weekly": (float(len(cold)), None),
        }

        scores: list[Score] = []
        for spec in by_period("weekly"):
            if spec.source == "live":
                value, note = live_values.get(spec.metric, (None, None))
            else:
                # No weekly seeded metric exists today; a future one would be a
                # mean over the window's rows.
                values = [
                    row.value
                    for row in self.db.list_seeded(days[0], days[-1])
                    if row.metric == spec.metric
                ] if days else []
                value = sum(values) / len(values) if values else None
                note = f"mean of {len(values)} days" if values else None
            score = self._emit(spec, week_key, value, note)
            self.db.upsert_score(score)
            scores.append(score)
        return scores

    # -- rollup ----------------------------------------------------------

    def score_all(self, today: str, days: list[str]) -> dict:
        """Score ``today`` daily and ``days`` weekly; return both plus a rollup."""

        daily = self.score_day(today)
        weekly = self.score_week(self.iso_week(today), days)
        return {
            "daily": daily,
            "weekly": weekly,
            "overall": rollup(daily + weekly),
        }
