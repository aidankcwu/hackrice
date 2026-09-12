"""Synthetic tick source.

Person A owns real capture (SPEC §13.1). Until the `replay` corpus and the
glasses path exist, this package fabricates a plausible tick stream so the whole
downstream pipeline -- gate, episodes, T1, scoring, dashboard -- can be built and
demoed. It is a development scaffold, not part of the demo path.
"""

from __future__ import annotations

from .scenario import DEFAULT_SCENARIO, Scenario, Segment
from .source import SimSource

__all__ = ["Scenario", "Segment", "DEFAULT_SCENARIO", "SimSource"]
