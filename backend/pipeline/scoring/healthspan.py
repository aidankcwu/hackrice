"""Dose-response healthspan view over this app's episodes and seeded rows (brian_score adapter).

:mod:`.brian_score` is a self-contained hazard engine (healthy-life hours, a
life-expectancy delta with a CI, levers ranked by hours per minute, a forecast
for tonight from leading indicators, a weekly ledger, and lagged attribution).
It is kept verbatim; this module is the only thing that knows both its
observation keys and this app's :class:`~pipeline.models.Episode` kinds and
SPEC §7 seeded rows.

Honesty rules of the adapter (the engine's own are listed in its docstring):

* every observation carries a provenance (``live`` / ``seeded`` / ``derived`` /
  ``missing``) and a one-line ``detail`` saying how it was made;
* a factor with no data is *absent* from ``obs`` and imputed by the engine at
  the population reference, so it can never earn credit;
* a zero from the glasses is a measurement only on a day that has episodes
  at all -- otherwise it is ``missing``;
* nothing is extrapolated to the end of the day and nothing is written back.

Day/night convention (``fixtures.py``): the seeded ``sleep_hours``, ``sri``,
``hrv_rmssd_ratio``, ``night_noise_db``, ``evening_light_ok`` and ``bed_time``
rows for day *D* describe the night that **starts** on D -- the same row the
§8 :class:`~.scorer.Scorer` uses, so the two panels agree on a day's sleep.

Synchronous and deterministic; the only clock read is the optional ``now_t``
the route passes in, used solely for ``as_of_hh``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import numpy as np

from ..config import Settings
from ..db import Database, day_key
from ..models import HEALTHY_FOOD_TYPES, Episode
from . import brian_score as bs
from .scorer import SAUNA_MIN_SECONDS, Scorer, _fmt_hour, _hour_of_day
from .thresholds import DEFAULT_BEDTIME_H

__all__ = ["healthspan_for_day", "healthspan_registry", "healthspan_week", "lite_payload",
           "profile_from_settings", "MAX_WEEK_DAYS", "NATURE_SCENES"]

# Known engine quirks (brian_score.py stays verbatim; each is handled or
# documented here rather than patched there):
#   * pins_from_episodes hard-codes a 14:00 caffeine cutoff -- _reconcile_pins
#     re-derives kind/effect from the profile cutoff so pin and forecast agree.
#   * forecast_tonight renders f"{16.5:.0f}:00" as "16:00" (half hours truncate).
#   * the "sauna" pin text assumes a qualifying session -- _engine_episode only
#     emits type "sauna" for >19 min, non-cold-plunge rows.
#   * insights() picks the "week" line by largest raw deficit across mixed
#     units (700 steps beats 36 nature minutes); the panel lists every line.
#   * the Recovery layer is two factors, so a measured sauna_wk=0 reads ~16/100
#     (worst tier) whenever HRV is at baseline -- a modelling choice, not a bug.
#   * the "tonight" insight always cites Drake 2013 whatever the driver.
#   * levers() ranks by gain / max(time, 1), so zero-time factors dominate ROI;
#     the adapter splits them out as levers_free (design §0 U3).

#: Outdoor scenes that count as green/blue space (White 2019). Derived from
#: :data:`~pipeline.models.OUTDOOR_SCENES` -- the built environment is outdoors
#: without being green or blue, so street, parking_lot, sports_venue,
#: construction_site and outdoor_other are excluded. ``tests/test_healthspan.py``
#: asserts the subset relation, so a new outdoor scene upstream fails loudly here.
NATURE_SCENES: frozenset[str] = frozenset({"park", "trail", "campus", "beach", "nature"})
#: Scene label for an outdoor_block with no ``scene`` tag: the builder only opens
#: one on ``scene in OUTDOOR_SCENES or vegetation_visible``, so it saw greenery.
UNTAGGED_SCENE = "green (no scene tag)"
#: Alcohol sightings closer than this are one occasion (sightings self-close at 60 s).
ALCOHOL_CLUSTER_GAP_S = 30 * 60
#: Drinks the day's count is read as at most -- the top of the engine's own
#: ``alcohol_drinks`` curve, and the same ceiling ``observations_from_app`` uses.
#: Past it a count is the camera re-seeing one table, not a new drink ("55
#: drinks" is a pipeline bug, never a display).
MAX_DRINKS_PER_DAY = 6
#: Evidence pins Today renders (screens.md §1.4). Merged episodes, newest last;
#: a day with more shows the most recent 12 and says so.
MAX_PINS_TODAY = 12
#: Tonight's screen window, hours after local midnight of ``day`` (22:00 -> 05:00 next morning).
NIGHT_SCREEN_WINDOW = (22.0, 29.0)
#: Daylight band for the bright-light fallback when no phone row exists (coarse, no solar model).
DAYLIGHT_FALLBACK_WINDOW = (8.0, 18.0)
#: Factors that are state markers, not actions; never offered as levers.
#: ``rt_z`` joins them for the same reason: a PVT score is a reading of the
#: person, not something they can decide to do for three minutes.
STATE_MARKERS = frozenset({"gait_speed", "recovery_ratio", "rt_z"})
#: coarse -- approximate FRIEND / ACSM treadmill norms (Kaminsky 2015), +-10 percentile.
#: sex -> decade -> VO2max (mL/kg/min) at the p5, p25, p50, p75, p95 knots.
VO2_PCT_ANCHORS: dict[str, dict[int, tuple[float, ...]]] = {
    "M": {
        20: (29.0, 37.0, 43.0, 50.0, 58.0),
        30: (28.0, 35.0, 41.0, 48.0, 55.0),
        40: (26.0, 33.0, 39.0, 45.0, 52.0),
        50: (23.0, 29.0, 35.0, 41.0, 48.0),
        60: (20.0, 25.0, 30.0, 36.0, 42.0),
    },
}
VO2_PCT_ANCHORS["F"] = {
    decade: tuple(v - 6.0 for v in row) for decade, row in VO2_PCT_ANCHORS["M"].items()
}
PCT_KNOTS = (5.0, 25.0, 50.0, 75.0, 95.0)
#: Prior nights needed before a bedtime habit or a sleep baseline is derived.
MIN_HABIT_ROWS = 3
#: (LEADING["caffeine_after_cutoff"]["sleep_h"], prior_se) for the attribution blend.
CAFFEINE_PRIOR = (-1.0, 0.3)
#: Attribution window, days before ``day``.
ATTRIBUTION_DAYS = 60
#: Engine name reported in the payload.
ENGINE = "brian_score"
#: Prior days with a ``utility_today`` before a 30-day mean is honest enough to
#: weight future healthy years by (``two_currencies(u_mean=...)``); below it the
#: mean is ``None`` and the engine falls back to today's own utility.
MIN_UTILITY_DAYS = 7
#: Trailing window the utility mean is taken over.
UTILITY_MEAN_DAYS = 30
#: Seeded metric prefix an adherence state is read from: ``adherence_<lever>_a``
#: and ``_b`` are the Beta(a, b) counts ``brian_score.Adherence`` keeps per lever.
ADHERENCE_PREFIX = "adherence_"
#: Thompson-sampling seed. Fixed so two polls of the same day rank levers the
#: same way -- the exploration happens across days, not across page reloads.
ADHERENCE_SEED = 7
#: Bright minutes at or below which a day counts as a no-daylight day.
NO_DAYLIGHT_MAX_MIN = 15.0
#: Night screen minutes at or above which a day counts as a night-screen day.
NIGHT_SCREEN_MIN_MIN = 20.0
#: Social index below which a day counts as isolated.
ISOLATED_MAX_INDEX = 20.0
#: Bed shift (minutes later than habit) at or above which a day counts as late.
LATE_BED_MIN_SHIFT = 30.0

#: Seeded rows read as-is: engine key -> (seeded metric, basis when the row is absent).
_SEEDED_AS_IS: dict[str, tuple[str, str]] = {
    "steps": ("steps", "phone"),
    "vilpa_min": ("vilpa_minutes", "phone"),
    "gait_speed": ("gait_speed_ms", "phone"),
    "sleep_hours": ("sleep_hours", "whoop"),
    "sri": ("sleep_regularity_sri", "whoop"),
    "noise_night_db": ("night_noise_db", "phone"),
    "smoker": ("journal_nicotine", "whoop"),
    "recovery_ratio": ("hrv_rmssd_ratio", "whoop"),
    #: Written by ``wearables/air.py`` off OpenAQ, never by the seed generator.
    "pm25": ("pm25", "openaq"),
}

#: The three-tap self-check, seeded metric -> ``utility_today`` check key.
_CHECK_METRICS = {"pvt_check_energy": "energy", "pvt_check_mood": "mood",
                  "pvt_check_clarity": "clarity"}

CONVENTIONS = [
    "Night rows (sleep_hours, sri, hrv_rmssd_ratio, night_noise_db, evening_light_ok, bed_time) "
    "for day D describe the night that starts on D — same row the §8 scorer uses.",
    "Weekly hazard doses use the trailing 7 days; the ledger uses the ISO week to date.",
    "Unmeasured factors are imputed at the population reference and earn nothing.",
    "Untyped meals are excluded from the Mediterranean share (the §8 scorer counts them as off-pattern).",
    "Alcohol sightings within 30 min are one drink, capped at 6 — the top of the hazard curve; "
    "a journal '1' is read as one drink.",
    "Pins are merged episodes (the engine collapses fragments and repeated sightings); Today shows "
    "the most recent 12 and reports pins_total.",
    "pm25 is the day's OpenAQ reading at the wearer's coordinates; with no coordinates there is no row "
    "and the factor is unmeasured.",
    "rt_z is the day's PVT z-score against the wearer's own baseline; with no test today Mind is unmeasured.",
]

#: How each narrator driver flag is decided, for the payload and the docs.
DRIVER_RULES = {
    "caffeine_late": "a caffeine sighting later than bedtime minus the profile cutoff "
                     "(9 h, 12 h when CYP1A2 slow), else the whoop journal_caffeine_late row",
    "alcohol": "alcohol_drinks >= 1 (sightings clustered at 30 min, else the whoop journal)",
    "night_screen": f"night_screen_min >= {NIGHT_SCREEN_MIN_MIN:g} inside 22:00–05:00",
    "late_bed": f"bed_time at least {LATE_BED_MIN_SHIFT:g} min later than the habit "
                "(lower median of the prior nights with a bed_time row)",
    "no_daylight": f"day_light_min <= {NO_DAYLIGHT_MAX_MIN:g} on a day that was measured at all",
    "isolated": f"social_index < {ISOLATED_MAX_INDEX:g} on a day the glasses covered",
}


@dataclass(frozen=True)
class Obs:
    """One observation with its provenance."""

    value: float | None
    #: ``live`` | ``seeded`` | ``derived`` | ``missing`` (design §0 U13).
    source: str
    #: ``glasses`` | ``phone`` | ``whoop`` | ``oura`` | ``apple_watch`` | ``user`` |
    #: ``assumed`` -- for seeded rows, the row's own ``source`` string.
    basis: str
    detail: str


@dataclass
class _DayData:
    """One day's raw reads, loaded once per request."""

    day: str
    episodes: list[Episode]
    #: ``{metric: value}`` over the day's seeded rows (the Scorer's flatten).
    seeded: dict[str, float]
    #: ``{metric: source}`` for the same rows (``whoop``, ``phone``, ...).
    sources: dict[str, str]

    @property
    def covered(self) -> bool:
        """True when the glasses produced any episode that day."""

        return bool(self.episodes)

    def of_kind(self, kind: str) -> list[Episode]:
        return [e for e in self.episodes if e.kind == kind]


# -- reads and time helpers ------------------------------------------------


def _load(db: Database, days: list[str], history: list[str]) -> dict[str, _DayData]:
    """Episodes for ``days`` and one seeded range over ``days`` and ``history``.

    Episodes are also read for a ``history`` day when its seeded rows can feed
    the attribution (``sleep_hours`` and ``strain`` present); on the seeded
    demo DB that adds nothing beyond the trailing week.
    """

    everything = sorted(set(days) | set(history))
    seeded: dict[str, dict[str, float]] = {d: {} for d in everything}
    sources: dict[str, dict[str, str]] = {d: {} for d in everything}
    for row in db.list_seeded(everything[0], everything[-1]):
        seeded.setdefault(row.day, {})[row.metric] = row.value
        sources.setdefault(row.day, {})[row.metric] = row.source
    wanted = set(days) | {
        d for d in history if {"sleep_hours", "strain"} <= seeded[d].keys()
    }
    return {
        d: _DayData(d, db.list_episodes(d) if d in wanted else [], seeded[d], sources[d])
        for d in everything
    }


def _iso_week_to_date(day: str) -> list[str]:
    """ISO Monday .. ``day`` inclusive, oldest first."""

    end = date.fromisoformat(day)
    monday = end - timedelta(days=end.weekday())
    return [(monday + timedelta(days=i)).isoformat() for i in range(end.weekday() + 1)]


def _local(day: str, hours: float) -> float:
    """Epoch seconds ``hours`` after local midnight of ``day`` (DST-safe)."""

    midnight = datetime.combine(date.fromisoformat(day), time.min)
    return (midnight + timedelta(hours=hours)).timestamp()


def _end(e: Episode) -> float:
    """``duration_s`` is authoritative -- valid for open episodes too."""

    return e.start_t + e.duration_s


def _overlap_minutes(e: Episode, t0: float, t1: float) -> float:
    return max(0.0, min(_end(e), t1) - max(e.start_t, t0)) / 60.0


def _clusters(episodes: list[Episode], gap_s: float) -> int:
    """Occasions: runs of sightings whose consecutive starts are within ``gap_s``."""

    starts = sorted(e.start_t for e in episodes)
    if not starts:
        return 0
    return 1 + sum(1 for a, b in zip(starts, starts[1:]) if b - a > gap_s)


def _bed_h(seeded: dict[str, float]) -> float | None:
    """Seeded ``bed_time`` in hours after local midnight; before noon = next morning."""

    bed = seeded.get("bed_time")
    if bed is None:
        return None
    return float(bed) + (24.0 if bed < 12.0 else 0.0)


def _lower_median(values: list[float]) -> float:
    return sorted(values)[(len(values) - 1) // 2]


def _fitness_pct(vo2: float, age: int, sex: str) -> float:
    """VO2max -> population percentile for the person's sex and decade, clamped 3..97."""

    decade = min(60, max(20, (int(age) // 10) * 10))
    table = VO2_PCT_ANCHORS["F" if sex.upper().startswith("F") else "M"]
    return float(np.clip(np.interp(vo2, table[decade], PCT_KNOTS), 3.0, 97.0))


def _decade_label(age: int) -> str:
    decade = min(60, max(20, (int(age) // 10) * 10))
    return "60+" if decade == 60 else f"{decade}s"


# -- per-day live aggregates (shared by factors and ledger rows) -----------


def _is_nature(e: Episode) -> bool:
    return "scene" not in e.dominant or e.dominant.get("scene") in NATURE_SCENES


def _nature_minutes(episodes: list[Episode]) -> float:
    return sum(e.duration_s for e in episodes if e.kind == "outdoor_block" and _is_nature(e)) / 60.0


def _resistance_minutes(episodes: list[Episode]) -> float:
    return sum(e.duration_s for e in episodes if e.kind == "gym_session") / 60.0


def _is_sauna(e: Episode) -> bool:
    return e.dominant.get("scene") != "cold_plunge" and e.duration_s > SAUNA_MIN_SECONDS


def _sauna_count(episodes: list[Episode]) -> int:
    return sum(1 for e in episodes if e.kind == "sauna_session" and _is_sauna(e))


def _daylight_fallback(d: _DayData) -> float:
    t0, t1 = (_local(d.day, h) for h in DAYLIGHT_FALLBACK_WINDOW)
    return sum(_overlap_minutes(e, t0, t1) for e in d.of_kind("outdoor_block"))


# -- observations -----------------------------------------------------------


def _seeded_obs(d: _DayData, metric: str, default_basis: str) -> Obs:
    if metric in d.seeded:
        basis = d.sources.get(metric, default_basis)
        return Obs(float(d.seeded[metric]), "seeded", basis, f"{basis} {metric}, row {d.day}")
    return Obs(None, "missing", default_basis, f"no seeded {metric} row for {d.day}")


def _day_obs(d: _DayData, profile: bs.Profile) -> dict[str, Obs]:
    """The per-day rows of the factor table (design §A); no weekly keys."""

    s = d.seeded
    out: dict[str, Obs] = {}
    for key, (metric, basis) in _SEEDED_AS_IS.items():
        out[key] = _seeded_obs(d, metric, basis)

    if "vo2_max" in s:
        vo2 = float(s["vo2_max"])
        pct = _fitness_pct(vo2, profile.age, profile.sex)
        out["fitness_pct"] = Obs(
            pct, "derived", d.sources.get("vo2_max", "apple_watch"),
            f"VO2max {vo2:g} → ~{pct:.0f}th percentile, {profile.sex} "
            f"{_decade_label(profile.age)} (coarse norms, ±10)")
    else:
        out["fitness_pct"] = Obs(None, "missing", "apple_watch", f"no seeded vo2_max row for {d.day}")

    if "daytime_light_minutes" in s:
        out["day_light_min"] = _seeded_obs(d, "daytime_light_minutes", "phone")
    elif d.covered:
        out["day_light_min"] = Obs(
            _daylight_fallback(d), "derived", "glasses",
            "outdoor minutes 08:00–18:00 as a lower bound for bright light — "
            "no lux from the camera (coarse)")
    else:
        out["day_light_min"] = Obs(
            None, "missing", "phone", f"no seeded daytime_light_minutes row and no episodes on {d.day}")

    if "evening_light_ok" in s:
        ok = float(s["evening_light_ok"])
        lux = 1.0 if ok == 1.0 else 10.0
        out["night_light_lux"] = Obs(
            lux, "derived", d.sources.get("evening_light_ok", "phone"),
            f"proxy: evening_light_ok={ok:g} → {lux:g} lx (2-point map)")
    else:
        out["night_light_lux"] = Obs(None, "missing", "phone", f"no seeded evening_light_ok row for {d.day}")

    conversations = d.of_kind("conversation")
    if d.covered:
        conv_min = sum(e.duration_s for e in conversations) / 60.0
        people = min(len(conversations), 5)
        index = 100.0 * (0.6 * min(conv_min, 60.0) / 60.0 + 0.4 * people / 5)
        out["social_index"] = Obs(
            index, "derived", "glasses",
            f"{conv_min:.0f} conversation min over {len(conversations)} encounter(s); "
            "breadth = encounters capped at 5 (no identities)")
    else:
        out["social_index"] = Obs(
            None, "missing", "glasses", f"no episodes on {d.day} — glasses not worn yet")

    if "purpose_score" in s:
        p = float(s["purpose_score"])
        out["purpose"] = Obs(
            1.0 + (p - 1.0) * 5.0 / 4.0, "derived", d.sources.get("purpose_score", "user"),
            f"purpose_score {p:g}/5 rescaled to the engine's 1–6")
    else:
        out["purpose"] = Obs(None, "missing", "user", f"no seeded purpose_score row for {d.day}")

    meals = d.of_kind("meal")
    typed = [m for m in meals if "food_type" in m.dominant]
    if typed:
        on = sum(1 for m in typed if m.dominant["food_type"] in HEALTHY_FOOD_TYPES)
        out["med_adherence"] = Obs(
            on / len(typed), "live", "glasses",
            f"{on}/{len(typed)} typed meals on-pattern ({len(meals) - len(typed)} untyped ignored; "
            "the §8 scorer counts untyped as off-pattern)")
    else:
        out["med_adherence"] = Obs(None, "missing", "glasses", f"no typed meal seen on {d.day}")

    sightings = d.of_kind("alcohol_sighting")
    if sightings:
        occasions = _clusters(sightings, ALCOHOL_CLUSTER_GAP_S)
        drinks = min(MAX_DRINKS_PER_DAY, occasions)
        detail = (f"{len(sightings)} sighting(s) in {occasions} occasion(s) "
                  "(≤30 min apart = one drink, a floor)")
        if occasions > MAX_DRINKS_PER_DAY:
            detail += (f"; read as {MAX_DRINKS_PER_DAY} — the hazard curve ends there "
                       "and a higher count is a camera re-seeing one table")
        out["alcohol_drinks"] = Obs(float(drinks), "live", "glasses", detail)
    elif "journal_alcohol" in s:
        j = float(s["journal_alcohol"])
        out["alcohol_drinks"] = Obs(
            1.0 if j >= 1.0 else 0.0, "seeded", d.sources.get("journal_alcohol", "whoop"),
            f"whoop journal answer filed on {d.day}: "
            + ("1 → at least one drink (coarse)" if j >= 1.0 else "0 → none"))
    elif d.covered:
        out["alcohol_drinks"] = Obs(
            0.0, "live", "glasses", f"no alcohol in frame over {len(d.episodes)} episode(s)")
    else:
        out["alcohol_drinks"] = Obs(
            None, "missing", "glasses", f"no sightings, no journal row, no episodes on {d.day}")

    if out["smoker"].value is not None:
        out["smoker"] = Obs(
            out["smoker"].value, "seeded", out["smoker"].basis,
            f"whoop journal answer filed on {d.day}")

    if out["pm25"].value is not None:
        out["pm25"] = Obs(
            out["pm25"].value, "seeded", out["pm25"].basis,
            f"{out['pm25'].value:g} µg/m³ from the nearest reference station on {d.day}")
    else:
        out["pm25"] = Obs(None, "missing", "openaq",
                          f"no air reading for {d.day} — AIR_LAT/AIR_LON unset or OpenAQ unreachable")

    if "pvt_rt_z" in s:
        lapses = s.get("pvt_lapses")
        out["rt_z"] = Obs(
            float(s["pvt_rt_z"]), "derived", d.sources.get("pvt_rt_z", "pvt"),
            f"3-min PVT on {d.day}: {float(s['pvt_rt_z']):+.2f} SD vs your own baseline"
            + (f", {int(lapses)} lapse(s)" if lapses is not None else ""))
    else:
        out["rt_z"] = Obs(None, "missing", "pvt", "no PVT today")
    return out


def _week_obs(days: list[_DayData]) -> dict[str, Obs]:
    """``resistance_min_wk``, ``nature_min_wk``, ``sauna_wk`` over the trailing week."""

    covered = [d for d in days if d.covered]
    span = f"{days[0].day}..{days[-1].day}"
    if not covered:
        detail = f"no covered day in {span}"
        return {
            "resistance_min_wk": Obs(None, "missing", "glasses", detail),
            "nature_min_wk": Obs(None, "missing", "glasses", detail),
            "sauna_wk": Obs(None, "missing", "glasses", detail),
        }
    episodes = [e for d in covered for e in d.episodes]
    over = f"over {len(covered)} covered days ({span})"

    resistance = _resistance_minutes(episodes)
    outdoor = [e for e in episodes if e.kind == "outdoor_block"]
    nature = _nature_minutes(episodes)
    untagged = sum(e.duration_s for e in outdoor if "scene" not in e.dominant) / 60.0
    scenes = sorted({e.dominant["scene"] for e in outdoor if _is_nature(e) and "scene" in e.dominant})
    nature_detail = f"{nature:.0f} min in {', '.join(scenes) or 'no tagged nature scene'} {over}"
    if untagged > 0:
        nature_detail += (f"; {untagged:.0f} min outdoors with no scene tag counted as green "
                          "(builder opens outdoor_block on vegetation_visible)")
    sauna_all = [e for e in episodes if e.kind == "sauna_session"]
    cold = sum(1 for e in sauna_all if e.dominant.get("scene") == "cold_plunge")
    sauna = _sauna_count(episodes)
    short = len(sauna_all) - cold - sauna
    sauna_detail = f"{sauna} sessions >19 min {over}"
    if short:
        sauna_detail += f"; {short} shorter session(s) ignored"
    if cold:
        sauna_detail += f"; {cold} cold plunge(s) tracked, not scored"
    return {
        "resistance_min_wk": Obs(
            resistance, "derived", "glasses",
            f"{resistance:.0f} min {over}; all gym minutes counted as resistance — "
            "no lifting/cardio split from the camera"),
        "nature_min_wk": Obs(nature, "live", "glasses", nature_detail),
        "sauna_wk": Obs(float(sauna), "live", "glasses", sauna_detail),
    }


def _leading(d: _DayData, prior: list[_DayData], bedtime_hh: float) -> dict[str, Obs]:
    """Leading indicators for tonight's forecast (never scored as hazards)."""

    out: dict[str, Obs] = {}
    caffeine = d.of_kind("caffeine_sighting")
    if caffeine:
        hours = [_hour_of_day(e.start_t) for e in caffeine]
        out["last_caffeine_hh"] = Obs(
            max(hours), "live", "glasses",
            f"last of {len(caffeine)} sighting(s) at {_fmt_hour(max(hours))}")
    else:
        out["last_caffeine_hh"] = Obs(
            None, "missing", "glasses",
            "no caffeine sighting today" if d.covered else "no episodes today")

    if d.covered:
        t0, t1 = (_local(d.day, h) for h in NIGHT_SCREEN_WINDOW)
        screens = d.of_kind("screen_block")
        minutes = sum(_overlap_minutes(e, t0, t1) for e in screens)
        out["night_screen_min"] = Obs(
            minutes, "live", "glasses",
            f"{minutes:.0f} min of screen inside 22:00–05:00 tonight "
            f"({len(screens)} screen block(s) seen; blocks before 05:00 belong to last night)")
    else:
        out["night_screen_min"] = Obs(None, "missing", "glasses", "no episodes today")

    habits = [h for h in (_bed_h(p.seeded) for p in prior) if h is not None]
    if len(habits) >= MIN_HABIT_ROWS:
        habit = _lower_median(habits)
        out["planned_bed_shift_min"] = Obs(
            (bedtime_hh - habit) * 60.0, "derived", "whoop",
            f"tonight {_fmt_hour(bedtime_hh)} vs habit {_fmt_hour(habit)} "
            f"(lower median of {len(habits)} prior nights)")
    else:
        out["planned_bed_shift_min"] = Obs(
            0.0, "missing", "whoop", f"fewer than {MIN_HABIT_ROWS} prior nights with a bed_time")
    return out


def _ledger_row(d: _DayData, profile: bs.Profile) -> dict[str, float | None]:
    """One ``weekly_ledger`` row from the same derivations as the factors."""

    obs = _day_obs(d, profile)
    return {
        "nature_min_today": _nature_minutes(d.episodes),
        "resistance_min_today": _resistance_minutes(d.episodes),
        "sauna_today": _sauna_count(d.episodes),
        "steps": obs["steps"].value,
        "day_light_min": obs["day_light_min"].value,
        "vilpa_min": obs["vilpa_min"].value,
        "social_index": obs["social_index"].value,
    }


def _history(days: list[_DayData], profile: bs.Profile) -> dict | None:
    """``attribute`` kwargs: late caffeine seen by the glasses -> same-night sleep."""

    cutoff = profile.targets()["caffeine_cutoff_h_before_bed"]
    exposure, outcome, strain, weekday = [], [], [], []
    for d in days:
        if not d.covered or "sleep_hours" not in d.seeded or "strain" not in d.seeded:
            continue
        bed = _bed_h(d.seeded)
        if bed is None:
            bed = DEFAULT_BEDTIME_H
        late = any(_hour_of_day(e.start_t) > bed - cutoff for e in d.of_kind("caffeine_sighting"))
        exposure.append(1.0 if late else 0.0)
        outcome.append(float(d.seeded["sleep_hours"]))
        strain.append(float(d.seeded["strain"]))
        weekday.append(date.fromisoformat(d.day).weekday())
    if not outcome:
        return None
    return {
        "exposure": np.array(exposure),
        "outcome": np.array(outcome),
        "covariates": np.array(strain),
        "weekday": np.array(weekday),
        "prior_beta": CAFFEINE_PRIOR[0],
        "prior_se": CAFFEINE_PRIOR[1],
        "exposure_name": "late caffeine seen by the glasses (0/1)",
        "outcome_name": "sleep hours that night",
    }


# -- experience, the two currencies ----------------------------------------


def _pvt(d: _DayData) -> dict | None:
    """``utility_today(pvt=...)`` from the day's PVT rows, or ``None`` with no test."""

    if "pvt_rt_z" not in d.seeded:
        return None
    pvt: dict[str, float] = {"rt_z": float(d.seeded["pvt_rt_z"])}
    if "pvt_lapses" in d.seeded:
        pvt["lapses"] = float(d.seeded["pvt_lapses"])
    if "pvt_rt_ms" in d.seeded:
        pvt["rt_ms_median"] = float(d.seeded["pvt_rt_ms"])
    return pvt


def _check(d: _DayData) -> dict | None:
    """``utility_today(check=...)`` from the three-tap rows, or ``None`` with no taps."""

    check = {key: float(d.seeded[metric]) for metric, key in _CHECK_METRICS.items()
             if metric in d.seeded}
    return check or None


def _utility(d: _DayData) -> dict | None:
    """``utility_today`` for ``d``, or ``None`` when nothing was measured.

    The engine always has the pain/illness component (0.10 of the weight) so it
    would happily return 1.0 for a day with no PVT, no check and no recovery
    score. That is an invented number: a day with no evidence has no utility.
    """

    pvt, check = _pvt(d), _check(d)
    recovery = d.seeded.get("recovery_score")
    if pvt is None and check is None and recovery is None:
        return None
    return bs.utility_today(pvt=pvt, check=check,
                            recovery_score=None if recovery is None else float(recovery))


def _utility_mean(days: list[_DayData]) -> tuple[float | None, int]:
    """Mean utility over ``days`` and how many had one.

    ``None`` until :data:`MIN_UTILITY_DAYS` prior days carry a utility -- below
    that a 30-day mean is one or two days wearing a 30-day label.
    """

    values = [u["utility"] for u in (_utility(d) for d in days) if u is not None]
    if len(values) < MIN_UTILITY_DAYS:
        return None, len(values)
    return float(np.mean(values)), len(values)


# -- narrator --------------------------------------------------------------


def _drivers(d: _DayData, obs: dict[str, Obs], cutoff_h: float,
             bed_shift_min: float | None) -> dict[str, bool]:
    """The six ``annotate_week`` driver flags for one day (rules: :data:`DRIVER_RULES`).

    Each flag is ``False`` unless the day has evidence *for* it: an unmeasured
    day never counts as a clean day on a driver it could not see. Absent
    measurements therefore keep a day out of the "with" arm of a contrast without
    inventing a clean day, which is what "scored at nothing" means here.
    """

    light = obs["day_light_min"].value
    social = obs["social_index"].value
    drinks = obs["alcohol_drinks"].value
    screens = obs["night_screen_min"].value
    last_caffeine = obs["last_caffeine_hh"].value
    bed = _bed_h(d.seeded)
    late_floor = (DEFAULT_BEDTIME_H if bed is None else bed) - cutoff_h
    return {
        "caffeine_late": bool(
            (last_caffeine is not None and last_caffeine > late_floor)
            or float(d.seeded.get("journal_caffeine_late") or 0.0) >= 1.0),
        "alcohol": drinks is not None and drinks >= 1.0,
        "night_screen": screens is not None and screens >= NIGHT_SCREEN_MIN_MIN,
        "late_bed": bed_shift_min is not None and bed_shift_min >= LATE_BED_MIN_SHIFT,
        "no_daylight": light is not None and light <= NO_DAYLIGHT_MAX_MIN,
        "isolated": social is not None and social < ISOLATED_MAX_INDEX,
    }


def _week_table(days: list[_DayData], data: dict[str, _DayData],
                profile: bs.Profile) -> list[dict]:
    """``annotate_week`` rows for ``days`` (oldest first), one per day.

    ``hours`` is that day's own ``score_day`` over that day's own observations --
    the weekly factors included, read over *that* day's trailing 7 days -- so an
    arrow on the chart moves the same number the day's own ledger card shows.
    Days whose trailing week runs off the loaded range are scored over the part
    that is loaded; ``data`` holds 60 days, so only the oldest rows can be short.

    The bed-time habit is built forward through the week, so a day is only "late"
    against nights that preceded it, never against its own future.
    """

    cutoff = float(profile.targets()["caffeine_cutoff_h_before_bed"])
    habits: list[float] = []
    rows: list[dict] = []
    for d in days:
        obs = _day_obs(d, profile)
        trail = [data[t] for t in Scorer.week_days(d.day) if t in data]
        obs.update(_week_obs(trail or [d]))
        obs.update(_leading(d, [], profile.bedtime_hh))
        bed = _bed_h(d.seeded)
        shift = None
        if bed is not None and len(habits) >= MIN_HABIT_ROWS:
            shift = (bed - _lower_median(habits)) * 60.0
        if bed is not None:
            habits.append(bed)
        measured = {k: o.value for k, o in obs.items() if o.value is not None}
        rows.append({
            "day": d.day,
            "hours": round(bs.score_day(measured, profile).hours_today, 2),
            "sleep": d.seeded.get("sleep_hours"),
            "rec": d.seeded.get("recovery_score"),
            "sri": d.seeded.get("sleep_regularity_sri"),
            "drivers": _drivers(d, obs, cutoff, shift),
        })
    return rows


# -- adherence -------------------------------------------------------------


def _adherence_state(d: _DayData) -> dict[str, list[float]] | None:
    """``Adherence`` Beta counts from ``adherence_<lever>_a`` / ``_b`` seeded rows.

    ``None`` when no such row exists: with nothing learned about this person,
    "you do this 71 % of the time" would be a number nobody measured.
    """

    state: dict[str, list[float]] = {}
    for metric, value in d.seeded.items():
        if not metric.startswith(ADHERENCE_PREFIX) or metric[-2:] not in ("_a", "_b"):
            continue
        key = metric[len(ADHERENCE_PREFIX):-2]
        pair = state.setdefault(key, [1.0, 1.0])
        pair[0 if metric.endswith("_a") else 1] = float(value)
    return state or None


# -- pins -------------------------------------------------------------------


def _engine_episode(e: Episode) -> dict | None:
    """An engine-shaped episode dict for ``pins_from_episodes`` (design §D), or None."""

    common = {
        "start_hh": _hour_of_day(e.start_t),
        "minutes": e.duration_s / 60.0,
        "scene": e.dominant.get("scene"),
        "label": None,
        # Frames live 90 s in RAM and are only reachable per decision.
        "frame_url": None,
    }
    if e.kind == "meal":
        label = str(e.dominant.get("food_type", "untyped")).replace("_", " ")
        return {**common, "type": "meal", "label": label}
    if e.kind == "outdoor_block":
        return {**common, "type": "outdoor_block", "scene": e.dominant.get("scene", UNTAGGED_SCENE)}
    if e.kind in ("conversation", "screen_block", "caffeine_sighting", "alcohol_sighting"):
        return {**common, "type": e.kind}
    if e.kind == "sauna_session" and _is_sauna(e):
        return {**common, "type": "sauna"}
    return None  # gym_session has no engine pin type; short/cold sauna rows must not be "counted"


def _reconcile_pins(pins: list[dict], engine_eps: list[dict], profile: bs.Profile,
                    light_source: str) -> list[dict]:
    """Structural field edits after ``pins_from_episodes`` (matched positionally).

    The engine emits pins in input order and skips only a screen_block outside
    22:00-05:00, so the same filter gives the parallel list.
    """

    pinned = [e for e in engine_eps
              if e["type"] != "screen_block" or e["start_hh"] >= 22 or e["start_hh"] < 5]
    cutoff = profile.targets()["caffeine_cutoff_h_before_bed"]
    bed = _fmt_hour(profile.bedtime_hh)
    for pin, e in zip(pins, pinned):
        if e["type"] == "caffeine_sighting":
            late = e["start_hh"] > profile.bedtime_hh - cutoff
            pin["kind"] = "debit" if late else "credit"
            pin["effect"] = (
                f"inside your {cutoff:g} h cutoff (bed {bed}) — ~−1 h of sleep tonight"
                if late else f"outside your {cutoff:g} h cutoff — fine")
        elif e["type"] == "outdoor_block" and light_source == "seeded":
            green = e["scene"] in NATURE_SCENES or e["scene"] == UNTAGGED_SCENE
            pin["effect"] = ((f"nature +{e['minutes']:.0f} min this week · " if green else "")
                             + "bright light already counted by the phone")
    return pins


# -- serialisation ----------------------------------------------------------


def _effect_dict(e: bs.Effect) -> dict:
    """``Effect`` as JSON-safe fields (``beta``/``ci`` are NaN for n < 14)."""

    def num(v: float) -> float | None:
        return None if v != v else float(v)

    return {
        "exposure": e.exposure,
        "outcome": e.outcome,
        "beta": num(e.beta),
        "ci": [num(e.ci[0]), num(e.ci[1])],
        "n": int(e.n),
        "blended_beta": float(e.blended_beta),
        "note": e.note,
    }


def _jsonable(x):
    """nan/inf -> None, -0.0 -> 0.0, tuple -> list, numpy scalars -> Python, recursively."""

    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return 0.0 if x == 0.0 else x
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_jsonable(v) for v in x]
    return x


def profile_from_settings(settings: Settings, bedtime_hh: float) -> bs.Profile:
    """The engine profile from ``PROFILE_*`` settings plus the day's bedtime."""

    return bs.Profile(
        age=settings.profile_age,
        sex=settings.profile_sex.strip().upper()[:1] or "M",
        goal=settings.profile_goal,
        cyp1a2_slow=settings.profile_cyp1a2_slow,
        bedtime_hh=bedtime_hh,
    )


# -- entry point ------------------------------------------------------------


def healthspan_for_day(db: Database, settings: Settings, day: str, *,
                       now_t: float | None = None) -> dict:
    """The ``/api/healthspan`` payload for ``day`` (design §C).

    Raises ``ValueError`` on a malformed ``day``; the route maps it to 400.
    ``now_t`` only sets ``as_of_hh`` when it falls inside ``day``.
    """

    date.fromisoformat(day)
    trail7 = Scorer.week_days(day)
    wtd = _iso_week_to_date(day)
    prior7 = Scorer.week_days(day, 8)[:-1]
    history_days = Scorer.week_days(day, ATTRIBUTION_DAYS + 1)[:-1]
    # 13 days, not 7: the narrator scores each of the trailing 7 days over *its*
    # own trailing week, and the oldest of those reaches back 6 days further.
    # Without them the chart's first bars would read their own nature and sauna
    # weeks as uncovered and sit lower than the same day's own ledger card.
    data = _load(db, sorted(set(Scorer.week_days(day, 2 * len(trail7) - 1)) | set(wtd)),
                 history_days)
    today = data[day]

    bed = _bed_h(today.seeded)
    if bed is not None:
        bedtime = Obs(bed, "seeded", today.sources.get("bed_time", "whoop"),
                      f"bed_time row {day} → {_fmt_hour(bed)}")
    else:
        bedtime = Obs(DEFAULT_BEDTIME_H, "missing", "assumed",
                      f"assumed {_fmt_hour(DEFAULT_BEDTIME_H)} (no seeded bed_time row)")
    profile = profile_from_settings(settings, bedtime.value)

    all_obs = _day_obs(today, profile)
    all_obs.update(_week_obs([data[d] for d in trail7]))
    all_obs.update(_leading(today, [data[d] for d in prior7], profile.bedtime_hh))
    obs = {k: o.value for k, o in all_obs.items() if o.value is not None}

    day_score = bs.score_day(obs, profile)
    ledger = bs.weekly_ledger([_ledger_row(data[d], profile) for d in wtd], profile)

    sleep_rows = [(d, data[d].seeded["sleep_hours"]) for d in prior7
                  if "sleep_hours" in data[d].seeded]
    if len(sleep_rows) >= MIN_HABIT_ROWS:
        baseline = Obs(
            float(np.mean([v for _, v in sleep_rows])), "derived",
            today.sources.get("sleep_hours", "whoop"),
            f"mean of {len(sleep_rows)} prior nights ({sleep_rows[0][0]}..{sleep_rows[-1][0]}), "
            "today excluded")
    else:
        target = float(profile.targets()["sleep_hours"])
        baseline = Obs(target, "missing", "assumed",
                       f"fewer than {MIN_HABIT_ROWS} prior nights — profile target {target:g} h")
    forecast = bs.forecast_tonight(obs, baseline.value, profile)

    all_levers = bs.levers(obs, profile, top=len(bs.FACTORS) + 1)
    timed = [lv for lv in all_levers if lv.time_min > 0][:5]
    free = sorted((lv for lv in all_levers if lv.time_min <= 0 and lv.key not in STATE_MARKERS),
                  key=lambda lv: -lv.hours_gain)[:3]

    history = _history([data[d] for d in history_days], profile)
    effects = [bs.attribute(**history)] if history else []
    tips = bs.insights(day_score, ledger, forecast, timed, effects)
    payload = bs.to_payload(day_score, ledger, forecast, timed, tips)

    for row in payload["factors"]:
        o = all_obs[row["key"]]
        row.update(provenance=o.source, basis=o.basis, detail=o.detail)

    engine_eps = [ep for ep in (_engine_episode(e) for e in today.episodes) if ep is not None]
    all_pins = _reconcile_pins(bs.pins_from_episodes(engine_eps, day_score, forecast), engine_eps,
                               profile, all_obs["day_light_min"].source)
    # Today shows at most MAX_PINS_TODAY of the merged episodes; the count says
    # how many there were so the strip can offer the full day instead of
    # silently dropping evidence.
    pins = all_pins[-MAX_PINS_TODAY:]

    # Fully-lived hours today, and the future healthy years they weight.
    experience = _utility(today)
    utility_days = Scorer.week_days(day, UTILITY_MEAN_DAYS + 1)[:-1]
    u_mean, u_days = _utility_mean([data[d] for d in utility_days if d in data])
    currencies = None
    if experience is not None:
        currencies = bs.two_currencies(day_score, experience["utility"], u_mean)
        currencies["utility_mean_30d"] = u_mean
        currencies["utility_days"] = u_days

    week_table = _week_table([data[d] for d in trail7], data, profile)
    annotations = bs.annotate_week(week_table)

    adherence_state = _adherence_state(today)
    levers_personalized = None
    if adherence_state is not None:
        adherence = bs.Adherence(adherence_state)
        levers_personalized = [dict(lv.__dict__, p_adherence=round(adherence.expected(lv.key), 2))
                               for lv in adherence.rank(timed, seed=ADHERENCE_SEED)]

    provenance = {k: o for k, o in all_obs.items()}
    provenance["bedtime_hh"] = bedtime
    provenance["baseline_sleep_h"] = baseline
    factor_days = sorted(set(trail7) | set(wtd))
    payload.update({
        "day": day,
        "as_of_hh": (round(_hour_of_day(now_t), 2)
                     if now_t is not None and day_key(now_t) == day else None),
        "engine": ENGINE,
        "measured": {"count": sum(1 for f in payload["factors"] if f["measured"]),
                     "total": len(payload["factors"])},
        "levers_free": [lv.__dict__ for lv in free],
        "levers_personalized": levers_personalized,
        "pins": pins,
        "pins_total": len(all_pins),
        "experience": experience,
        "currencies": currencies,
        "week_table": week_table,
        "annotations": annotations,
        "narrator_prompts": [bs.narrator_prompt(a) for a in annotations],
        "driver_rules": dict(DRIVER_RULES),
        "observations": obs,
        "provenance": {k: {"source": o.source, "basis": o.basis, "detail": o.detail}
                       for k, o in provenance.items()},
        "effects": [_effect_dict(e) for e in effects],
        "profile": {
            "age": profile.age, "sex": profile.sex, "goal": profile.goal,
            "cyp1a2_slow": profile.cyp1a2_slow, "height_m": settings.profile_height_m,
            "bedtime_hh": profile.bedtime_hh, "bedtime_source": bedtime.source,
        },
        "baseline_sleep_h": baseline.value,
        "window": {
            "ledger_days": wtd,
            "factor_days": trail7,
            "uncovered_days": [d for d in factor_days if not data[d].covered],
            "days_elapsed": len(wtd),
        },
        "conventions": list(CONVENTIONS),
    })
    return _jsonable(payload)


# -- week view and registry -------------------------------------------------

#: Largest ``?days=N`` the week route accepts (a month of bars).
MAX_WEEK_DAYS = 31
#: Keys a day keeps in the trailing-days list: enough for the bar chart, the
#: table and the narrator, without 31 copies of every factor row and pin.
LITE_KEYS = (
    "day", "overall", "layers", "hours_today", "hours_ci", "years_delta",
    "experience", "currencies", "forecast", "observations", "measured",
)


def lite_payload(full: dict) -> dict:
    """One day trimmed to what the week chart and table render."""

    lite = {k: full[k] for k in LITE_KEYS if k in full}
    lite["drivers"] = next((r["drivers"] for r in full.get("week_table") or []
                            if r["day"] == full["day"]), {})
    return lite


def healthspan_week(db: Database, settings: Settings, day: str, days: int, *,
                    now_t: float | None = None) -> dict:
    """``{"days": [lite payload per day, oldest first], "today": full payload}``.

    ``days`` is clamped to 1..:data:`MAX_WEEK_DAYS`; each day is scored
    independently, exactly as ``?day=`` would score it.
    """

    date.fromisoformat(day)
    n = max(1, min(MAX_WEEK_DAYS, int(days)))
    window = Scorer.week_days(day, n)
    today = healthspan_for_day(db, settings, day, now_t=now_t)
    return {
        "day": day,
        "days": [lite_payload(healthspan_for_day(db, settings, d) if d != day else today)
                 for d in window],
        "today": today,
    }


def healthspan_registry() -> dict:
    """``export_registry()`` plus the adapter's own provenance rules.

    The How-it-is-scored page reads this one object: the engine says what each
    factor's curve and evidence grade are, and ``adapter`` says which app source
    feeds the dose -- nobody retypes either.
    """

    registry = bs.export_registry()
    rules: dict[str, dict[str, str]] = {}
    for factor in bs.FACTORS.values():
        rules[factor.key] = {"source": _ADAPTER_SOURCES.get(factor.key, "unmapped"),
                             "bonus": "earns hours, excluded from the layer score"
                                      if factor.bonus else ""}
    registry["adapter"] = {
        "engine": ENGINE,
        "provenance_labels": {
            "live": "a direct sum or count over the glasses' episodes for that day",
            "seeded": "an integration row used as-is; basis carries the row's own source",
            "derived": "a proxy or conversion of either",
            "missing": "no measurement at all — imputed at the population reference, earns nothing",
        },
        "factor_sources": rules,
        "leading_indicators": {
            "last_caffeine_hh": "glasses caffeine_sighting, latest of the day",
            "night_screen_min": "glasses screen_block overlap with 22:00–05:00",
            "planned_bed_shift_min": "seeded bed_time vs the lower median of the prior nights",
        },
        "driver_rules": dict(DRIVER_RULES),
        "state_markers": sorted(STATE_MARKERS),
        "conventions": list(CONVENTIONS),
    }
    return _jsonable(registry)


#: Which app source feeds each engine factor's dose (``healthspan_registry``).
#: Every key of ``bs.FACTORS`` appears here; a factor the engine gains and the
#: adapter has not mapped reads "unmapped", which ``tests/test_healthspan.py``
#: fails on rather than letting the page show a blank line.
_ADAPTER_SOURCES: dict[str, str] = {
    "steps": "seeded steps row (phone)",
    "vilpa_min": "seeded vilpa_minutes row (phone)",
    "resistance_min_wk": "glasses gym_session minutes over the trailing 7 days",
    "fitness_pct": "seeded vo2_max row → percentile for sex and decade (coarse, ±10)",
    "gait_speed": "seeded gait_speed_ms row (phone)",
    "sleep_hours": "seeded sleep_hours row (WHOOP)",
    "sri": "seeded sleep_regularity_sri row (WHOOP)",
    "day_light_min": "seeded daytime_light_minutes row (phone), else glasses outdoor "
                     "minutes 08:00–18:00 as a lower bound",
    "night_light_lux": "seeded evening_light_ok row → 1 or 10 lx (2-point map)",
    "social_index": "glasses conversation minutes and encounter breadth (no identities)",
    "purpose": "seeded purpose_score row (entered), rescaled 1–5 → 1–6",
    "nature_min_wk": "glasses outdoor_block minutes in green scenes over the trailing 7 days",
    "pm25": "OpenAQ nearest-station reading, written as the day's pm25 row",
    "noise_night_db": "seeded night_noise_db row (phone)",
    "med_adherence": "glasses typed meals on-pattern; untyped meals ignored",
    "alcohol_drinks": "glasses alcohol_sighting occasions (≤30 min apart = one), "
                      "else the WHOOP journal answer",
    "smoker": "seeded journal_nicotine row (WHOOP)",
    "sauna_wk": "glasses sauna_session rows over 19 min, cold plunge excluded",
    "rt_z": "seeded pvt_rt_z row written by POST /api/pvt (entered)",
    "recovery_ratio": "seeded hrv_rmssd_ratio row (WHOOP)",
}
