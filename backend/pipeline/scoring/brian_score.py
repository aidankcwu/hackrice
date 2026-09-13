"""
brian_score.py — Project Brian healthspan scoring engine.

What this is
------------
A dose-response hazard model. Every factor maps a measured dose to a published
relative hazard for all-cause mortality (or the closest hard outcome), shrunk by
evidence grade, capped, discounted for within-layer correlation, and summed in
log-hazard space. The sum converts to a life-expectancy delta with the Gompertz
shift (adult mortality doubles roughly every 8 years, so a proportional hazard
HR shifts the survival curve by ln(HR)/b years, b = ln2/8). A day's share of that
delta is expressed in healthy-life hours — the "microlife" framing
(Spiegelhalter & Blastland, BMJ 2012: a lifelong HR of 1.1 ≈ 1 microlife/day).

It sits on top of WHOOP, not instead of it: WHOOP supplies sleep timing, HRV,
RHR, strain, steps; the glasses supply light, nature, social, gait, noise,
screens, sightings. WHOOP's own recovery score is treated as a physiological
marker (low weight) and as the response variable for attribution, never as a
mortality factor — that avoids double counting.

Honesty rules baked in
----------------------
* Observational HRs are shrunk (A_cohort 0.7, B 0.5, C 0.25) before use.
* Any single factor's log-hazard is capped at ±0.6 (HR 0.55–1.8).
* Correlated factors inside a layer are discounted (1, .6, .4, .3 ...).
* Uncertainty from each HR's 95% CI is propagated to years and hours.
* Leading indicators (caffeine timing, night screens) forecast tomorrow;
  they are not scored as today's mortality hazards.
* Cold plunge is tracked, not scored. Biological age is never claimed.

Dependencies: numpy only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# --------------------------------------------------------------------------- #
# 1. Constants
# --------------------------------------------------------------------------- #

GOMPERTZ_B = math.log(2) / 8.0          # adult mortality doubling time ≈ 8 y
HOURS_PER_YEAR = 8766.0
FACTOR_CAP = 0.6                        # |log-hazard| cap per factor
LAYER_DISCOUNT = [1.0, 0.6, 0.4, 0.3, 0.25, 0.2]

SHRINK = {"A_rct": 1.0, "A_cohort": 0.7, "B": 0.5, "C": 0.25}

# Approximate US remaining life expectancy (years) by age, sex (2022 tables).
# Used only to spread a lifetime delta over the days that remain.
_RLE = {
    "M": {20: 55.7, 30: 46.5, 40: 37.5, 50: 28.9, 60: 21.0, 70: 14.0, 80: 8.3},
    "F": {20: 60.7, 30: 51.3, 40: 41.9, 50: 32.9, 60: 24.3, 70: 16.3, 80: 9.7},
}


def remaining_life_years(age: int, sex: str) -> float:
    table = _RLE["F" if sex.upper().startswith("F") else "M"]
    ages = sorted(table)
    if age <= ages[0]:
        return table[ages[0]] + (ages[0] - age)
    if age >= ages[-1]:
        return max(table[ages[-1]] - (age - ages[-1]) * 0.6, 2.0)
    lo = max(a for a in ages if a <= age)
    hi = min(a for a in ages if a > age)
    t = (age - lo) / (hi - lo)
    return table[lo] + t * (table[hi] - table[lo])


# --------------------------------------------------------------------------- #
# 2. Factor registry — dose → hazard ratio, with evidence grade and source
# --------------------------------------------------------------------------- #

@dataclass
class Factor:
    key: str
    layer: str
    label: str
    unit: str
    points: List[Tuple[float, float]]      # (dose, HR) — HR relative to worst tier
    grade: str                             # A_rct | A_cohort | B | C
    se_log: float                          # SE of ln(HR) at the strongest tier
    ref: float                             # population-typical dose (credit/debit pivot)
    source: str
    higher_is_better: bool = True
    time_cost_min_per_unit: float = 0.0    # minutes of effort per unit of dose
    lever_step: float = 0.0                # feasible daily/weekly increment for levers
    lever_time_min: float = 0.0            # minutes that increment costs
    active_for: Optional[List[str]] = None # goal profiles this applies to (None = all)
    age_min: int = 0
    note: str = ""

    def hr(self, dose: float) -> float:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return float(np.interp(dose, xs, ys))

    def log_hazard(self, dose: float) -> float:
        raw = math.log(self.hr(dose)) * SHRINK[self.grade]
        return max(-FACTOR_CAP, min(FACTOR_CAP, raw))

    def best_dose(self) -> float:
        return min(self.points, key=lambda p: p[1])[0]

    def worst_dose(self) -> float:
        return max(self.points, key=lambda p: p[1])[0]


FACTORS: Dict[str, Factor] = {f.key: f for f in [
    # ---------------- Movement ----------------
    Factor("steps", "movement", "Daily steps", "steps",
           [(2000, 1.00), (4000, 0.78), (6000, 0.62), (8000, 0.55), (10000, 0.50), (12000, 0.49)],
           "A_cohort", 0.07, ref=5500,
           source="Paluch 2022 Lancet Public Health (15 cohorts, n=47,471): vs ~3.5k, Q2 5.8k HR 0.60, Q3 7.8k 0.55, Q4 10.9k 0.47; plateau 8–10k under 60, 6–8k over 60",
           time_cost_min_per_unit=0.01, lever_step=2000, lever_time_min=20),
    Factor("vilpa_min", "movement", "Vigorous bursts", "min/day",
           [(0, 1.00), (1, 0.88), (3, 0.74), (4.4, 0.70), (10, 0.62)],
           "A_cohort", 0.08, ref=1.0,
           source="Stamatakis 2022 Nature Medicine (UK Biobank, n=25,241): 3–4 min/day of vigorous bursts → 26–30% lower all-cause mortality",
           time_cost_min_per_unit=1.0, lever_step=3, lever_time_min=3),
    Factor("resistance_min_wk", "movement", "Strength training", "min/week",
           [(0, 1.00), (30, 0.88), (60, 0.83), (130, 0.90), (180, 0.95)],
           "A_cohort", 0.05, ref=20,
           source="Momma 2022 BJSM meta-analysis (16 studies): 30–60 min/wk → 10–17% lower mortality; J-shaped above ~130 min",
           time_cost_min_per_unit=1.0, lever_step=30, lever_time_min=30),
    Factor("fitness_pct", "movement", "Cardiorespiratory fitness", "percentile",
           [(5, 1.00), (25, 0.70), (50, 0.55), (75, 0.45), (97, 0.40)],
           "A_cohort", 0.06, ref=50,
           source="Mandsager 2018 JAMA Netw Open (n=122,007): low vs elite fitness adjusted HR 5.04; no upper limit of benefit. Curve here is deliberately compressed",
           time_cost_min_per_unit=0.0, lever_step=10, lever_time_min=150,
           note="percentile from wearable VO2max estimate or a 12-min test"),
    Factor("gait_speed", "movement", "Walking speed", "m/s",
           [(0.6, 1.00), (0.8, 0.78), (1.0, 0.61), (1.2, 0.48), (1.4, 0.40)],
           "A_cohort", 0.04, ref=1.2,
           source="Studenski 2011 JAMA (pooled n=34,485): each +0.1 m/s ≈ HR 0.88; Dunedin: gait speed at 45 tracks biological aging",
           lever_step=0.1, lever_time_min=0, age_min=0,
           note="mortality gradient established in older adults; weight is age-scaled below 45"),
    # ---------------- Sleep ----------------
    Factor("sleep_hours", "sleep", "Sleep duration", "h",
           [(4, 1.35), (5, 1.20), (6, 1.10), (7, 1.00), (8, 1.00), (9, 1.12), (10, 1.30)],
           "A_cohort", 0.04, ref=7.0, higher_is_better=False,
           source="Cappuccio 2010 Sleep meta-analysis (16 studies, n=1.3M): short sleep RR 1.12, long sleep RR 1.30",
           lever_step=0.5, lever_time_min=30,
           note="U-shaped; 7–8 h is the reference"),
    Factor("sri", "sleep", "Sleep regularity", "SRI 0–100",
           [(40, 1.00), (60, 0.86), (70, 0.80), (80, 0.74), (90, 0.70)],
           "A_cohort", 0.06, ref=75,
           source="Windred 2024 Sleep (UK Biobank, n=60,977): top vs bottom SRI quintile 20–48% lower all-cause mortality; regularity outperformed duration",
           lever_step=10, lever_time_min=0),
    # ---------------- Light ----------------
    Factor("day_light_min", "light", "Bright light minutes", "min/day",
           [(0, 1.00), (30, 0.92), (60, 0.87), (120, 0.85)],
           "A_cohort", 0.05, ref=40,
           source="Windred 2024 PNAS (n=88,905, wrist sensors): darkest-day deciles → ~15–20% higher mortality; Brown 2022 consensus: ≥250 lx melanopic by day",
           time_cost_min_per_unit=0.0, lever_step=30, lever_time_min=30,
           note="bright band = estimated melanopic ≥250 lx; glasses measure at the eye"),
    Factor("night_light_lux", "light", "Light during sleep", "melanopic lx",
           [(0, 1.00), (1, 1.00), (10, 1.12), (50, 1.25), (100, 1.34)],
           "A_cohort", 0.05, ref=3, higher_is_better=False,
           source="Windred 2024 PNAS: brightest-night deciles → 21–34% higher mortality; Brown 2022: ≤1 lx melanopic during sleep",
           lever_step=-5, lever_time_min=2),
    # ---------------- Social ----------------
    Factor("social_index", "social", "Social integration", "index 0–100",
           [(0, 1.00), (25, 0.85), (50, 0.72), (75, 0.62), (100, 0.55)],
           "A_cohort", 0.05, ref=50,
           source="Holt-Lunstad 2010 PLOS Med (148 studies, n=308,849): stronger ties OR 1.50 for survival; complex integration OR 1.91",
           lever_step=15, lever_time_min=20,
           note="index = distinct people/week, conversation minutes/day, reciprocity (passive, no identities)"),
    Factor("purpose", "social", "Purpose in life", "1–6",
           [(1, 1.00), (3, 0.75), (6, 0.60)],
           "B", 0.15, ref=4,
           source="Alimujiang 2019 JAMA Netw Open (HRS, n=6,985): lowest vs highest purpose HR 2.43",
           lever_step=1, lever_time_min=0, note="weekly one-question check-in"),
    # ---------------- Environment ----------------
    Factor("nature_min_wk", "environment", "Time in nature", "min/week",
           [(0, 1.00), (60, 0.98), (120, 0.96), (300, 0.94)],
           "B", 0.02, ref=60,
           source="White 2019 Sci Rep (n≈20k): ≥120 min/wk → better health/wellbeing; Rojas-Rueda 2019 Lancet Planet Health: 4% lower mortality per 0.1 NDVI",
           lever_step=60, lever_time_min=60),
    Factor("noise_night_db", "environment", "Night noise", "dB(A)",
           [(30, 1.00), (45, 1.00), (55, 1.05), (65, 1.10)],
           "B", 0.03, ref=40, higher_is_better=False,
           source="WHO 2018 Environmental Noise Guidelines: Lnight <45 dB; IHD RR ~1.08 per 10 dB Lden",
           lever_step=-10, lever_time_min=5),
    # ---------------- Diet & substances ----------------
    Factor("med_adherence", "diet", "Mediterranean pattern", "0–1",
           [(0, 1.00), (0.5, 0.93), (1.0, 0.86)],
           "A_cohort", 0.03, ref=0.5,
           source="Sofi 2010 AJCN meta-analysis: each 2-point adherence gain → 8% lower mortality; PREDIMED RCT: ~30% fewer major CV events",
           lever_step=0.2, lever_time_min=0),
    Factor("alcohol_drinks", "diet", "Alcohol", "drinks/day",
           [(0, 1.00), (1, 1.02), (2, 1.08), (3, 1.15), (4, 1.30), (6, 1.50)],
           "A_cohort", 0.04, ref=0.5, higher_is_better=False,
           source="Zhao 2023 JAMA Netw Open (107 studies): no protective range once abstainer bias is removed; risk rises from ~2 drinks/day",
           lever_step=-1, lever_time_min=0),
    Factor("smoker", "diet", "Smoking / vaping nicotine daily", "0/1",
           [(0, 1.00), (1, 2.80)],
           "A_cohort", 0.05, ref=0, higher_is_better=False,
           source="Jha 2013 NEJM (n=201,551): smokers lose ≥10 years; quitting before 40 removes ~90% of the excess",
           lever_step=-1, lever_time_min=0),
    # ---------------- Recovery / heat ----------------
    Factor("sauna_wk", "recovery", "Sauna sessions", "sessions/week",
           [(0, 1.00), (1, 1.00), (2.5, 0.78), (4, 0.60), (7, 0.60)],
           "B", 0.12, ref=0,
           source="Laukkanen 2015 JAMA IM (n=2,315 Finnish men, 20.7 y): 4–7×/wk vs 1× HR 0.60 all-cause; sessions >19 min",
           lever_step=1, lever_time_min=25, note="single male cohort — shrunk 50%"),
    Factor("recovery_ratio", "recovery", "HRV vs your baseline", "7d/60d ln-RMSSD",
           [(0.70, 1.10), (0.85, 1.05), (1.00, 1.00), (1.15, 0.97)],
           "B", 0.05, ref=1.0,
           source="Low HRV is a marker of autonomic strain (Framingham, Tsuji 1996); used here as a state marker, not a cause",
           lever_step=0.05, lever_time_min=0),
]}

# Leading indicators: forecast tomorrow, never scored as today's hazard.
LEADING = {
    "caffeine_after_cutoff": {"sleep_h": -1.0,
        "source": "Drake 2013 J Clin Sleep Med RCT: 400 mg caffeine 6 h before bed cut total sleep time by >1 h"},
    "night_screen_min": {"sleep_h_per_60min": -0.25, "melatonin_delay_min_per_60min": 15,
        "source": "Evening light ≥10 lx melanopic delays melatonin (Brown 2022); effect size here is a conservative estimate (grade C)"},
    "alcohol_drinks": {"hrv_pct_per_drink": -6.0, "sleep_h_per_drink": -0.15,
        "source": "Wearable cohorts show same-night HRV suppression per drink; conservative estimate"},
    "late_bed_shift_min": {"sri_pts_per_30min": -6,
        "source": "SRI mechanics: a 30-min bedtime shift on one night costs ~6 SRI points over a 7-day window"},
}

LAYER_LABELS = {
    "movement": "Movement", "sleep": "Sleep", "light": "Light & clock",
    "social": "Social", "environment": "Environment", "diet": "Diet & substances",
    "recovery": "Recovery",
}


# --------------------------------------------------------------------------- #
# 3. Profile — what changes for a 20-year-old athlete vs a 45-year-old desk worker
# --------------------------------------------------------------------------- #

@dataclass
class Profile:
    age: int = 25
    sex: str = "M"
    goal: str = "average"      # average | athlete | shift | genetic_risk
    apoe4: bool = False
    lpa_high: bool = False
    cyp1a2_slow: bool = False
    bedtime_hh: float = 23.0   # habitual bedtime, decimal hours

    def factor_weight(self, f: Factor) -> float:
        """Profile multipliers on the *priority* of a factor. Biology stays the same;
        what changes is how much a lever matters to this person right now."""
        w = 1.0
        if f.key == "gait_speed" and self.age < 45:
            w *= 0.4                       # gradient established in older adults
        if f.key == "fitness_pct" and self.age < 35:
            w *= 1.3                       # compounding capital
        if f.key in ("resistance_min_wk", "gait_speed") and self.age >= 60:
            w *= 1.5                       # sarcopenia / falls
        if f.key == "social_index" and self.age >= 60:
            w *= 1.3
        if self.goal == "athlete":
            if f.key == "recovery_ratio":
                w *= 2.0                   # under-recovery is the athlete's risk
            if f.key in ("steps", "vilpa_min"):
                w *= 0.5                   # already saturated
            if f.key == "sleep_hours":
                w *= 1.3                   # sleep extension
        if self.apoe4 and f.key in ("sleep_hours", "sri", "alcohol_drinks", "fitness_pct"):
            w *= 1.4
        if self.lpa_high and f.key in ("med_adherence", "fitness_pct"):
            w *= 1.3
        return w

    def targets(self) -> Dict[str, float]:
        t = {
            "steps": 8000 if self.age < 60 else 7000,
            "vilpa_min": 4,
            "resistance_min_wk": 60,
            "sleep_hours": 7.5,
            "sri": 80,
            "day_light_min": 45,
            "night_light_lux": 1,
            "social_index": 70,
            "nature_min_wk": 120,
            "noise_night_db": 45,
            "med_adherence": 0.7,
            "alcohol_drinks": 0,
            "sauna_wk": 2.5,
            "recovery_ratio": 1.0,
            "caffeine_cutoff_h_before_bed": 12 if self.cyp1a2_slow else 9,
        }
        if self.goal == "athlete":
            t["sleep_hours"] = 8.5
            t["steps"] = 6000
            t["mobility_min_wk"] = 60      # tracked target, not a hazard factor
        return t


# --------------------------------------------------------------------------- #
# 4. Scoring
# --------------------------------------------------------------------------- #

@dataclass
class FactorResult:
    key: str
    layer: str
    label: str
    dose: Optional[float]
    hr: Optional[float]
    log_hazard: float           # absolute (vs worst tier)
    rel_log_hazard: float       # vs population-typical reference (credit < 0 < debit)
    hours_today: float          # credit/debit in healthy-life hours for today
    grade: str
    source: str
    measured: bool


@dataclass
class DayScore:
    overall: float
    layers: Dict[str, float]
    factors: List[FactorResult]
    years_delta: float
    years_ci: Tuple[float, float]
    hours_today: float
    hours_ci: Tuple[float, float]
    remaining_days: float


def _layer_sum(values: List[float]) -> float:
    """Discounted sum that limits double counting of correlated factors."""
    vals = sorted(values, key=abs, reverse=True)
    return sum(v * LAYER_DISCOUNT[min(i, len(LAYER_DISCOUNT) - 1)] for i, v in enumerate(vals))


def _total(loghaz_by_layer: Dict[str, List[float]]) -> float:
    return sum(_layer_sum(v) for v in loghaz_by_layer.values())


def score_day(obs: Dict[str, float], profile: Profile) -> DayScore:
    """obs: measured doses keyed by Factor.key. Missing factors are imputed at the
    population reference and flagged measured=False (they earn nothing)."""
    rle_days = remaining_life_years(profile.age, profile.sex) * 365.25
    per_layer: Dict[str, List[float]] = {}
    per_layer_best: Dict[str, List[float]] = {}
    per_layer_worst: Dict[str, List[float]] = {}
    rel_by_layer: Dict[str, List[float]] = {}
    var_terms: List[float] = []
    results: List[FactorResult] = []

    for f in FACTORS.values():
        if f.active_for and profile.goal not in f.active_for:
            continue
        measured = f.key in obs and obs[f.key] is not None
        dose = float(obs[f.key]) if measured else f.ref
        w = profile.factor_weight(f)
        lh = f.log_hazard(dose) * w
        lh_ref = f.log_hazard(f.ref) * w
        rel = lh - lh_ref if measured else 0.0
        per_layer.setdefault(f.layer, []).append(lh)
        per_layer_best.setdefault(f.layer, []).append(f.log_hazard(f.best_dose()) * w)
        per_layer_worst.setdefault(f.layer, []).append(f.log_hazard(f.worst_dose()) * w)
        rel_by_layer.setdefault(f.layer, []).append(rel)
        if measured:
            var_terms.append((f.se_log * SHRINK[f.grade] * w) ** 2)
        hours = (-rel / GOMPERTZ_B) * HOURS_PER_YEAR / rle_days
        results.append(FactorResult(f.key, f.layer, f.label, dose if measured else None,
                                    f.hr(dose) if measured else None, lh, rel, hours,
                                    f.grade, f.source, measured))

    # Layer scores 0–100 on the evidence-derived best/worst range
    layers: Dict[str, float] = {}
    for layer in per_layer:
        cur, best, worst = _layer_sum(per_layer[layer]), _layer_sum(per_layer_best[layer]), _layer_sum(per_layer_worst[layer])
        layers[layer] = 100.0 * (worst - cur) / (worst - best) if worst != best else 50.0
        layers[layer] = float(max(0.0, min(100.0, layers[layer])))

    cur_t, best_t, worst_t = _total(per_layer), _total(per_layer_best), _total(per_layer_worst)
    overall = float(max(0.0, min(100.0, 100.0 * (worst_t - cur_t) / (worst_t - best_t))))

    # Life-expectancy delta vs the population-typical person (Gompertz shift)
    rel_total = _total(rel_by_layer)
    years = -rel_total / GOMPERTZ_B
    se_years = math.sqrt(sum(var_terms)) / GOMPERTZ_B if var_terms else 0.0
    hours = years * HOURS_PER_YEAR / rle_days
    se_hours = se_years * HOURS_PER_YEAR / rle_days
    return DayScore(overall, layers, results, years, (years - 1.96 * se_years, years + 1.96 * se_years),
                    hours, (hours - 1.96 * se_hours, hours + 1.96 * se_hours), rle_days)


# --------------------------------------------------------------------------- #
# 5. Levers — "Bryan Johnson in your head": rank by healthy-life hours per minute
# --------------------------------------------------------------------------- #

@dataclass
class Lever:
    key: str
    label: str
    action: str
    hours_gain: float
    time_min: float
    roi_hours_per_min: float
    layers: List[str]
    source: str


def _hours_for_dose(f: Factor, dose: float, profile: Profile, rle_days: float) -> float:
    w = profile.factor_weight(f)
    return (-(f.log_hazard(dose) * w) / GOMPERTZ_B) * HOURS_PER_YEAR / rle_days


def levers(obs: Dict[str, float], profile: Profile, top: int = 5) -> List[Lever]:
    rle_days = remaining_life_years(profile.age, profile.sex) * 365.25
    out: List[Lever] = []
    for f in FACTORS.values():
        if f.lever_step == 0 or f.key not in obs or obs[f.key] is None:
            continue
        dose = float(obs[f.key])
        new = dose + f.lever_step
        lo, hi = min(p[0] for p in f.points), max(p[0] for p in f.points)
        new = max(lo, min(hi, new))
        if new == dose:
            continue
        gain = _hours_for_dose(f, new, profile, rle_days) - _hours_for_dose(f, dose, profile, rle_days)
        if gain <= 0:
            continue
        t = max(f.lever_time_min, 1.0)
        out.append(Lever(f.key, f.label, f"{f.label}: {dose:g} → {new:g} {f.unit}", gain, f.lever_time_min,
                         gain / t, [f.layer], f.source))
    # Bundle: one act that hits several layers (retime, don't add)
    bundle_keys = [("steps", 2000), ("day_light_min", 30), ("nature_min_wk", 30), ("social_index", 10)]
    gain, layers_hit = 0.0, []
    for k, step in bundle_keys:
        if k in obs and obs[k] is not None:
            f = FACTORS[k]
            hi = max(p[0] for p in f.points)
            new = min(hi, float(obs[k]) + step)
            g = _hours_for_dose(f, new, profile, rle_days) - _hours_for_dose(f, float(obs[k]), profile, rle_days)
            if g > 0:
                gain += g
                layers_hit.append(f.layer)
    if gain > 0:
        out.append(Lever("bundle_walk", "Outdoor walk with someone before 10:00",
                         "30-min outdoor walk with a friend before 10:00", gain, 30, gain / 30,
                         sorted(set(layers_hit)), "Bundles steps, bright light, nature, social — one act, four layers"))
    out.sort(key=lambda l: l.roi_hours_per_min, reverse=True)
    return out[:top]


# --------------------------------------------------------------------------- #
# 6. Tomorrow forecast from leading indicators
# --------------------------------------------------------------------------- #

@dataclass
class Forecast:
    sleep_hours: float
    hrv_change_pct: float
    sri_change_pts: float
    melatonin_delay_min: float
    drivers: List[str]


def forecast_tonight(today: Dict[str, float], baseline_sleep_h: float, profile: Profile) -> Forecast:
    drivers: List[str] = []
    sleep = baseline_sleep_h
    hrv = 0.0
    sri = 0.0
    mel = 0.0
    cutoff = profile.targets()["caffeine_cutoff_h_before_bed"]
    last_caf = today.get("last_caffeine_hh")
    if last_caf is not None and (profile.bedtime_hh - last_caf) < cutoff:
        sleep += LEADING["caffeine_after_cutoff"]["sleep_h"] * min(1.0, (cutoff - (profile.bedtime_hh - last_caf)) / cutoff + 0.5)
        drivers.append(f"caffeine at {last_caf:.0f}:00 is inside your {cutoff} h cutoff")
    screens = today.get("night_screen_min", 0.0) or 0.0
    if screens > 0:
        sleep += LEADING["night_screen_min"]["sleep_h_per_60min"] * screens / 60
        mel += LEADING["night_screen_min"]["melatonin_delay_min_per_60min"] * screens / 60
        drivers.append(f"{screens:.0f} min of screens after 22:00")
    drinks = today.get("alcohol_drinks", 0.0) or 0.0
    if drinks > 0:
        hrv += LEADING["alcohol_drinks"]["hrv_pct_per_drink"] * drinks
        sleep += LEADING["alcohol_drinks"]["sleep_h_per_drink"] * drinks
        drivers.append(f"{drinks:g} drink(s) — expect a lower HRV tonight")
    shift = today.get("planned_bed_shift_min", 0.0) or 0.0
    if abs(shift) >= 30:
        sri += LEADING["late_bed_shift_min"]["sri_pts_per_30min"] * abs(shift) / 30
        drivers.append(f"bedtime {shift:+.0f} min vs habit")
    return Forecast(round(sleep, 2), round(hrv, 1), round(sri, 1), round(mel, 0), drivers)


# --------------------------------------------------------------------------- #
# 7. Weekly ledger — targets, accrual, projection, deficits (the planner's input)
# --------------------------------------------------------------------------- #

@dataclass
class LedgerLine:
    key: str
    label: str
    accrued: float
    target: float
    projected: float
    deficit: float
    days_elapsed: int
    status: str   # on_track | at_risk | behind


WEEKLY_KEYS = {"nature_min_wk": "nature_min_today", "resistance_min_wk": "resistance_min_today",
               "sauna_wk": "sauna_today"}
DAILY_KEYS = ["steps", "day_light_min", "vilpa_min", "social_index"]


def weekly_ledger(days: List[Dict[str, float]], profile: Profile, week_len: int = 7) -> List[LedgerLine]:
    """days: this week's per-day observations so far (oldest first)."""
    t = profile.targets()
    n = max(1, len(days))
    lines: List[LedgerLine] = []
    for wk_key, day_key in WEEKLY_KEYS.items():
        acc = sum((d.get(day_key) or 0.0) for d in days)
        proj = acc / n * week_len
        target = t[wk_key]
        lines.append(_line(wk_key, FACTORS[wk_key].label, acc, target, proj, n))
    for k in DAILY_KEYS:
        vals = [d.get(k) for d in days if d.get(k) is not None]
        if not vals:
            continue
        acc = float(np.mean(vals))
        target = t[k]
        lines.append(_line(k, FACTORS[k].label, acc, target, acc, n))
    return lines


def _line(key, label, acc, target, proj, n) -> LedgerLine:
    deficit = max(0.0, target - proj)
    status = "on_track" if proj >= target else ("at_risk" if proj >= 0.7 * target else "behind")
    return LedgerLine(key, label, round(acc, 1), target, round(proj, 1), round(deficit, 1), n, status)


# --------------------------------------------------------------------------- #
# 8. Attribution — what actually moves *this* person (glasses → WHOOP)
# --------------------------------------------------------------------------- #

@dataclass
class Effect:
    exposure: str
    outcome: str
    beta: float          # change in outcome per unit exposure
    ci: Tuple[float, float]
    n: int
    blended_beta: float  # Bayesian blend with population prior
    note: str


def attribute(exposure: np.ndarray, outcome: np.ndarray, covariates: Optional[np.ndarray] = None,
              weekday: Optional[np.ndarray] = None, prior_beta: float = 0.0, prior_se: float = 0.05,
              exposure_name: str = "", outcome_name: str = "") -> Effect:
    """Lagged OLS: outcome[t] (e.g. next-night ln HRV) on exposure[t] with weekday
    dummies and covariates (strain, temperature). Requires n >= 14.
    Returns the personal estimate, its 90% CI, and a precision-weighted blend with
    the population prior so early estimates are honest, not noisy."""
    y = np.asarray(outcome, float)
    x = np.asarray(exposure, float)
    n = len(y)
    if n < 14:
        return Effect(exposure_name, outcome_name, float("nan"), (float("nan"), float("nan")), n, prior_beta,
                      "fewer than 14 days — showing population prior")
    cols = [np.ones(n), x]
    if covariates is not None:
        c = np.asarray(covariates, float)
        cols += [c[:, i] for i in range(c.shape[1])] if c.ndim == 2 else [c]
    if weekday is not None:
        wd = np.asarray(weekday, int)
        for d in range(1, 7):
            cols.append((wd == d).astype(float))
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(1, n - X.shape[1])
    sigma2 = float(resid @ resid) / dof
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    se = math.sqrt(max(cov[1, 1], 1e-12))
    b = float(beta[1])
    ci = (b - 1.645 * se, b + 1.645 * se)
    wp, ws = 1.0 / prior_se ** 2, 1.0 / se ** 2
    blended = (wp * prior_beta + ws * b) / (wp + ws)
    return Effect(exposure_name, outcome_name, b, ci, n, float(blended),
                  "personal estimate" if abs(b) > 1.645 * se else "not yet distinguishable from zero")


# --------------------------------------------------------------------------- #
# 9. Insights — plain sentences with the evidence attached
# --------------------------------------------------------------------------- #

def insights(day: DayScore, ledger: List[LedgerLine], fc: Forecast, lv: List[Lever],
             effects: Optional[List[Effect]] = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    # 1. Tonight
    if fc.drivers:
        out.append({"kind": "tonight",
                    "text": f"Tonight: ~{fc.sleep_hours:.1f} h of sleep, HRV {fc.hrv_change_pct:+.0f}%"
                            + (f", clock delayed ~{fc.melatonin_delay_min:.0f} min" if fc.melatonin_delay_min else "")
                            + ". Because: " + "; ".join(fc.drivers) + ".",
                    "source": LEADING["caffeine_after_cutoff"]["source"]})
    # 2. Today's ledger
    credits = sorted([f for f in day.factors if f.measured and f.hours_today > 0.05], key=lambda f: -f.hours_today)[:2]
    debits = sorted([f for f in day.factors if f.measured and f.hours_today < -0.05], key=lambda f: f.hours_today)[:2]
    if credits or debits:
        parts = [f"+{c.hours_today:.1f} h {c.label.lower()}" for c in credits] + \
                [f"{d.hours_today:.1f} h {d.label.lower()}" for d in debits]
        out.append({"kind": "today", "text": f"Today nets {day.hours_today:+.1f} healthy-life hours "
                                             f"(±{(day.hours_ci[1]-day.hours_ci[0])/2:.1f}): " + ", ".join(parts) + ".",
                    "source": "Gompertz shift + microlife framing (Spiegelhalter & Blastland, BMJ 2012)"})
    # 3. Deficits with projection
    for l in sorted(ledger, key=lambda l: -l.deficit):
        if l.status == "on_track":
            continue
        out.append({"kind": "week", "text": f"{l.label}: {l.accrued:g} so far, on pace for {l.projected:g} "
                                            f"vs target {l.target:g}. You need {l.deficit:g} more by Sunday.",
                    "source": FACTORS[l.key].source})
        break
    # 4. Best lever
    if lv:
        best = lv[0]
        if best.time_min <= 0:
            text = (f"Cheapest win, costs no time: {best.action}. "
                    f"≈ +{best.hours_gain:.1f} healthy-life hours.")
        else:
            text = (f"Best use of your next {best.time_min:.0f} minutes: {best.action}. "
                    f"≈ +{best.hours_gain:.1f} healthy-life hours ({best.roi_hours_per_min*60:.1f} h per hour invested).")
        out.append({"kind": "lever", "text": text, "source": best.source})
    # 5. Personal evidence
    for e in (effects or []):
        if e.n >= 14 and not math.isnan(e.beta) and "personal" in e.note:
            out.append({"kind": "you", "text": f"On your own data ({e.n} days): each unit of {e.exposure} moves "
                                               f"{e.outcome} by {e.beta:+.3f} [90% CI {e.ci[0]:+.3f}, {e.ci[1]:+.3f}].",
                        "source": "Lagged regression with weekday and strain covariates"})
            break
    return out


# --------------------------------------------------------------------------- #
# 10. Serialize for the dashboard
# --------------------------------------------------------------------------- #

def to_payload(day: DayScore, ledger: List[LedgerLine], fc: Forecast, lv: List[Lever],
               tips: List[Dict[str, str]]) -> dict:
    return {
        "overall": round(day.overall),
        "layers": {LAYER_LABELS[k]: round(v) for k, v in day.layers.items()},
        "years_delta": round(day.years_delta, 2),
        "years_ci": [round(day.years_ci[0], 2), round(day.years_ci[1], 2)],
        "hours_today": round(day.hours_today, 2),
        "hours_ci": [round(day.hours_ci[0], 2), round(day.hours_ci[1], 2)],
        "factors": [{"key": f.key, "layer": LAYER_LABELS[f.layer], "label": f.label, "dose": f.dose,
                     "hr": None if f.hr is None else round(f.hr, 3), "hours": round(f.hours_today, 2),
                     "grade": f.grade, "measured": f.measured, "source": f.source} for f in day.factors],
        "ledger": [l.__dict__ for l in ledger],
        "forecast": fc.__dict__,
        "levers": [l.__dict__ for l in lv],
        "insights": tips,
    }


# --------------------------------------------------------------------------- #
# 10b. Adapter — from the app's raw episodes + wearable rows to observations
# --------------------------------------------------------------------------- #

def observations_from_app(episodes: List[dict], wearable_day: dict, week_rows: List[dict],
                          bedtime_hh: float = 23.0) -> Dict[str, float]:
    """episodes: today's glasses episodes as emitted by the pipeline, each
      {"type": "screen_block|caffeine_sighting|meal|conversation|outdoor_block|
                alcohol_sighting|sauna|walk", "start_hh": 12.1, "minutes": 18,
       "scene": "park", "lux": 3200, "people": 2, "cadence": 112, "db": 48, "tags": [...]}
    wearable_day: {"steps", "sleep_hours", "sri", "hrv_ratio", "strain",
                   "workouts": [{"kind": "strength", "minutes": 40}], "night_db": 41,
                   "vo2max_pct": 70, "night_lux": 1}
    week_rows: this week's daily dicts of the same shape (for weekly factors)."""
    def mins(kind, pred=lambda e: True):
        return sum((e.get("minutes") or 0) for e in episodes if e.get("type") == kind and pred(e))

    bright = mins("outdoor_block") + sum((e.get("minutes") or 0) for e in episodes
                                        if e.get("type") not in ("outdoor_block",) and (e.get("lux") or 0) >= 1000)
    nature_today = mins("outdoor_block", lambda e: (e.get("scene") or "") in ("park", "trees", "trail", "water", "garden", "nature"))
    conv_min = mins("conversation")
    people = len({p for e in episodes if e.get("type") == "conversation" for p in (e.get("people_ids") or [])}) \
        or max([e.get("people") or 0 for e in episodes if e.get("type") == "conversation"] + [0])
    # social index: 60% contact minutes (saturates at 60 min), 40% breadth (saturates at 5 people)
    social = 100 * (0.6 * min(conv_min, 60) / 60 + 0.4 * min(people, 5) / 5)
    caf = [e.get("start_hh") for e in episodes if e.get("type") == "caffeine_sighting" and e.get("start_hh") is not None]
    screens_night = mins("screen_block", lambda e: (e.get("start_hh") or 0) >= 22 or (e.get("start_hh") or 0) < 5)
    drinks = sum(max(1, int(e.get("count") or 1)) for e in episodes if e.get("type") == "alcohol_sighting")
    meals = [e for e in episodes if e.get("type") == "meal"]
    med = (sum(1 for m in meals if "mediterranean" in (m.get("tags") or []) or "plant" in (m.get("tags") or []))
           / len(meals)) if meals else None
    cadences = [e.get("cadence") for e in episodes if e.get("type") == "walk" and e.get("cadence")]
    gait = None
    if cadences and wearable_day.get("height_m"):
        step_len = 0.415 * wearable_day["height_m"]                 # fallback model
        gait = float(np.median(cadences)) / 60 * step_len
    week_nature = sum((r.get("nature_min") or 0) for r in week_rows) + nature_today
    week_strength = sum(w.get("minutes", 0) for r in week_rows for w in (r.get("workouts") or []) if w.get("kind") == "strength") \
        + sum(w.get("minutes", 0) for w in (wearable_day.get("workouts") or []) if w.get("kind") == "strength")
    week_sauna = sum(1 for r in week_rows if r.get("sauna")) + (1 if mins("sauna") > 0 else 0)
    obs = {
        "steps": wearable_day.get("steps"),
        "vilpa_min": wearable_day.get("vilpa_min"),
        "resistance_min_wk": week_strength,
        "fitness_pct": wearable_day.get("vo2max_pct"),
        "gait_speed": gait,
        "sleep_hours": wearable_day.get("sleep_hours"),
        "sri": wearable_day.get("sri"),
        "day_light_min": bright,
        "night_light_lux": wearable_day.get("night_lux"),
        "social_index": social if (conv_min or people) else None,
        "purpose": wearable_day.get("purpose"),
        "nature_min_wk": week_nature,
        "noise_night_db": wearable_day.get("night_db"),
        "med_adherence": med,
        "alcohol_drinks": drinks,
        "smoker": wearable_day.get("smoker", 0),
        "sauna_wk": week_sauna,
        "recovery_ratio": wearable_day.get("hrv_ratio"),
        # leading indicators
        "last_caffeine_hh": max(caf) if caf else None,
        "night_screen_min": screens_night,
        "planned_bed_shift_min": wearable_day.get("planned_bed_shift_min", 0),
    }
    return {k: v for k, v in obs.items() if v is not None}


def pins_from_episodes(episodes: List[dict], day: "DayScore", fc: "Forecast") -> List[dict]:
    """Evidence pins: each glasses episode with a frame, tied to what it cost or earned."""
    by_key = {f.key: f for f in day.factors}
    out = []
    for e in episodes:
        t = e.get("type")
        hh = e.get("start_hh") or 0
        time = f"{int(hh):02d}:{int(round((hh % 1) * 60)):02d}"
        pin = {"time": time, "img": e.get("frame_url"), "grade": "A", "kind": "credit", "seen": "", "effect": ""}
        if t == "outdoor_block":
            f = by_key.get("day_light_min")
            pin.update(seen=f"Outdoors, {e.get('scene', 'daylight')}", effect=f"bright light {'before 10:00 — advances your clock' if hh < 10 else 'counted'}"
                       + (f" · +{f.hours_today:.1f} h today" if f and f.hours_today > 0.05 else ""))
        elif t == "conversation":
            f = by_key.get("social_index")
            pin.update(seen=f"Conversation, {e.get('minutes', 0):.0f} min", effect="counts toward social integration"
                       + (f" · +{f.hours_today:.1f} h" if f and f.hours_today > 0.05 else ""))
        elif t == "alcohol_sighting":
            pin.update(kind="debit", seen="Alcohol in frame", effect=f"HRV {fc.hrv_change_pct:+.0f}% tonight (forecast)")
        elif t == "caffeine_sighting":
            pin.update(kind="debit" if hh >= 14 else "credit", grade="B", seen=f"Caffeine at {time}",
                       effect="inside your cutoff — ~−1 h of sleep tonight" if hh >= 14 else "outside your cutoff — fine")
        elif t == "screen_block" and (hh >= 22 or hh < 5):
            pin.update(kind="debit", grade="C", seen=f"Screen, {e.get('minutes', 0):.0f} min after 22:00",
                       effect=f"melatonin delayed ~{fc.melatonin_delay_min:.0f} min")
        elif t == "meal":
            pin.update(grade="A", seen=f"Meal: {e.get('label', 'logged')}", effect="tagged against the Mediterranean pattern")
        elif t == "sauna":
            pin.update(grade="B", seen="Sauna", effect="session counted toward 2–3 per week")
        elif t == "walk":
            pin.update(seen=f"Walking, cadence {e.get('cadence', '—')}", effect="gait speed sample")
        else:
            continue
        out.append(pin)
    return out


def run_from_json(req: dict) -> dict:
    """req = {"profile": {...Profile fields...}, "episodes": [...], "wearable_day": {...},
              "week_rows": [...], "history": {"exposure": [...], "outcome": [...],
              "covariates": [...], "weekday": [...], "exposure_name": "...", "outcome_name": "..."}}"""
    profile = Profile(**(req.get("profile") or {}))
    episodes = req.get("episodes") or []
    obs = observations_from_app(episodes, req.get("wearable_day") or {}, req.get("week_rows") or [], profile.bedtime_hh)
    day = score_day(obs, profile)
    week_days = [{"nature_min_today": r.get("nature_min", 0),
                  "resistance_min_today": sum(w.get("minutes", 0) for w in (r.get("workouts") or []) if w.get("kind") == "strength"),
                  "sauna_today": 1 if r.get("sauna") else 0, "steps": r.get("steps"),
                  "day_light_min": r.get("day_light_min"), "vilpa_min": r.get("vilpa_min"),
                  "social_index": r.get("social_index")} for r in (req.get("week_rows") or [])]
    week_days.append({"nature_min_today": obs.get("nature_min_wk", 0) - sum(r.get("nature_min", 0) for r in (req.get("week_rows") or [])),
                      "resistance_min_today": sum(w.get("minutes", 0) for w in ((req.get("wearable_day") or {}).get("workouts") or []) if w.get("kind") == "strength"),
                      "sauna_today": 1 if any(e.get("type") == "sauna" for e in episodes) else 0,
                      "steps": obs.get("steps"), "day_light_min": obs.get("day_light_min"),
                      "vilpa_min": obs.get("vilpa_min"), "social_index": obs.get("social_index")})
    ledger = weekly_ledger(week_days, profile)
    fc = forecast_tonight(obs, baseline_sleep_h=float((req.get("wearable_day") or {}).get("baseline_sleep_h", 7.5)), profile=profile)
    lv = levers(obs, profile)
    effects = []
    h = req.get("history")
    if h and h.get("exposure") and h.get("outcome"):
        effects.append(attribute(np.array(h["exposure"]), np.array(h["outcome"]),
                                 covariates=np.array(h["covariates"]) if h.get("covariates") else None,
                                 weekday=np.array(h["weekday"]) if h.get("weekday") else None,
                                 prior_beta=h.get("prior_beta", 0.0), prior_se=h.get("prior_se", 0.05),
                                 exposure_name=h.get("exposure_name", "exposure"), outcome_name=h.get("outcome_name", "outcome")))
    tips = insights(day, ledger, fc, lv, effects)
    payload = to_payload(day, ledger, fc, lv, tips)
    payload["pins"] = pins_from_episodes(episodes, day, fc)
    payload["observations"] = obs
    payload["effects"] = [e.__dict__ for e in effects]
    return payload


# --------------------------------------------------------------------------- #
# 11. Self-test — run `python brian_score.py`; CLI: `python brian_score.py --json < req.json`
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import json
    import sys

    # The self-test prints "→ ≈ ±"; a cp1252 console (Windows) would otherwise crash on them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if "--json" in sys.argv:
        print(json.dumps(run_from_json(json.load(sys.stdin)), default=float))
        sys.exit(0)

    me = Profile(age=20, sex="M", goal="average", bedtime_hh=23.0)
    today = {
        "steps": 9100, "vilpa_min": 2, "resistance_min_wk": 40, "fitness_pct": 70, "gait_speed": 1.35,
        "sleep_hours": 7.4, "sri": 84, "day_light_min": 44, "night_light_lux": 2,
        "social_index": 58, "nature_min_wk": 35, "noise_night_db": 45, "med_adherence": 0.6,
        "alcohol_drinks": 2, "smoker": 0, "sauna_wk": 0, "recovery_ratio": 1.02,
        # leading indicators
        "last_caffeine_hh": 16, "night_screen_min": 40, "planned_bed_shift_min": 0,
    }
    day = score_day(today, me)
    week = [{"nature_min_today": 15, "resistance_min_today": 40, "sauna_today": 0, "steps": 9100,
             "day_light_min": 44, "vilpa_min": 2, "social_index": 58},
            {"nature_min_today": 20, "resistance_min_today": 0, "sauna_today": 0, "steps": 8500,
             "day_light_min": 30, "vilpa_min": 1, "social_index": 40},
            {"nature_min_today": 0, "resistance_min_today": 0, "sauna_today": 0, "steps": 7900,
             "day_light_min": 20, "vilpa_min": 0, "social_index": 62}]
    ledger = weekly_ledger(week, me)
    fc = forecast_tonight(today, baseline_sleep_h=7.5, profile=me)
    lv = levers(today, me)

    # attribution on synthetic 30-day data: green minutes → next-night ln HRV
    rng = np.random.default_rng(1)
    green = rng.uniform(0, 60, 30)
    strain = rng.uniform(5, 15, 30)
    wd = np.arange(30) % 7
    ln_hrv = 3.7 + 0.004 * green - 0.01 * strain + rng.normal(0, 0.05, 30)
    eff = attribute(green, ln_hrv, covariates=strain, weekday=wd, prior_beta=0.002, prior_se=0.003,
                    exposure_name="green-view minutes", outcome_name="next-night ln HRV")

    tips = insights(day, ledger, fc, lv, [eff])
    payload = to_payload(day, ledger, fc, lv, tips)
    print(json.dumps({k: payload[k] for k in ("overall", "layers", "years_delta", "years_ci", "hours_today", "hours_ci")}, indent=1))
    print("\nLEVERS")
    for l in lv:
        print(f"  {l.roi_hours_per_min*60:5.2f} h/h  +{l.hours_gain:.2f} h  {l.action}")
    print("\nLEDGER")
    for l in ledger:
        print(f"  {l.label:24s} {l.accrued:>7g} / {l.target:<6g} proj {l.projected:<7g} {l.status}")
    print("\nFORECAST", fc)
    print("\nATTRIBUTION", eff)
    print("\nINSIGHTS")
    for t in tips:
        print(f"  [{t['kind']}] {t['text']}")

    # sanity: athlete profile re-weights, worst/best bounds hold
    ath = Profile(age=20, sex="M", goal="athlete")
    assert 0 <= score_day(today, ath).overall <= 100
    best = {k: FACTORS[k].best_dose() for k in FACTORS}
    worst = {k: FACTORS[k].worst_dose() for k in FACTORS}
    assert score_day(best, me).overall > 99 and score_day(worst, me).overall < 1
    print("\nself-test ok")
