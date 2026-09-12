"""The fixed 7-day synthetic dataset (SPEC §6, §7).

SPEC §6 does not ask for real history -- it asks for "synthetic data containing
a pattern worth finding". This module is that data, and it is **fixed**: no
randomness, no clock reads beyond resolving ``end_day`` to timestamps, so the
same ``end_day`` always produces byte-identical rows.

**The pattern.** Days 2, 4 and 6 of the window are late-caffeine days: a coffee
at ~16:30, a 00:45 bedtime instead of 23:00, 5.8-6.2 h of sleep instead of
7.4-7.8, HRV recovery at 0.82-0.86 instead of ~1.02, and SRI around 62 instead
of 84. Nothing labels those days -- the coffee is a live
``caffeine_sighting`` episode and the bad night is a seeded WHOOP row, so
correlating them is real work for the reasoner and the report. A second,
weaker trend runs underneath: steps fall from 9.1k to 5.5k across the week.

Day/night convention: ``sleep_hours`` and ``bed_time`` for day *D* describe the
night that **starts** on day D. That is what makes "coffee at 16:30 on day D"
and "5.8 h of sleep on day D" the same story.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..models import Episode, SeededRow

__all__ = [
    "DAY_COUNT",
    "LATE_CAFFEINE_INDEXES",
    "SEEDED_SOURCES",
    "SEEDED_UNITS",
    "days_ending",
    "seed_rows",
    "seed_live_episodes",
]

DAY_COUNT = 7

#: Zero-based indexes of the late-caffeine days, i.e. days 2, 4 and 6.
LATE_CAFFEINE_INDEXES = (1, 3, 5)

#: Hour (local, after midnight) of the late coffee on those days.
LATE_COFFEE_HOUR = 16.5
#: Everyone has a morning coffee; it is never late.
MORNING_COFFEE_HOUR = 8.25

# -- seeded series, one value per day, oldest first -----------------------

SLEEP_HOURS = (7.4, 5.8, 7.6, 6.0, 7.8, 6.2, 7.5)
BED_TIME = (23.0, 0.75, 23.0, 0.75, 23.0, 0.75, 23.0)
HRV_RMSSD_RATIO = (1.02, 0.82, 1.03, 0.84, 1.01, 0.86, 1.02)
SLEEP_REGULARITY_SRI = (84.0, 62.0, 85.0, 61.0, 83.0, 63.0, 84.0)
STEPS = (9100.0, 8500.0, 7900.0, 7300.0, 6700.0, 6100.0, 5500.0)
VILPA_MINUTES = (4.2, 3.8, 3.1, 2.6, 2.2, 1.8, 1.4)
GAIT_SPEED_MS = (1.31, 1.28, 1.26, 1.24, 1.22, 1.19, 1.17)
BALANCE_ONE_LEG_S = (14.0, 13.0, 15.0, 12.0, 13.0, 11.0, 12.0)
NIGHT_NOISE_DB = (41.0, 42.0, 43.0, 42.0, 41.0, 44.0, 42.0)
BREATHWORK_MINUTES = (6.0, 5.0, 5.0, 0.0, 6.0, 0.0, 5.0)
PURPOSE_SCORE = (4.0, 4.0, 4.0, 3.0, 4.0, 4.0, 4.0)
DAYTIME_LIGHT_MINUTES = (35.0, 28.0, 41.0, 22.0, 38.0, 26.0, 44.0)
EVENING_LIGHT_OK = (1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0)
RESTING_HR = (58.0, 63.0, 58.0, 63.0, 57.0, 63.0, 58.0)
RESPIRATORY_RATE = (14.2, 15.1, 14.0, 15.3, 13.9, 15.0, 14.1)
SKIN_TEMP_DEV = (0.0, 0.3, -0.1, 0.4, 0.0, 0.2, -0.1)
SPO2 = (98.0, 96.0, 98.0, 96.0, 99.0, 97.0, 98.0)
DEEP_MIN = (92.0, 61.0, 96.0, 64.0, 101.0, 68.0, 94.0)
REM_MIN = (112.0, 78.0, 116.0, 81.0, 119.0, 84.0, 114.0)
RECOVERY_SCORE = (82.0, 34.0, 86.0, 39.0, 79.0, 43.0, 84.0)
STRAIN = (14.2, 6.1, 16.8, 5.4, 18.1, 6.8, 8.3)
RUN_KM = (8.0, 0.0, 12.0, 0.0, 16.0, 0.0, 0.0)
RUN_PACE = (5.15, 0.0, 5.25, 0.0, 5.42, 0.0, 0.0)
RUN_AVG_HR = (146.0, 0.0, 151.0, 0.0, 154.0, 0.0, 0.0)
VO2_MAX = (51.0,) * DAY_COUNT
WALKING_STEADINESS = (94.0, 91.0, 95.0, 90.0, 94.0, 92.0, 95.0)
DAYLIGHT_MIN = (35.0, 28.0, 41.0, 22.0, 38.0, 26.0, 44.0)
JOURNAL_ALCOHOL = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
JOURNAL_CAFFEINE_LATE = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0)
JOURNAL_ZERO = (0.0,) * DAY_COUNT

#: SPEC §7 provenance for each seeded row.
SEEDED_SOURCES: dict[str, str] = {
    "sleep_hours": "whoop",
    "sleep_regularity_sri": "whoop",
    "hrv_rmssd_ratio": "whoop",
    "bed_time": "whoop",
    "wake_time": "whoop",
    "steps": "phone",
    "vilpa_minutes": "phone",
    "gait_speed_ms": "phone",
    "night_noise_db": "phone",
    "daytime_light_minutes": "phone",
    "evening_light_ok": "phone",
    "nature_minutes": "phone",
    "balance_one_leg_s": "user",
    "breathwork_minutes": "user",
    "purpose_score": "user",
    "resting_hr": "whoop",
    "respiratory_rate": "oura",
    "skin_temp_dev": "oura",
    "spo2": "apple_watch",
    "deep_min": "oura",
    "rem_min": "oura",
    "recovery_score": "whoop",
    "strain": "whoop",
    "run_km": "apple_watch",
    "run_pace": "apple_watch",
    "run_avg_hr": "apple_watch",
    "vo2_max": "apple_watch",
    "walking_steadiness": "apple_watch",
    "daylight_min": "apple_watch",
    "journal_alcohol": "whoop",
    "journal_caffeine_late": "whoop",
    "journal_nicotine": "whoop",
    "journal_cannabis": "whoop",
}

SEEDED_UNITS: dict[str, str] = {
    "sleep_hours": "h",
    "sleep_regularity_sri": "SRI",
    "hrv_rmssd_ratio": "ratio",
    "bed_time": "h after midnight",
    "wake_time": "h after midnight",
    "steps": "steps",
    "vilpa_minutes": "min",
    "gait_speed_ms": "m/s",
    "night_noise_db": "dB",
    "daytime_light_minutes": "min",
    "evening_light_ok": "0/1",
    "nature_minutes": "min",
    "balance_one_leg_s": "s",
    "breathwork_minutes": "min",
    "purpose_score": "1-5",
    "resting_hr": "bpm",
    "respiratory_rate": "brpm",
    "skin_temp_dev": "°C",
    "spo2": "%",
    "deep_min": "min",
    "rem_min": "min",
    "recovery_score": "0-100",
    "strain": "0-21",
    "run_km": "km",
    "run_pace": "min/km",
    "run_avg_hr": "bpm",
    "vo2_max": "mL/kg/min",
    "walking_steadiness": "%",
    "daylight_min": "min",
    "journal_alcohol": "0/1",
    "journal_caffeine_late": "0/1",
    "journal_nicotine": "0/1",
    "journal_cannabis": "0/1",
}

# -- live episode series, one value per historical day (6 days) -----------

#: SPEC §8 work-hours proxy: 6-9 h of screen a day, averaging 7.5.
SCREEN_HOURS = (7.5, 8.0, 7.0, 8.5, 7.2, 6.8)
#: Weekly nature total of 84 min, deliberately short of the 120 min target.
OUTDOOR_MINUTES = (12.0, 18.0, 10.0, 14.0, 16.0, 14.0)
#: Dominant ``food_type`` of the day's logged meal (§9 enum).
MEAL_FOOD_TYPE = ("mixed", "processed", "vegetables", "mixed", "fish", "grains")
#: Conversation start hours per historical day.
CONVERSATION_HOURS: tuple[tuple[float, ...], ...] = (
    (10.5, 15.0),
    (11.0,),
    (9.75, 14.5),
    (13.0,),
    (10.0, 16.0),
    (11.5, 15.5),
)


def days_ending(end_day: str, n: int = DAY_COUNT) -> list[str]:
    """The ``n`` ``YYYY-MM-DD`` days ending at ``end_day`` inclusive, oldest first."""

    end = date.fromisoformat(end_day)
    return [date.fromordinal(end.toordinal() - i).isoformat() for i in range(n - 1, -1, -1)]


def _ts(day: str, hour: float) -> float:
    """Local-time epoch seconds for ``hour`` hours after midnight on ``day``."""

    midnight = datetime.combine(date.fromisoformat(day), datetime.min.time())
    return midnight.timestamp() + hour * 3600.0


def _row(day: str, metric: str, value: float) -> SeededRow:
    return SeededRow(
        day=day,
        metric=metric,
        value=float(value),
        unit=SEEDED_UNITS.get(metric, ""),
        source=SEEDED_SOURCES.get(metric, "phone"),
    )


def seed_rows(end_day: str) -> list[SeededRow]:
    """One row per seeded metric per day, for the 7 days ending ``end_day``."""

    rows: list[SeededRow] = []
    for i, day in enumerate(days_ending(end_day)):
        bed = BED_TIME[i]
        sleep = SLEEP_HOURS[i]
        wake = round((bed + sleep) % 24.0, 2)
        series = {
            "sleep_hours": sleep,
            "sleep_regularity_sri": SLEEP_REGULARITY_SRI[i],
            "hrv_rmssd_ratio": HRV_RMSSD_RATIO[i],
            "bed_time": bed,
            "wake_time": wake,
            "steps": STEPS[i],
            "vilpa_minutes": VILPA_MINUTES[i],
            "gait_speed_ms": GAIT_SPEED_MS[i],
            "balance_one_leg_s": BALANCE_ONE_LEG_S[i],
            "night_noise_db": NIGHT_NOISE_DB[i],
            "breathwork_minutes": BREATHWORK_MINUTES[i],
            "purpose_score": PURPOSE_SCORE[i],
            "daytime_light_minutes": DAYTIME_LIGHT_MINUTES[i],
            "evening_light_ok": EVENING_LIGHT_OK[i],
            # Nature is a live metric (SPEC §7); the seeded row exists only so
            # the 7-day integration panel is not silently missing it.
            "nature_minutes": 0.0,
            "resting_hr": RESTING_HR[i],
            "respiratory_rate": RESPIRATORY_RATE[i],
            "skin_temp_dev": SKIN_TEMP_DEV[i],
            "spo2": SPO2[i],
            "deep_min": DEEP_MIN[i],
            "rem_min": REM_MIN[i],
            "recovery_score": RECOVERY_SCORE[i],
            "strain": STRAIN[i],
            "run_km": RUN_KM[i],
            "run_pace": RUN_PACE[i],
            "run_avg_hr": RUN_AVG_HR[i],
            "vo2_max": VO2_MAX[i],
            "walking_steadiness": WALKING_STEADINESS[i],
            "daylight_min": DAYLIGHT_MIN[i],
            "journal_alcohol": JOURNAL_ALCOHOL[i],
            "journal_caffeine_late": JOURNAL_CAFFEINE_LATE[i],
            "journal_nicotine": JOURNAL_ZERO[i],
            "journal_cannabis": JOURNAL_ZERO[i],
        }
        rows.extend(_row(day, metric, value) for metric, value in series.items())
    return rows


class _Ids:
    """Deterministic ``e_seed_XXXX`` episode ids."""

    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"e_seed_{self.n:04d}"


def _episode(
    ids: _Ids, kind: str, day: str, start_hour: float, minutes: float, dominant: dict[str, Any]
) -> Episode:
    start = _ts(day, start_hour)
    duration = minutes * 60.0
    return Episode(
        id=ids.next(),
        kind=kind,  # type: ignore[arg-type]
        start_t=start,
        end_t=start + duration,
        duration_s=duration,
        dominant=dominant,
        tick_count=max(1, int(duration)),
        open=False,
    )


def seed_live_episodes(end_day: str) -> list[Episode]:
    """Historical live episodes for the six days *before* ``end_day``.

    Today's episodes come from the live tick stream; these exist so weekly live
    metrics (nature minutes, work hours) are not empty on day one, and so the
    late coffees that explain the bad nights are visible on the live side.
    """

    ids = _Ids()
    episodes: list[Episode] = []
    days = days_ending(end_day)[:-1]  # the previous 6 days, oldest first
    for i, day in enumerate(days):
        episodes.append(
            _episode(
                ids, "caffeine_sighting", day, MORNING_COFFEE_HOUR, 1.5,
                {"scene": "home", "activity": "standing"},
            )
        )
        for hour in CONVERSATION_HOURS[i]:
            episodes.append(
                _episode(
                    ids, "conversation", day, hour, 22.0,
                    {"scene": "office", "activity": "seated"},
                )
            )
        episodes.append(
            _episode(
                ids, "meal", day, 12.5, 35.0,
                {"scene": "restaurant", "activity": "eating", "food_type": MEAL_FOOD_TYPE[i]},
            )
        )
        episodes.append(
            _episode(
                ids, "screen_block", day, 9.5, SCREEN_HOURS[i] * 60.0,
                {"scene": "office", "activity": "seated"},
            )
        )
        episodes.append(
            _episode(
                ids, "outdoor_block", day, 18.5, OUTDOOR_MINUTES[i],
                {"scene": "park", "activity": "walking"},
            )
        )
        if i in LATE_CAFFEINE_INDEXES:
            episodes.append(
                _episode(
                    ids, "caffeine_sighting", day, LATE_COFFEE_HOUR, 2.0,
                    {"scene": "office", "activity": "seated"},
                )
            )
    return episodes
