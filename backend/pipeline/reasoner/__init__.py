"""T1 -- the reasoner (SPEC §4).

One structured LLM call per escalation, interleaved text and images, a fixed
action set, and a decision row for every escalation including the silent and
dropped ones.

    reasoner = Reasoner(db, frame_store, make_client(settings, "fake"),
                        SpeechLimiter(*...), settings)
    if not reasoner.try_escalate(esc):
        ...  # dropped on contention; already logged
"""

from __future__ import annotations

from .client import (
    FakeReasonerClient,
    OpenAIReasonerClient,
    ReasonerClient,
    make_client,
)
from .envelope import build_envelope, select_frames, tick_table
from .evidence import EvidenceStore
from .prompts import DEFAULT_PERSONA, NO_SEVEN_DAY
from .reasoner import FRAMES_PER_ESCALATION, Reasoner
from .schema import (
    T1_JSON_SCHEMA,
    T1_TEXT_FORMAT,
    Action,
    AnnotateAction,
    LogInsightAction,
    NothingAction,
    SpeakAction,
    T1Response,
    WatchAction,
    normalize,
)

__all__ = [
    "Reasoner",
    "FRAMES_PER_ESCALATION",
    "ReasonerClient",
    "OpenAIReasonerClient",
    "FakeReasonerClient",
    "make_client",
    "EvidenceStore",
    "build_envelope",
    "select_frames",
    "tick_table",
    "T1Response",
    "Action",
    "SpeakAction",
    "LogInsightAction",
    "AnnotateAction",
    "WatchAction",
    "NothingAction",
    "normalize",
    "T1_JSON_SCHEMA",
    "T1_TEXT_FORMAT",
    "DEFAULT_PERSONA",
    "NO_SEVEN_DAY",
]
