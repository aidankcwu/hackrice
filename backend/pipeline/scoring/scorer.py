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

from datetime import date, datetime, timedelta
from typing import Iterable

from ..db import Database, day_key
from ..models import HEALTHY_FOOD_TYPES, Episode, Score
from .thresholds import (
    CAFFEINE_CUTOFF_LEAD_H,
    DEFAULT_BEDTIME_H,
    MetricSpec,
    THRESHOLDS,
    by_period,
)

__all__ = ["LIVE_DAILY_SOURCES", "Scorer", "row_provenance", "rollup"]

#: ``seeded``-table sources that are a **live wearable reporting now**, not the
#: SPEC §6 demo seed. A daily row written by one of these is provenance
#: ``live``: the number came off the wearer's own device this morning, and
#: neither the §8 panel nor the By-layer panel may call it "Seeded".
#:
#: Defined here rather than in :mod:`.healthspan` because that module imports
#: this one; ``healthspan`` re-exports the name, so there is still exactly one
#: definition. It is deliberately *not* derived from
#: :data:`pipeline.wearables.DEVICES`: that tuple is the catalogue of devices
#: this backend will *accept* samples from, and it lists ``apple_watch``,
#: ``whoop`` and ``oura`` -- which are exactly the names ``seed/fixtures.py``
#: writes into the ``seeded`` table for the demo. Membership there means "a real
#: device could send this", not "this row did come from one", so the two sets
#: cannot be the same object. Add a name here when a poller starts writing daily
#: rows under it.
LIVE_DAILY_SOURCES: frozenset[str] = frozenset({"fitbit", "apple_watch_live"})


def row_provenance(source: str) -> str:
    """``live`` when a ``seeded``-table row's own source is a live device."""

    return "live" if source in LIVE_DAILY_SOURCES else "seeded"


#: SPEC §8: sauna sessions count only above 19 minutes.
SAUNA_MIN_SECONDS = 19 * 60
#: SPEC §8 HRV: flag a 7d/60d ln RMSSD ratio below this.
HRV_FLAG_BELOW = 0.9
#: Hours awake in a day. Rate-type metrics observed over a short session are
#: extrapolated onto this, not onto 24 h -- nobody stares at a screen while asleep.
WAKING_HOURS = 16.0


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

    def _seeded(self, day: str) -> tuple[dict[str, float], dict[str, str]]:
        """``({metric: value}, {metric: source})`` for the day's integration rows.

        The sources ride along because a row's own ``source`` is what decides
        whether it is a live device reading or the demo seed.
        """

        rows = self.db.list_seeded(day, day)
        return ({r.metric: r.value for r in rows}, {r.metric: r.source for r in rows})

    def _episodes(self, day: str) -> list[Episode]:
        return self.db.list_episodes(day)

    # -- row construction ------------------------------------------------

    def _seeded_daily(
        self, spec: MetricSpec, seeded: dict[str, float], sources: dict[str, str]
    ) -> tuple[float | None, str | None, str]:
        """``(value, note, provenance)`` for one seeded daily spec.

        A row written by a connected device is labelled ``live`` and says which
        device, so the §8 panel stops calling a Fitbit night "Seeded". A row the
        demo seed wrote stays ``seeded``; a day with no device row still falls
        back to the seed and is reported as the seed, honestly.
        """

        raw = seeded.get(spec.metric)
        value = None if raw is None else float(raw)
        note: str | None = None
        if spec.metric == "hrv_rmssd_ratio" and value is not None and value < HRV_FLAG_BELOW:
            note = "below baseline (<0.90) — recovery flag"
        if value is None:
            return value, note, spec.source
        row_source = sources.get(spec.metric, "")
        provenance = row_provenance(row_source)
        if provenance == "live":
            note = _join(f"live from {row_source}", note)
        return value, note, provenance

    def _emit(
        self,
        spec: MetricSpec,
        period_key: str,
        value: float | None,
        note: str | None = None,
        score_override: float | None = None,
        source_override: str | None = None,
    ) -> Score:
        score, base_note = spec.evaluate(value)
        if score_override is not None:
            score = score_override
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
            source=source_override or spec.source,
            grade=spec.grade,
            note=final_note,
        )

    # -- live aggregation ------------------------------------------------

    @staticmethod
    def _of_kind(episodes: Iterable[Episode], kind: str) -> list[Episode]:
        return [e for e in episodes if e.kind == kind]

    @staticmethod
    def _screen_hours(episodes: Iterable[Episode]) -> float:
        """Hours covered by screen blocks: the union of their intervals.

        Two blocks that overlap (a stale open row beside a live one, or the
        builder flapping) cover the same seconds once. Summing durations
        counted them twice and more, which is how a 28-minute window once
        scored 65 h/day.
        """
        spans = sorted(
            (e.start_t, (e.end_t if e.end_t is not None else e.start_t + e.duration_s))
            for e in episodes if e.kind == "screen_block"
        )
        total = 0.0
        cur_start = cur_end = None
        for start, end in spans:
            if cur_end is None or start > cur_end:
                if cur_end is not None:
                    total += cur_end - cur_start
                cur_start, cur_end = start, end
            else:
                cur_end = max(cur_end, end)
        if cur_end is not None:
            total += cur_end - cur_start
        return max(0.0, total) / 3600.0

    def _caffeine(
        self, episodes: list[Episode], seeded: dict[str, float], reported: dict[str, dict]
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
            if reported.get(e.id, {}).get("confirmed") is not False
            and _hour_of_day(e.start_t) > cutoff_h
        ]
        note = f"cutoff {_fmt_hour(cutoff_h)} ({provenance} − 9 h)"
        if late:
            note += f"; last sighting {_fmt_hour(_hour_of_day(late[-1].start_t))}"
        value = sum(float(reported.get(e.id, {}).get("count") or 1.0) for e in late)
        changed = [e for e in self._of_kind(episodes, "caffeine_sighting")
                   if _hour_of_day(e.start_t) > cutoff_h
                   and (reported.get(e.id, {}).get("confirmed") is False
                        or reported.get(e.id, {}).get("count") is not None)]
        if changed:
            fragments = []
            for e in changed:
                r = reported[e.id]
                fragments.append("wearer reported: not mine" if r.get("confirmed") is False
                                 else f"wearer reported: {r['count']:g} servings")
            note = _join(note, *fragments) or note
        return value, note

    @staticmethod
    def _diet_pattern(meals: list[Episode], reported: dict[str, dict]) -> tuple[float | None, str | None]:
        if not meals:
            return None, None
        on_pattern = sum(1 for m in meals if
                         (reported.get(m.id, {}).get("food_type") or
                          m.dominant.get("food_type")) in HEALTHY_FOOD_TYPES)
        changed = [f"wearer reported: {reported[m.id]['food_type']}" for m in meals
                   if reported.get(m.id, {}).get("food_type") is not None
                   and reported[m.id]["food_type"] != m.dominant.get("food_type")]
        return on_pattern / len(meals), _join(
            f"{on_pattern}/{len(meals)} meals on-pattern", *changed)

    @staticmethod
    def _confirmed(episodes: list[Episode], reported: dict[str, dict]) -> list[Episode]:
        return [e for e in episodes if reported.get(e.id, {}).get("confirmed") is not False]

    @staticmethod
    def _report_note(episodes: list[Episode], reported: dict[str, dict]) -> str | None:
        return _join(*("wearer reported: not mine" for e in episodes
                       if reported.get(e.id, {}).get("confirmed") is False))

    @staticmethod
    def _alcohol(episodes: list[Episode], reported: dict[str, dict]) -> tuple[float, str | None]:
        value = 0.0
        notes: list[str] = []
        for e in episodes:
            r = reported.get(e.id, {})
            if r.get("confirmed") is False:
                notes.append("wearer reported: not mine")
            else:
                count = r.get("count")
                value += float(count if count is not None else 1.0)
                if count is not None and float(count) != 1.0:
                    notes.append(f"wearer reported: {float(count):g} drinks")
        return value, _join(*notes)

    # -- daily -----------------------------------------------------------

    def score_day(self, day: str) -> list[Score]:
        """Score every daily metric for ``day`` and upsert the rows."""

        episodes = self._episodes(day)
        reported = self.db.reported_by_episode(day)
        seeded, sources = self._seeded(day)
        raw_meals = self._of_kind(episodes, "meal")
        meals = self._confirmed(raw_meals, reported)
        diet_value, diet_note = self._diet_pattern(meals, reported)
        late_caffeine, caffeine_note = self._caffeine(episodes, seeded, reported)
        alcohol, alcohol_note = self._alcohol(self._of_kind(episodes, "alcohol_sighting"), reported)

        live_values: dict[str, tuple[float | None, str | None]] = {
            "social_episodes_daily": (
                float(len(self._confirmed(self._of_kind(episodes, "conversation"), reported))),
                self._report_note(self._of_kind(episodes, "conversation"), reported),
            ),
            "screen_hours_daily": (self._screen_hours(episodes), None),
            "meals_logged_daily": (float(len(meals)), self._report_note(raw_meals, reported)),
            "diet_pattern_daily": (diet_value, diet_note),
            "caffeine_cutoff_daily": (late_caffeine, caffeine_note),
            "alcohol_daily": (
                alcohol, alcohol_note,
            ),
        }

        scores: list[Score] = []
        for spec in by_period("daily"):
            if spec.source == "live":
                value, note = live_values.get(spec.metric, (None, None))
                provenance = spec.source
            else:
                value, note, provenance = self._seeded_daily(spec, seeded, sources)
            score = self._emit(spec, day, value, note, source_override=provenance)
            self.db.upsert_score(score)
            scores.append(score)
        return scores

    # -- session window --------------------------------------------------

    @staticmethod
    def _clip(episodes: Iterable[Episode], t0: float, t1: float) -> list[Episode]:
        """Episodes overlapping ``[t0, t1]``, with their durations clipped to it.

        An open episode has no ``end_t``; it is still running at ``t1``, so the
        window's right edge is its end for scoring purposes. Clipping rather
        than filtering is what makes a 2-minute session honest: a screen block
        that started an hour ago contributes the seconds that happened inside
        the window and not one more.
        """

        clipped: list[Episode] = []
        for episode in episodes:
            end = episode.end_t if episode.end_t is not None else t1
            if end <= t0 or episode.start_t >= t1:
                continue
            start = max(episode.start_t, t0)
            stop = min(end, t1)
            clipped.append(episode.model_copy(update={
                "start_t": start,
                "end_t": stop,
                "duration_s": max(0.0, stop - start),
            }))
        return clipped

    def _window_episodes(self, t0: float, t1: float) -> list[Episode]:
        """Everything overlapping the window, clipped to it.

        Read by time overlap rather than by the two ``day`` keys the window
        touches: an episode that started before midnight and is still open at
        01:00 belongs to a 01:00 window, and its ``day`` column says yesterday.
        """

        return self._clip(self.db.episodes_between(t0, t1), t0, t1)

    def score_window(self, t0: float, t1: float) -> list[Score]:
        """Score one ``[t0, t1]`` window -- a judging session, not a day.

        Returned, never upserted: a two-minute window is not a day and must not
        overwrite the day's rows. The shape is the ordinary :class:`Score` row
        with ``period="session"``, so the dashboard renders it with the
        component it already has.

        Two kinds of live metric need different treatment over a short window.
        A **rate** (screen hours) is meaningless raw -- two minutes of screen is
        0.03 h, which would score as a flawless low-risk day -- so it is
        extrapolated to a 16-hour waking day and the note says so out loud. A
        **count** (meals, conversations, sightings) is reported raw: three
        coffees in two minutes is three coffees, and multiplying it up would be
        a lie. Seeded metrics come from ``day_key(t1)`` exactly as
        :meth:`score_day` reads them and stay labelled ``seeded``, so nobody
        mistakes last night's sleep for something the glasses saw. Weekly specs
        are skipped: a week cannot be observed in a session.
        """

        t0, t1 = (t0, t1) if t1 >= t0 else (t1, t0)
        window_s = max(0.0, t1 - t0)
        window_h = window_s / 3600.0
        minutes = max(1, int(round(window_s / 60.0)))
        day = day_key(t1)
        period_key = f"{int(t0)}-{int(t1)}"

        episodes = self._window_episodes(t0, t1)
        reported = self.db.reported_by_episode()
        seeded, sources = self._seeded(day)
        raw_meals = self._of_kind(episodes, "meal")
        meals = self._confirmed(raw_meals, reported)
        diet_value, diet_note = self._diet_pattern(meals, reported)
        late_caffeine, caffeine_note = self._caffeine(episodes, seeded, reported)
        alcohol, alcohol_note = self._alcohol(self._of_kind(episodes, "alcohol_sighting"), reported)
        observed = f"observed in a {minutes}-minute session"

        screen_h = self._screen_hours(episodes)
        if window_h > 0:
            projected: float | None = screen_h * WAKING_HOURS / window_h
            screen_note = (
                f"at this rate: {projected:.1f} h/day over a {minutes}-minute session"
            )
        else:  # pragma: no cover - a zero-length window has nothing to project
            projected, screen_note = None, "zero-length session"

        live_values: dict[str, tuple[float | None, str | None]] = {
            "social_episodes_daily": (
                float(len(self._confirmed(self._of_kind(episodes, "conversation"), reported))),
                _join(observed, self._report_note(self._of_kind(episodes, "conversation"), reported))),
            "screen_hours_daily": (projected, screen_note),
            "meals_logged_daily": (float(len(meals)), _join(observed, self._report_note(raw_meals, reported))),
            "diet_pattern_daily": (diet_value, diet_note),
            "caffeine_cutoff_daily": (late_caffeine, caffeine_note),
            "alcohol_daily": (
                alcohol, _join(observed, alcohol_note)),
        }

        scores: list[Score] = []
        for spec in by_period("daily"):
            if spec.source == "live":
                value, note = live_values.get(spec.metric, (None, None))
                provenance = spec.source
            else:
                value, note, provenance = self._seeded_daily(spec, seeded, sources)
            row = self._emit(spec, period_key, value, note, source_override=provenance)
            # ``Score.period`` is a Literal["daily", "weekly"] owned by
            # ``pipeline.models``; ``model_copy`` re-labels the row without
            # widening that contract or re-validating it.
            scores.append(row.model_copy(update={"period": "session"}))
        return scores

    # -- weekly ----------------------------------------------------------

    def sightings_summary(self, end_day: str) -> dict:
        """Counts caffeine and alcohol sightings in two trailing 7-day windows."""

        this_days = self.week_days(end_day)
        first = date.fromisoformat(this_days[0])
        last_days = [(first - timedelta(days=i)).isoformat() for i in range(7, 0, -1)]

        def counts(days: list[str]) -> dict[str, int]:
            episodes = [episode for day in days for episode in self._episodes(day)]
            return {
                "caffeine": len(self._of_kind(episodes, "caffeine_sighting")),
                "alcohol": len(self._of_kind(episodes, "alcohol_sighting")),
            }

        current = counts(this_days)
        previous = counts(last_days)
        return {
            kind: {"this_week": current[kind], "last_week": previous[kind]}
            for kind in ("caffeine", "alcohol")
        }

    @staticmethod
    def _sightings_comparison(this_week: int, last_week: int) -> tuple[float | None, str]:
        if last_week == 0:
            return None, f"{this_week} this week vs 0 last week (no prior data)"
        change = (this_week - last_week) / last_week
        percent = round(abs(change) * 100)
        sign = "−" if change < 0 else "+" if change > 0 else ""
        note = f"{this_week} this week vs {last_week} last week ({sign}{percent}%)"
        if this_week < last_week:
            return 1.0, note
        if this_week == last_week:
            return 0.6, note
        return max(0.2, 0.6 - 0.4 * (this_week / last_week - 1.0)), note

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
        sightings = self.sightings_summary(days[-1]) if days else {
            "caffeine": {"this_week": 0, "last_week": 0},
            "alcohol": {"this_week": 0, "last_week": 0},
        }
        sighting_values: dict[str, tuple[float, str, float | None]] = {}
        for kind in ("caffeine", "alcohol"):
            current = sightings[kind]["this_week"]
            previous = sightings[kind]["last_week"]
            comparison_score, comparison_note = self._sightings_comparison(current, previous)
            sighting_values[f"{kind}_sightings_weekly"] = (
                float(current), comparison_note, comparison_score
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
            "caffeine_sightings_weekly": sighting_values["caffeine_sightings_weekly"][:2],
            "alcohol_sightings_weekly": sighting_values["alcohol_sightings_weekly"][:2],
        }

        scores: list[Score] = []
        for spec in by_period("weekly"):
            if spec.source == "live":
                value, note = live_values.get(spec.metric, (None, None))
                provenance = spec.source
            else:
                # No weekly seeded metric exists today; a future one would be a
                # mean over the window's rows.
                rows = [
                    row
                    for row in self.db.list_seeded(days[0], days[-1])
                    if row.metric == spec.metric
                ] if days else []
                values = [row.value for row in rows]
                value = sum(values) / len(values) if values else None
                note = f"mean of {len(values)} days" if values else None
                # A week is only ``live`` when every day of it came off a
                # device; one seeded day in the window and the mean is a mix,
                # which the dashboard must not show as a device reading.
                devices = {row.source for row in rows}
                provenance = spec.source
                if rows and all(row_provenance(src) == "live" for src in devices):
                    provenance = "live"
                    note = _join(f"live from {'/'.join(sorted(devices))}", note)
            score_override = (
                sighting_values[spec.metric][2]
                if spec.metric in sighting_values
                else None
            )
            score = self._emit(spec, week_key, value, note, score_override, provenance)
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
