"""Lifestyle-tracking pipeline backend.

Person B's half of the system (SPEC §13.2): everything downstream of the tick
stream. This package deliberately contains no capture code -- ticks arrive from
Person A's capture layer (or, until that exists, from :mod:`pipeline.sim`).
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
