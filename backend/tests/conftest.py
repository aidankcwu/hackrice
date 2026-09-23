from __future__ import annotations

import os

# The local backend/.env is a demo switchboard (FIXED_LINES_ONLY=1 etc.);
# Settings reads it, and environment variables win over the file. Pin the
# demo-only switches off so the suite tests the code, not tonight's demo.
os.environ.setdefault("FIXED_LINES_ONLY", "0")
# Auth off for the suite even if the shell exports a deploy token; blank (not
# absent) so a later load_dotenv(override=False) cannot turn it back on.
# test_auth.py turns it on per test.
os.environ["API_TOKEN"] = ""

import time

import pytest

from pipeline.models import AiBlock, DeviceBlock, SensorBlock, Tick
from pipeline.reasoner.effort import reset_effort_memory


@pytest.fixture(autouse=True)
def _forget_learned_effort():
    """The accepted reasoning effort is process-wide by design; a stub model
    that rejected it in one test must not decide the next test's first call."""

    reset_effort_memory()
    yield
    reset_effort_memory()


def make_tick(seq: int = 0, t: float | None = None, with_ai: bool = True) -> Tick:
    now = time.time() if t is None else t
    return Tick(
        tick_id=f"t_{seq:08d}",
        t=now,
        seq=seq,
        sensor=SensorBlock(
            lux_proxy=340.0,
            cct=4100.0,
            hist_spread=0.62,
            frame_delta=0.12,
            flow_mag=0.04,
            sharpness=88.0,
            phash=f"{seq:016x}",
        ),
        device=DeviceBlock(accel_rms=0.04, gps_speed=0.2),
        ai=AiBlock(as_of=now, age_ms=0, scene="office", activity="seated")
        if with_ai
        else None,
        frame_ref=f"f_{seq:08d}",
    )


@pytest.fixture
def tick_factory():
    return make_tick
