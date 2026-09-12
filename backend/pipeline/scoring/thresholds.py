"""SPEC §8 reference thresholds, expressed as data.

Every row of the §8 table becomes one :class:`MetricSpec` in :data:`THRESHOLDS`,
carrying the target string, the evidence grade, the citation, whether the metric
is live (derived from episodes) or seeded (§7), and a ``score_fn`` that maps a
raw value onto 0..1.

**The scoring model here is deliberately rough**: straight lines, bands and steps
over the §8 numbers, nothing fitted, no hazard ratios. It exists so the dashboard
has an honest, legible, deterministic number per metric and a rollup; it is meant
to be replaced wholesale by anyone who wants a real model. Every spec carries the
threshold it was built from, so a replacement can start from the same table.

Conventions:

* ``score_fn(None) == 0.0`` -- missing data scores zero and is annotated
  ``"no data"`` by the scorer, never silently treated as a pass.
* every ``score_fn`` returns a value in ``[0.0, 1.0]`` for any finite input.
* ``metric`` ids are snake_case and are the primary key used by
  ``Score.metric``, ``SeededRow.metric`` and the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from ..models import HEALTHY_FOOD_TYPES

__all__ = [
    "ScoreFn",
    "MetricSpec",
    "THRESHOLDS",
    "GRADE_WEIGHTS",
    "MEDITERRANEAN_FOOD_TYPES",
    "HEALTHY_FOOD_TYPES",
    "DEFAULT_BEDTIME_H",
    "CAFFEINE_CUTOFF_LEAD_H",
    "by_period",
    "by_source",
]

ScoreFn = Callable[[float | None], float]

#: Evidence-quality weights for the rollup (SPEC §8 "Grade is evidence quality").
GRADE_WEIGHTS: dict[str, float] = {"A": 1.0, "B": 0.7, "C": 0.3}

#: ``food_type`` values counted as "on-pattern" for PREDIMED-style scoring.
#: One name for one idea: the family itself lives in ``models`` next to the
#: enum it partitions (mirrored from A's ``longevity.ai_fields``), and this is
#: the §8-facing alias. Everything outside it -- ``red_meat``, ``processed``,
#: ``sweets``, ``burger``, ``none`` -- is off-pattern.
MEDITERRANEAN_FOOD_TYPES = HEALTHY_FOOD_TYPES

#: Bedtime assumed when no seeded ``bed_time`` row exists for the day, in hours
#: after local midnight. 23:00 - 9 h = a 14:00 caffeine cutoff.
DEFAULT_BEDTIME_H = 23.0

#: SPEC §8: "system cutoff = bedtime - 9 h (CYP1A2 slow: -12 h)".
CAFFEINE_CUTOFF_LEAD_H = 9.0


# -- score function builders ---------------------------------------------
#
# All four are total: they accept None and any float and return 0..1.


def _clamp01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def ramp(full: float, zero: float = 0.0) -> ScoreFn:
    """Linear 0.0 at ``zero`` -> 1.0 at ``full``, flat outside.

    Works in either direction: ``ramp(6, 12)`` scores 1.0 at 6 h or less and
    0.0 at 12 h or more.
    """

    def fn(value: float | None) -> float:
        if value is None:
            return 0.0
        if full == zero:
            return 1.0 if value >= full else 0.0
        return _clamp01((value - zero) / (full - zero))

    return fn


def band(lo: float, hi: float, zero_lo: float, zero_hi: float) -> ScoreFn:
    """U-shaped: 1.0 inside ``[lo, hi]``, falling to 0.0 at the outer bounds."""

    def fn(value: float | None) -> float:
        if value is None:
            return 0.0
        if lo <= value <= hi:
            return 1.0
        if value < lo:
            return _clamp01((value - zero_lo) / (lo - zero_lo))
        return _clamp01((zero_hi - value) / (zero_hi - hi))

    return fn


def step(threshold: float, below: float = 0.0) -> ScoreFn:
    """1.0 at or above ``threshold``, ``below`` under it."""

    def fn(value: float | None) -> float:
        if value is None:
            return 0.0
        return 1.0 if value >= threshold else _clamp01(below)

    return fn


def none_is_good(hit: float) -> ScoreFn:
    """For counts of a thing you want zero of: 0 -> 1.0, any -> ``hit``."""

    def fn(value: float | None) -> float:
        if value is None:
            return 0.0
        return 1.0 if value <= 0 else _clamp01(hit)

    return fn


def flat_if_present(level: float) -> ScoreFn:
    """A fixed near-zero credit when the behaviour happened at all.

    Only used for cold plunge, which SPEC §8 says to log and score near zero.
    """

    def fn(value: float | None) -> float:
        if value is None or value <= 0:
            return 0.0
        return _clamp01(level)

    return fn


@dataclass(frozen=True)
class MetricSpec:
    """One §8 row: what it is, where the number comes from, how it scores."""

    metric: str
    layer: str
    label: str
    unit: str
    #: The §8 target column, near-verbatim. Copied onto ``Score.target``.
    target_text: str
    #: §8 evidence grade, A strongest.
    grade: Literal["A", "B", "C"]
    #: §7 provenance. ``live`` = aggregated from episodes; ``seeded`` = wearable
    #: / phone integration rows.
    source: Literal["live", "seeded"]
    period: Literal["daily", "weekly"]
    citation: str
    score_fn: ScoreFn
    #: Optional standing caveat, merged into ``Score.note`` by the scorer.
    note: str | None = None

    @property
    def weight(self) -> float:
        return GRADE_WEIGHTS.get(self.grade, 0.5)

    def evaluate(self, value: float | None) -> tuple[float, str | None]:
        """``(score, note)``. ``None`` in means ``(0.0, "no data")``."""

        if value is None:
            return 0.0, "no data"
        return _clamp01(self.score_fn(value)), self.note


def _spec(**kwargs: object) -> MetricSpec:
    return MetricSpec(**kwargs)  # type: ignore[arg-type]


_SPECS: list[MetricSpec] = [
    # -- LIVE: derived from episodes (SPEC §7, §10) ----------------------
    _spec(
        metric="nature_minutes_weekly",
        layer="nature",
        label="Nature dose",
        unit="min/week",
        target_text="≥120 min/week; peak 200–300 min; pattern doesn't matter",
        grade="B",
        source="live",
        period="weekly",
        citation="White 2019 Sci Rep",
        score_fn=ramp(full=120.0),
        note="outdoor_block minutes; credit caps at the 300 min peak",
    ),
    _spec(
        metric="social_episodes_daily",
        layer="social",
        label="Social integration",
        unit="conversations/day",
        target_text="≥3 conversation episodes/day (stronger ties → survival OR 1.50)",
        grade="A",
        source="live",
        period="daily",
        citation="Holt-Lunstad 2010 / 2015",
        score_fn=ramp(full=3.0),
        note="proxy: sustained people_present episodes, not tie quality",
    ),
    _spec(
        metric="screen_hours_daily",
        layer="stress",
        label="Screen hours",
        unit="h/day",
        target_text="≤6 h/day screen time (proxy for the ≥55 h/week work-hours risk)",
        grade="A",
        source="live",
        period="daily",
        citation="WHO/ILO 2021 (work hours)",
        score_fn=ramp(full=6.0, zero=12.0),
        note="proxy: screen hours, not clocked work hours",
    ),
    _spec(
        metric="work_hours_weekly",
        layer="stress",
        label="Work hours",
        unit="h/week",
        target_text="≥55 h/week associated with higher stroke/IHD mortality",
        grade="A",
        source="live",
        period="weekly",
        citation="WHO/ILO 2021",
        score_fn=ramp(full=40.0, zero=55.0),
        note="proxy: screen hours × 7, not clocked work hours",
    ),
    _spec(
        metric="meals_logged_daily",
        layer="diet",
        label="Meals logged",
        unit="meals/day",
        target_text="≥2 meals observed/day (coverage check for diet pattern)",
        grade="C",
        source="live",
        period="daily",
        citation="coverage metric, not an outcome",
        score_fn=ramp(full=2.0),
        note="coverage of the camera, not a health target",
    ),
    _spec(
        metric="diet_pattern_daily",
        layer="diet",
        label="Diet pattern",
        unit="share on-pattern",
        target_text="Mediterranean pattern ≈ 30% fewer major CV events",
        grade="A",
        source="live",
        period="daily",
        citation="PREDIMED (republished 2018)",
        score_fn=ramp(full=1.0),
        note="share of observed meals with an on-pattern food_type",
    ),
    _spec(
        metric="caffeine_cutoff_daily",
        layer="diet",
        label="Caffeine cutoff",
        unit="late sightings",
        target_text="No caffeine after bedtime − 9 h (6 h before bed cut sleep >1 h)",
        grade="B",
        source="live",
        period="daily",
        citation="Drake 2013 J Clin Sleep Med",
        score_fn=none_is_good(0.0),
        note=None,  # the scorer writes the cutoff time it actually used
    ),
    _spec(
        metric="alcohol_daily",
        layer="diet",
        label="Alcohol",
        unit="sightings",
        target_text="No safe level; nightly HRV drop visible the same night",
        grade="A",
        source="live",
        period="daily",
        citation="Zhao 2023 JAMA Netw Open",
        score_fn=none_is_good(0.2),
        note="§8: no safe level — any sighting scores 0.2, not 0",
    ),
    _spec(
        metric="caffeine_sightings_weekly",
        layer="diet",
        label="Caffeine sightings",
        unit="sightings/week",
        target_text="fewer than last week (persona cut-down goal)",
        grade="B",
        source="live",
        period="weekly",
        citation="persona cut-down goal",
        score_fn=ramp(full=7.0, zero=21.0),
    ),
    _spec(
        metric="alcohol_sightings_weekly",
        layer="diet",
        label="Alcohol sightings",
        unit="sightings/week",
        target_text="fewer than last week (persona cut-down goal)",
        grade="B",
        source="live",
        period="weekly",
        citation="persona cut-down goal",
        score_fn=none_is_good(0.2),
    ),
    _spec(
        metric="resistance_sessions_weekly",
        layer="movement",
        label="Resistance training",
        unit="sessions/week",
        target_text="30–60 min/week, 2 sessions; 10–17% lower mortality",
        grade="A",
        source="live",
        period="weekly",
        citation="Momma 2022 BJSM",
        score_fn=ramp(full=2.0),
        note="gym_session count; benefit fades above ~130 min/wk (not modelled)",
    ),
    _spec(
        metric="sauna_sessions_weekly",
        layer="heat",
        label="Sauna",
        unit="sessions/week",
        target_text="2–3×/wk moderate benefit; 4–7×/wk ~40% lower mortality; sessions >19 min",
        grade="B",
        source="live",
        period="weekly",
        citation="Laukkanen 2015 JAMA IM — single male cohort",
        score_fn=ramp(full=2.0),
        note="only sessions longer than 19 min are counted",
    ),
    _spec(
        metric="cold_plunge_weekly",
        layer="cold",
        label="Cold plunge",
        unit="sessions/week",
        target_text="No healthspan evidence; log it, score it near zero, say so",
        grade="C",
        source="live",
        period="weekly",
        citation="—",
        score_fn=flat_if_present(0.05),
        note="no healthspan evidence (SPEC §8) — logged, scored near zero",
    ),
    # -- SEEDED: wearable / phone / self-report rows (SPEC §7) -----------
    _spec(
        metric="sleep_hours",
        layer="sleep",
        label="Sleep duration",
        unit="h",
        target_text="7–9 h (U-shaped risk)",
        grade="A",
        source="seeded",
        period="daily",
        citation="Multiple cohorts",
        score_fn=band(lo=7.0, hi=9.0, zero_lo=4.0, zero_hi=12.0),
    ),
    _spec(
        metric="sleep_regularity_sri",
        layer="sleep",
        label="Sleep regularity (SRI)",
        unit="SRI",
        target_text="SRI ≥80 (bed/wake within ±30 min); regularity beat duration",
        grade="A",
        source="seeded",
        period="daily",
        citation="Windred 2024 Sleep (UK Biobank, n=60,977)",
        score_fn=ramp(full=80.0, zero=50.0),
    ),
    _spec(
        metric="steps",
        layer="movement",
        label="Steps",
        unit="steps/day",
        target_text="~7,000/day meaningful; plateau ~8,000–10,000 under 60",
        grade="A",
        source="seeded",
        period="daily",
        citation="Paluch 2022 Lancet Public Health",
        score_fn=ramp(full=7000.0),
    ),
    _spec(
        metric="vilpa_minutes",
        layer="movement",
        label="VILPA",
        unit="min/day",
        target_text="3–4 min/day of vigorous bursts ≈ 26–30% lower mortality",
        grade="A",
        source="seeded",
        period="daily",
        citation="Stamatakis 2022 Nature Medicine",
        score_fn=ramp(full=3.0),
    ),
    _spec(
        metric="gait_speed_ms",
        layer="movement",
        label="Gait speed",
        unit="m/s",
        target_text="≥1.2 m/s good; each +0.1 m/s ≈ 12% lower mortality",
        grade="A",
        source="seeded",
        period="daily",
        citation="Studenski 2011 JAMA",
        score_fn=ramp(full=1.2, zero=0.6),
    ),
    _spec(
        metric="balance_one_leg_s",
        layer="movement",
        label="Balance (one-leg stand)",
        unit="s",
        target_text="10-s one-leg stand; failure → mortality HR 1.84 (ages 51–75)",
        grade="B",
        source="seeded",
        period="daily",
        citation="Araujo 2022 BJSM",
        score_fn=step(10.0, below=0.3),
        note="pass/fail at 10 s — a partial stand still scores 0.3",
    ),
    _spec(
        metric="hrv_rmssd_ratio",
        layer="stress",
        label="HRV recovery",
        unit="7d/60d ln RMSSD",
        target_text="7-day ln RMSSD ≥ 60-day baseline; flag if below by >1 SD for 3+ days",
        grade="B",
        source="seeded",
        period="daily",
        citation="Standard HRV practice",
        score_fn=ramp(full=1.0, zero=0.8),
        note=None,  # the scorer flags ratios below 0.9
    ),
    _spec(
        metric="breathwork_minutes",
        layer="stress",
        label="Breathwork",
        unit="min/day",
        target_text="5 min/day cyclic sighing improves mood, lowers respiratory rate",
        grade="B",
        source="seeded",
        period="daily",
        citation="Balban 2023 Cell Rep Med",
        score_fn=ramp(full=5.0),
    ),
    _spec(
        metric="night_noise_db",
        layer="noise",
        label="Night noise",
        unit="dB Lnight",
        target_text="<45 dB Lnight",
        grade="A",
        source="seeded",
        period="daily",
        citation="WHO 2018",
        score_fn=ramp(full=45.0, zero=60.0),
    ),
    _spec(
        metric="purpose_score",
        layer="purpose",
        label="Life purpose",
        unit="1–5",
        target_text="Lowest vs highest purpose → mortality HR 2.43 (target 5/5)",
        grade="B",
        source="seeded",
        period="daily",
        citation="Alimujiang 2019 JAMA Netw Open",
        score_fn=ramp(full=5.0, zero=1.0),
    ),
    _spec(
        metric="daytime_light_minutes",
        layer="light",
        label="Daytime light",
        unit="min",
        target_text="≥250 lux melanopic daily dose; system target ≥30 min outdoors or bright-band before 10:00",
        grade="A",
        source="seeded",
        period="daily",
        citation="Brown 2022 PLOS Biology; Windred 2024 PNAS",
        score_fn=ramp(full=30.0),
        note="seeded (SPEC §7): absolute lux is not recoverable from an auto-exposed JPEG",
    ),
    _spec(
        metric="evening_light_ok",
        layer="light",
        label="Evening light",
        unit="0/1",
        target_text="≤10 lux melanopic in the 3 h pre-bed",
        grade="A",
        source="seeded",
        period="daily",
        citation="Brown 2022 PLOS Biology",
        score_fn=ramp(full=1.0),
        note="seeded (SPEC §7): out of demo scope on the live side",
    ),
]

THRESHOLDS: dict[str, MetricSpec] = {spec.metric: spec for spec in _SPECS}


def by_period(period: str) -> list[MetricSpec]:
    """Specs whose rollup period is ``daily`` or ``weekly``, in table order."""

    return [s for s in _SPECS if s.period == period]


def by_source(source: str) -> list[MetricSpec]:
    """Specs sourced ``live`` (episodes) or ``seeded`` (integration rows)."""

    return [s for s in _SPECS if s.source == source]
