"""CaptureSettings: defaults, env overrides and the WATCH_THRESHOLDS_JSON rules."""

from __future__ import annotations

import logging

import pytest

from longevity.ai_fields import BOOL_FIELDS
from pipeline.capture.settings import POINT_CONCEPTS, CaptureSettings

_ENV = (
    "WATCHER", "WATCHER_FPS_MAX", "WATCH_K", "WATCH_THRESHOLDS_JSON",
    "GATE_READS_WATCH", "LABELER_MAX_PER_HOUR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    s = CaptureSettings()
    assert s.watcher is True
    assert s.watcher_model == "mobileclip2-s0"
    assert s.watcher_fps_max == 7.0
    assert (s.watch_k, s.watch_n) == (2, 3)
    assert s.watch_cooldown_s == 30.0
    assert s.labeler_max_per_hour == 600
    assert s.labeler_error_backoff_n == 5
    assert s.gate_reads_watch is False
    th = s.thresholds()
    assert set(th) == set(BOOL_FIELDS)
    assert all(v == (0.60, 0.40) for v in th.values())


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCHER", "0")
    monkeypatch.setenv("GATE_READS_WATCH", "true")
    monkeypatch.setenv("WATCHER_FPS_MAX", "2.5")
    monkeypatch.setenv("WATCH_K", "1")
    monkeypatch.setenv("LABELER_MAX_PER_HOUR", "100")
    s = CaptureSettings()
    assert s.watcher is False
    assert s.gate_reads_watch is True
    assert s.watcher_fps_max == 2.5
    assert s.watch_k == 1
    assert s.labeler_max_per_hour == 100


def test_json_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCH_THRESHOLDS_JSON", '{"food_present": {"enter": 0.7, "exit": 0.5}}')
    th = CaptureSettings().thresholds()
    assert th["food_present"] == (0.7, 0.5)
    assert th["screen_present"] == (0.60, 0.40)


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]"])
def test_invalid_json_uses_defaults(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("WATCH_THRESHOLDS_JSON", raw)
    with caplog.at_level(logging.WARNING):
        th = CaptureSettings().thresholds()
    assert all(v == (0.60, 0.40) for v in th.values())
    assert "WATCH_THRESHOLDS_JSON" in caplog.text


def test_exit_not_below_enter_is_ignored(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(
        "WATCH_THRESHOLDS_JSON",
        '{"food_present": {"enter": 0.5, "exit": 0.5}, "screen_present": {"enter": 0.8, "exit": 0.3}}',
    )
    with caplog.at_level(logging.WARNING):
        th = CaptureSettings().thresholds()
    assert th["food_present"] == (0.60, 0.40)
    assert th["screen_present"] == (0.8, 0.3)
    assert "food_present" in caplog.text


def test_unknown_concept_is_ignored(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("WATCH_THRESHOLDS_JSON", '{"unicorn_visible": {"enter": 0.9, "exit": 0.1}}')
    with caplog.at_level(logging.WARNING):
        th = CaptureSettings().thresholds()
    assert "unicorn_visible" not in th
    assert "unicorn_visible" in caplog.text


def test_point_concepts_are_bool_fields() -> None:
    # A renamed §9 field must fail here, not silently drop out of the watcher.
    assert POINT_CONCEPTS <= set(BOOL_FIELDS)


def test_gate_reads_watch_fails_closed_on_a_typo(monkeypatch):
    """An off-by-default switch must not turn on because of a typo."""

    monkeypatch.setenv("GATE_READS_WATCH", "ture")
    assert CaptureSettings().gate_reads_watch is False
    monkeypatch.setenv("GATE_READS_WATCH", "1")
    assert CaptureSettings().gate_reads_watch is True
    monkeypatch.setenv("GATE_READS_WATCH", "off")
    assert CaptureSettings().gate_reads_watch is False


def test_underscore_keys_are_metadata_not_concepts(monkeypatch, caplog):
    """probe_watcher --out carries a _meta key; it must not warn or change anything."""

    monkeypatch.setenv("WATCH_THRESHOLDS_JSON", '{"_meta": {"model": "fake"}, "food_present": {"enter": 0.7, "exit": 0.5}}')
    with caplog.at_level("WARNING"):
        th = CaptureSettings().thresholds()
    assert th["food_present"] == (0.7, 0.5)
    assert "_meta" not in caplog.text
