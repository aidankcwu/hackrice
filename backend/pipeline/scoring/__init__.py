"""Scoring: SPEC §8 thresholds as data, plus the rough model over them (§10)."""

from .healthspan import healthspan_for_day
from .scorer import Scorer, rollup
from .thresholds import GRADE_WEIGHTS, MetricSpec, THRESHOLDS, by_period, by_source

__all__ = [
    "Scorer",
    "rollup",
    "healthspan_for_day",
    "MetricSpec",
    "THRESHOLDS",
    "GRADE_WEIGHTS",
    "by_period",
    "by_source",
]
