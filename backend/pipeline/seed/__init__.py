"""The fixed 7-day synthetic dataset and its loader (SPEC §6, §7)."""

from typing import Any

from .fixtures import days_ending, seed_live_episodes, seed_rows

__all__ = [
    "days_ending",
    "seed_rows",
    "seed_live_episodes",
    "seed_database",
    "seven_day_summary",
]


def __getattr__(name: str) -> Any:
    # Imported lazily so ``python -m pipeline.seed.generate`` does not load the
    # module twice (runpy warns when the package __init__ imports it first).
    if name in ("seed_database", "seven_day_summary"):
        from . import generate

        return getattr(generate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
