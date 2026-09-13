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

__all__ = ["healthspan_for_day", "profile_from_settings", "NATURE_SCENES"]

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
#: Tonight's screen window, hours after local midnight of ``day`` (22:00 -> 05:00 next morning).
NIGHT_SCREEN_WINDOW = (22.0, 29.0)
#: Daylight band for the bright-light fallback when no phone row exists (coarse, no solar model).
DAYLIGHT_FALLBACK_WINDOW = (8.0, 18.0)
#: Factors that are state markers, not actions; never offered as levers.
STATE_MARKERS = frozenset({"gait_speed", "recovery_ratio"})
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
}

CONVENTIONS = [
    "Night rows (sleep_hours, sri, hrv_rmssd_ratio, night_noise_db, evening_light_ok, bed_time) "
    "for day D describe the night that starts on D — same row the §8 scorer uses.",
    "Weekly hazard doses use the trailing 7 days; the ledger uses the ISO week to date.",
    "Unmeasured factors are imputed at the population reference and earn nothing.",
    "Untyped meals are excluded from the Mediterranean share (the §8 scorer counts them as off-pattern).",
    "Alcohol sightings within 30 min are one drink; a journal '1' is read as one drink.",
]


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
    reported: dict[str, dict]

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
    reported = db.reported_by_episode()
    return {
        d: _DayData(d, db.list_episodes(d) if d in wanted else [], seeded[d], sources[d], reported)
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

    raw_meals = d.of_kind("meal")
    meals = [m for m in raw_meals if d.reported.get(m.id, {}).get("confirmed") is not False]
    typed = [(m, d.reported.get(m.id, {}).get("food_type") or m.dominant.get("food_type"))
             for m in meals if (d.reported.get(m.id, {}).get("food_type") is not None
                                or "food_type" in m.dominant)]
    if typed:
        on = sum(1 for _, food_type in typed if food_type in HEALTHY_FOOD_TYPES)
        reports = [f"wearer reported: {food_type}" for m, food_type in typed
                   if d.reported.get(m.id, {}).get("food_type") is not None
                   and food_type != m.dominant.get("food_type")]
        reports.extend("wearer reported: not mine" for m in raw_meals
                       if d.reported.get(m.id, {}).get("confirmed") is False)
        out["med_adherence"] = Obs(
            on / len(typed), "live", "glasses + wearer report" if reports else "glasses",
            f"{on}/{len(typed)} typed meals on-pattern ({len(meals) - len(typed)} untyped ignored; "
            "the §8 scorer counts untyped as off-pattern)" + ("; " + "; ".join(reports) if reports else ""))
    else:
        out["med_adherence"] = Obs(None, "missing", "glasses", f"no typed meal seen on {d.day}")

    raw_sightings = d.of_kind("alcohol_sighting")
    sightings = [e for e in raw_sightings if d.reported.get(e.id, {}).get("confirmed") is not False]
    if sightings:
        specified = [e for e in sightings if d.reported.get(e.id, {}).get("count") is not None]
        unspecified = [e for e in sightings if e not in specified]
        occasions = _clusters(unspecified, ALCOHOL_CLUSTER_GAP_S)
        drinks = float(occasions) + sum(float(d.reported[e.id]["count"]) for e in specified)
        reports = [f"wearer reported: {float(d.reported[e.id]['count']):g} drinks" for e in specified
                   if float(d.reported[e.id]["count"]) != 1.0]
        reports.extend("wearer reported: not mine" for e in raw_sightings
                       if d.reported.get(e.id, {}).get("confirmed") is False)
        out["alcohol_drinks"] = Obs(
            drinks, "live", "glasses + wearer report" if reports else "glasses",
            f"{len(unspecified)} sighting(s) in {occasions} occasion(s) "
            "(≤30 min apart = one drink, a floor)" + ("; " + "; ".join(reports) if reports else ""))
    elif raw_sightings:
        out["alcohol_drinks"] = Obs(
            0.0, "live", "glasses + wearer report",
            f"wearer reported: not mine ({len(raw_sightings)} sighting(s) excluded)")
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
    data = _load(db, sorted(set(trail7) | set(wtd)), history_days)
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
    pins = _reconcile_pins(bs.pins_from_episodes(engine_eps, day_score, forecast), engine_eps,
                           profile, all_obs["day_light_min"].source)

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
        "pins": pins,
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
