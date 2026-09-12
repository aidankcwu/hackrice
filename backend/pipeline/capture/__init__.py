"""Adapters joining Person A's capture pipeline to the backend."""

from .bridge import LongevityCapture
from .frames import RingFrameStore

__all__ = ["LongevityCapture", "RingFrameStore"]
