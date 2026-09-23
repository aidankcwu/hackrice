"""DeciderSettings: defaults, env overrides, topic parsing, validation."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from pipeline.config import Settings
from pipeline.reasoner.decider_settings import DEFAULT_TOPICS, DeciderSettings

_ACTIONS = {"log_insight", "remember", "watch", "speak", "ask", "act", "look"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every field's env var unset, so a developer's shell cannot leak in."""

    for name in list(DeciderSettings.model_fields) + ["T1_MODEL"]:
        for key in (name.upper(), name):
            monkeypatch.delenv(key, raising=False)


def test_defaults() -> None:
    s = DeciderSettings()
    assert s.decider == "clerk"
    assert s.typesafe_api_key is None
    assert s.jev_model == "jev-latest"
    assert s.jev_timeout_s == 2.0
    assert s.thresholds() == {
        "log_insight": 0.60, "remember": 0.70, "watch": 0.60, "speak": 0.70,
        "ask": 0.75, "act": 0.80, "look": 0.60,
    }
    assert (s.decide_uncertain_low, s.decide_uncertain_high) == (0.40, 0.60)
    assert s.writer_model == Settings.model_fields["t1_model"].default
    assert s.writer_timeout_s == 6.0
    assert s.act_sound_max_per_hour == 6
    assert s.quiet_max_s == 45.0


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDER", "jev")
    monkeypatch.setenv("DECIDE_SPEAK", "0.9")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    s = DeciderSettings()
    assert s.decider == "jev"
    assert s.decide_speak == 0.9
    assert s.thresholds()["speak"] == 0.9
    assert s.typesafe_api_key == "ts-test"


def test_thresholds_keys_exclude_annotate() -> None:
    assert set(DeciderSettings().thresholds()) == _ACTIONS


def test_topics_default() -> None:
    assert DeciderSettings().topics() == DEFAULT_TOPICS


def test_topics_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_TOPICS_JSON", '["coffee", "walks"]')
    assert DeciderSettings().topics() == ["coffee", "walks"]


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', "[]", '["ok", 3]'])
def test_topics_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("DECIDE_TOPICS_JSON", raw)
    with caplog.at_level(logging.WARNING):
        assert DeciderSettings().topics() == DEFAULT_TOPICS
    assert "DECIDE_TOPICS_JSON" in caplog.text


@pytest.mark.parametrize("value", ["1.5", "-0.1"])
def test_out_of_range_threshold_raises(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DECIDE_ACT", value)
    with pytest.raises(ValidationError):
        DeciderSettings()


def test_uncertain_band_must_be_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_UNCERTAIN_LOW", "0.6")
    monkeypatch.setenv("DECIDE_UNCERTAIN_HIGH", "0.6")
    with pytest.raises(ValidationError):
        DeciderSettings()


def test_writer_model_follows_t1_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T1_MODEL", "gpt-test-writer")
    assert DeciderSettings().writer_model == "gpt-test-writer"
