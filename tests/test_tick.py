"""build_tick: block placement and omission (SPEC §12, docs/PERCEPTION.md "Tick")."""

from __future__ import annotations

from longevity.tick import build_tick

SENSOR = {"lux_proxy": 0.4}
DEVICE = {"accel_rms": 0.03}
AI = {"as_of": 1.0, "age_ms": 0, "food_present": True}
WATCH = {
    "v": 1, "model": "mobileclip2-s0", "frames": 10, "usable": 9,
    "scores": {"food_present": 0.71}, "novelty": 0.34, "hot": ["food_present"], "woke": None,
}


def test_watch_sits_between_device_and_ai() -> None:
    tick = build_tick(seq=7, t=1.0, sensor=SENSOR, device=DEVICE, watch=WATCH, ai=AI)
    assert list(tick) == ["v", "tick_id", "t", "seq", "sensor", "device", "watch", "ai", "frame_ref"]
    assert tick["watch"] == WATCH


def test_watch_precedes_ai_without_device() -> None:
    tick = build_tick(seq=7, t=1.0, sensor=SENSOR, watch=WATCH, ai=AI)
    keys = list(tick)
    assert "device" not in tick
    assert keys.index("sensor") < keys.index("watch") < keys.index("ai")


def test_watch_omitted_not_null_when_none() -> None:
    tick = build_tick(seq=7, t=1.0, sensor=SENSOR, device=DEVICE, ai=AI)
    assert "watch" not in tick
    assert list(tick) == ["v", "tick_id", "t", "seq", "sensor", "device", "ai", "frame_ref"]
