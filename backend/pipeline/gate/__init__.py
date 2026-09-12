"""Synchronous trigger gate."""

from .gate import TriggerGate
from .triggers import Trigger, default_triggers

__all__ = ["Trigger", "TriggerGate", "default_triggers"]
