"""Rules behind /api/status.health (Astra review of the batch-1 health block)."""

from __future__ import annotations

from pipeline.api.wiring import health_problems

_T1 = {"dropped_error": 0, "dropped_timeout": 0}


def _phone(**over):
    base = {"connected": 1, "latest_age_s": 1.0, "connected_for_s": 30.0}
    base.update(over)
    return base


def _problems(**over):
    kwargs = dict(source="glasses", phone=_phone(), ai_coverage=0.9, ai_ticks=40,
                  tagger={"errors": 0}, t1=_T1, t1_error_snapshot=(0, 0),
                  speech={"last_error": None})
    kwargs.update(over)
    return health_problems(**kwargs)


def test_streaming_phone_is_healthy() -> None:
    assert _problems() == []


def test_no_phone_on_glasses_source() -> None:
    assert "phone_disconnected" in _problems(phone=_phone(connected=0))
    assert "phone_disconnected" in _problems(phone=None)


def test_pings_only_phone_is_flagged_after_10s() -> None:
    quiet = _phone(latest_age_s=None, connected_for_s=12.0)
    assert "no_packets_10s" in _problems(phone=quiet)
    fresh = _phone(latest_age_s=None, connected_for_s=3.0)
    assert "no_packets_10s" not in _problems(phone=fresh)


def test_stale_frames_are_flagged() -> None:
    assert "no_packets_10s" in _problems(phone=_phone(latest_age_s=11.0))


def test_ticks_stopped_is_its_own_problem() -> None:
    assert "no_ticks_60s" in _problems(ticks_stale=True)


def test_low_coverage_needs_enough_samples() -> None:
    assert "ai_coverage_low" not in _problems(ai_coverage=0.1, ai_ticks=5)
    assert "ai_coverage_low" in _problems(ai_coverage=0.1, ai_ticks=25)


def test_non_glasses_sources_ignore_phone_rules() -> None:
    assert _problems(source="replay", phone=_phone(connected=0)) == []


def test_t1_and_tts_rules() -> None:
    assert "t1_errors" in _problems(t1={"dropped_error": 1, "dropped_timeout": 0})
    assert "tts_failing" in _problems(speech={"last_error": "boom"})
