"""build_reasoner_extras: DECIDER switch -> decider, writers, settings. No network."""

from __future__ import annotations

import logging

import pytest

from pipeline.actions.speech import SpeechLimiter
from pipeline.config import Settings
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.reasoner.client import FakeReasonerClient, OpenAIReasonerClient
from pipeline.reasoner.decider import JevDecider
from pipeline.reasoner.decider_settings import DeciderSettings
from pipeline.reasoner.factory import ReasonerExtras, build_reasoner_extras
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.writers import Writers


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(DeciderSettings.model_fields) + ["T1_MODEL"]:
        for key in (name.upper(), name):
            monkeypatch.delenv(key, raising=False)


def openai_client() -> OpenAIReasonerClient:
    return OpenAIReasonerClient(api_key="sk-dummy", model="gpt-dummy")


def test_default_settings_is_clerk() -> None:
    extras = build_reasoner_extras()
    assert extras.decider is None and extras.writers is None
    assert extras.settings.decider == "clerk"


def test_clerk() -> None:
    settings = DeciderSettings(decider="clerk", typesafe_api_key="k")
    extras = build_reasoner_extras(settings, openai_client())
    assert (extras.decider, extras.writers, extras.settings) == (None, None, settings)


def test_jev_without_key_uses_clerk(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    extras = build_reasoner_extras(DeciderSettings(decider="jev"), openai_client())
    assert (extras.decider, extras.writers) == (None, None)
    assert "using the clerk" in caplog.text


def test_jev_with_key_and_openai_client() -> None:
    settings = DeciderSettings(decider="jev", typesafe_api_key="ts-dummy")
    extras = build_reasoner_extras(settings, openai_client())
    assert isinstance(extras.decider, JevDecider)
    assert isinstance(extras.writers, Writers)
    assert extras.settings is settings


@pytest.mark.parametrize("client", [FakeReasonerClient(), None])
def test_jev_with_fake_client_has_no_writers(
    client, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pipeline.reasoner.factory")
    settings = DeciderSettings(decider="jev", typesafe_api_key="ts-dummy")
    extras = build_reasoner_extras(settings, client)
    assert isinstance(extras.decider, JevDecider)
    assert extras.writers is None
    infos = [r for r in caplog.records
             if r.name == "pipeline.reasoner.factory" and r.levelno == logging.INFO]
    assert len(infos) == 1 and "only annotate" in infos[0].getMessage()


def test_as_kwargs_keys() -> None:
    extras = build_reasoner_extras()
    assert set(extras.as_kwargs()) == {"decider", "writers", "decider_settings"}
    with pytest.raises(Exception):
        extras.decider = None  # type: ignore[misc]  # frozen


@pytest.mark.parametrize("decider", ["clerk", "jev"])
def test_reasoner_accepts_kwargs(decider: str) -> None:
    settings = Settings(demo_mode=True, openai_api_key=None)
    extras: ReasonerExtras = build_reasoner_extras(
        DeciderSettings(decider=decider, typesafe_api_key="ts-dummy"), openai_client()
    )
    db = Database(":memory:").connect().init_schema()
    try:
        reasoner = Reasoner(
            db, InMemoryFrameStore(ttl_s=90.0), FakeReasonerClient(),
            SpeechLimiter(settings.timings.speech_min_gap,
                          settings.timings.speech_max_per_hour),
            settings, **extras.as_kwargs(),
        )
        assert reasoner.decider is extras.decider
    finally:
        db.close()
