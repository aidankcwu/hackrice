"""Scoring: SPEC §8 thresholds as data, plus the rough model over them (§10)."""

from .scorer import Scorer, rollup
from .thresholds import GRADE_WEIGHTS, MetricSpec, THRESHOLDS, by_period, by_source

__all__ = [
    "Scorer",
    "rollup",
    "MetricSpec",
    "THRESHOLDS",
    "GRADE_WEIGHTS",
    "by_period",
    "by_source",
]
