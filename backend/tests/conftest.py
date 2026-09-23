from __future__ import annotations

import os

# The local backend/.env is a demo switchboard (FIXED_LINES_ONLY=1 etc.); the
# suite never reads it (_never_read_the_local_dotenv), but a shell can export
# the same switches. Pin the demo-only ones off so the suite tests the code,
# not tonight's demo.
os.environ.setdefault("FIXED_LINES_ONLY", "0")
# Auth off for the suite even if the shell exports a deploy token; blank (not
# absent) so a later load_dotenv(override=False) cannot turn it back on.
# test_auth.py turns it on per test.
os.environ["API_TOKEN"] = ""
# Hosted-deployment presets (deploy/): a .env copied from a tester's container
# must not lock the suite behind a token, reset its databases or swap its
# persona. Assigned, not setdefault: these change what a test observes.
for _hosted in ("ACCESS_TOKEN", "PERSONA_FILE", "ROOT_PATH"):
    os.environ[_hosted] = ""
os.environ["DEMO_RESET_ON_START"] = "0"

import functools
import sys
import time
from pathlib import Path

import dotenv.main
import pytest

from pipeline.config import Settings
from pipeline.models import AiBlock, DeviceBlock, SensorBlock, Tick
from pipeline.reasoner.effort import reset_effort_memory

_REAL_LOAD_DOTENV = dotenv.main.load_dotenv


@functools.wraps(_REAL_LOAD_DOTENV)
def _no_local_dotenv(dotenv_path=None, stream=None, *args, **kwargs) -> bool:
    """``load_dotenv`` for the test session. A file named ``.env`` (or dotenv's
    own search for one) is a developer's local settings -- the capture bridge
    reads backend/.env, the wearable clients ./.env -- and is never exported
    into a test; a stream or any other file loads as usual. The original is
    ``__wrapped__``."""

    if stream is None and (not dotenv_path or Path(dotenv_path).name == ".env"):
        return False
    return _REAL_LOAD_DOTENV(dotenv_path, stream, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _never_read_the_local_dotenv():
    """The suite never depends on a developer's backend/.env.

    Two readers. bridge.py:46 load_dotenv()s it into os.environ (API keys,
    API_TOKEN, AUTOPILOT_*); modules bound ``load_dotenv`` by name at import,
    so rebind every copy already imported and the dotenv attributes later
    imports will bind. And a plain ``Settings()`` reads ./.env through its
    ``model_config``; that becomes no file. A test of .env loading passes its
    own: ``Settings(_env_file=path)``."""

    with pytest.MonkeyPatch.context() as mp:
        for module in list(sys.modules.values()):
            if getattr(module, "__dict__", {}).get("load_dotenv") is _REAL_LOAD_DOTENV:
                mp.setattr(module, "load_dotenv", _no_local_dotenv)
        mp.setitem(Settings.model_config, "env_file", None)
        yield


@pytest.fixture(autouse=True)
def _restore_environ():
    """Whatever a test does to os.environ ends with that test: keys it added
    are removed, keys it changed or deleted are put back."""

    before = dict(os.environ)
    yield
    for key in set(os.environ) - before.keys():
        del os.environ[key]
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


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
