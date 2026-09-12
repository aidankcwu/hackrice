"""The speech rate limiter and the TTS seam (SPEC §4.6, §13.3).

The model proposes speech; code disposes. Everything here is gated on
**simulated** time, so a ``--speed 10`` run does not silently get ten times the
utterance budget.
"""

from __future__ import annotations

import logging

import pytest

from pipeline.actions.speech import (
    SpeechLimiter,
    clear_spoken,
    default_speak_fn,
    get_speak_fn,
    set_speak_fn,
    spoken,
)

T0 = 1_757_700_000.0


@pytest.fixture(autouse=True)
def _isolated_hook():
    clear_spoken()
    yield
    set_speak_fn(default_speak_fn)
    clear_spoken()


# -- gating ---------------------------------------------------------------


def test_the_first_utterance_is_always_allowed():
    assert SpeechLimiter(min_gap_s=600, max_per_hour=6).allow(T0) is True


def test_min_gap_is_enforced_on_simulated_time():
    limiter = SpeechLimiter(min_gap_s=600, max_per_hour=60)

    assert limiter.allow(T0) is True
    assert limiter.allow(T0 + 1) is False
    assert limiter.allow(T0 + 599) is False
    assert limiter.allow(T0 + 600) is True


def test_wall_clock_does_not_open_the_gate():
    """A denied call must not consume or reset anything but the counters."""

    limiter = SpeechLimiter(min_gap_s=600, max_per_hour=60)
    limiter.allow(T0)

    for offset in range(1, 60):
        assert limiter.allow(T0 + offset) is False

    assert limiter.last_spoken_t == T0, "denials never move the last-spoken mark"
    assert limiter.stats() == {"allowed": 1, "suppressed": 59, "in_last_hour": 1}


def test_hourly_cap_is_enforced():
    limiter = SpeechLimiter(min_gap_s=0, max_per_hour=3)

    assert [limiter.allow(T0 + i * 10) for i in range(5)] == [
        True,
        True,
        True,
        False,
        False,
    ]


def test_the_hourly_window_slides():
    limiter = SpeechLimiter(min_gap_s=0, max_per_hour=2)

    assert limiter.allow(T0) is True
    assert limiter.allow(T0 + 10) is True
    assert limiter.allow(T0 + 20) is False
    # The first utterance ages out of the trailing hour.
    assert limiter.allow(T0 + 3601) is True


def test_allow_consumes_the_slot_so_it_cannot_be_asked_twice():
    limiter = SpeechLimiter(min_gap_s=60, max_per_hour=10)

    assert limiter.allow(T0) is True
    assert limiter.allow(T0) is False


def test_a_zero_cap_silences_everything():
    assert SpeechLimiter(min_gap_s=0, max_per_hour=0).allow(T0) is False


# -- the hook -------------------------------------------------------------


def test_speak_records_the_utterance_with_simulated_time():
    limiter = SpeechLimiter(min_gap_s=0, max_per_hour=10)

    limiter.speak("Coffee this late may cost you sleep tonight.", "low", t=T0)

    assert spoken == [(T0, "Coffee this late may cost you sleep tonight.", "low")]


def test_the_default_hook_logs_at_info(caplog):
    limiter = SpeechLimiter(min_gap_s=0, max_per_hour=10)

    with caplog.at_level(logging.INFO, logger="pipeline.actions.speech"):
        limiter.speak("stand up", "normal", t=T0)

    assert "SPEAK[normal]: stand up" in caplog.text


def test_set_speak_fn_swaps_the_sink():
    received: list[tuple[str, str]] = []
    set_speak_fn(lambda text, urgency: received.append((text, urgency)))

    SpeechLimiter(min_gap_s=0, max_per_hour=10).speak("hello", "high", t=T0)

    assert received == [("hello", "high")]
    assert spoken == [(T0, "hello", "high")], "the log records either way"


def test_get_speak_fn_returns_what_was_set():
    def fn(text: str, urgency: str) -> None:
        pass

    set_speak_fn(fn)
    assert get_speak_fn() is fn


def test_a_hook_that_raises_never_reaches_the_caller(caplog):
    """docs/API.md seam §3: speak() must never raise into the action handler."""

    def angry(text: str, urgency: str) -> None:
        raise RuntimeError("ElevenLabs is down")

    set_speak_fn(angry)
    limiter = SpeechLimiter(min_gap_s=0, max_per_hour=10)

    with caplog.at_level(logging.ERROR, logger="pipeline.actions.speech"):
        limiter.speak("this must not explode", "low", t=T0)

    assert "ElevenLabs is down" in caplog.text
    assert spoken == [(T0, "this must not explode", "low")]


def test_speak_falls_back_to_the_last_granted_time():
    limiter = SpeechLimiter(min_gap_s=0, max_per_hour=10)
    limiter.allow(T0 + 42)

    limiter.speak("no explicit t", "low")

    assert spoken[0][0] == T0 + 42
