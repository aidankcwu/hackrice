"""conftest.py keeps the environment per test and the developer's .env out.

The capture bridge load_dotenv()s the real backend/.env into os.environ
(bridge.py:46). Nothing undid it, so every later test ran with the developer's
API keys, API_TOKEN and demo switches -- test_config's frozen demo preset
failed only in suite order. A plain Settings() read the same file too. These
run in file order: a test that changes the environment, then one that checks
the change did not outlive it.
"""

from __future__ import annotations

import os

import pytest

from pipeline.bus import TickBus
from pipeline.capture import bridge
from pipeline.config import Settings

ADDED = "BRIAN_ENV_ISOLATION_ADDED"
CANARY = "BRIAN_ENV_ISOLATION_CANARY"

#: What the first test saw before it changed anything; the second compares.
_before: dict[str, str | None] = {}


def test_a_test_may_change_the_environment_directly() -> None:
    _before.update(
        API_TOKEN=os.environ.get("API_TOKEN"),
        FIXED_LINES_ONLY=os.environ.get("FIXED_LINES_ONLY"),
    )
    # conftest sets both before anything imports, so both exist to change.
    assert None not in _before.values()

    os.environ[ADDED] = "1"
    os.environ["API_TOKEN"] = "a-developer-token"  # would turn the auth gate on
    del os.environ["FIXED_LINES_ONLY"]


def test_the_next_test_sees_the_environment_as_it_was() -> None:
    if not _before:
        pytest.skip("checks what test_a_test_may_change_the_environment_directly left")
    assert ADDED not in os.environ
    assert os.environ.get("API_TOKEN") == _before["API_TOKEN"]
    assert os.environ.get("FIXED_LINES_ONLY") == _before["FIXED_LINES_ONLY"]


def _build_capture(tmp_path) -> bridge.LongevityCapture:
    # A replay source does not touch its directory until it runs.
    return bridge.LongevityCapture(
        Settings(_env_file=None, db_path=tmp_path / "unused.db"),  # type: ignore[call-arg]
        source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=50, loop=False,
        camera=0, vlm="fake", flow=None,
    )


def test_a_value_in_the_backend_dotenv_never_reaches_the_suite(tmp_path, monkeypatch) -> None:
    fake_backend = tmp_path / "backend"
    (fake_backend / "pipeline" / "capture").mkdir(parents=True)
    (fake_backend / ".env").write_text(f"{CANARY}=from-backend-dotenv\n", encoding="utf-8")
    # bridge.py finds backend/.env from its own __file__; point it at the fake tree.
    monkeypatch.setattr(bridge, "__file__", str(fake_backend / "pipeline" / "capture" / "bridge.py"))
    assert CANARY not in os.environ

    _build_capture(tmp_path)
    assert CANARY not in os.environ

    # Control: with the loader production uses, the same build exports the value,
    # so the conftest guard -- not a wrong path -- is what kept it out above.
    monkeypatch.setattr(bridge, "load_dotenv", bridge.load_dotenv.__wrapped__)
    _build_capture(tmp_path)
    assert os.environ[CANARY] == "from-backend-dotenv"


def test_a_value_the_control_exported_did_not_outlive_its_test() -> None:
    assert CANARY not in os.environ


def test_a_value_in_the_backend_dotenv_never_reaches_a_plain_settings(tmp_path, monkeypatch) -> None:
    fake_backend = tmp_path / "backend"
    fake_backend.mkdir()
    (fake_backend / ".env").write_text("T1_MODEL=from-backend-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(fake_backend)  # Settings reads .env from the working directory
    monkeypatch.delenv("T1_MODEL", raising=False)  # the environment wins over the file

    assert Settings().t1_model == Settings.model_fields["t1_model"].default

    # Control: a test that means to read a .env passes it and gets the value.
    assert Settings(_env_file=".env").t1_model == "from-backend-dotenv"  # type: ignore[call-arg]
