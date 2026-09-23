"""Sound cue as an ``act`` kind with its own hourly limiter (docs/PERCEPTION.md)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from longevity import wire
from pipeline.actions.sound import (
    SOUND_KIND,
    SOUND_NAMES,
    SoundLimiter,
    drop_sound_if_speaking,
    is_sound_act,
    sound_act,
)
from pipeline.reasoner.schema import SpeakAction


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_constants():
    assert SOUND_KIND == "sound"
    assert SOUND_NAMES == ("chime", "tick", "soft")


def test_limiter_allows_up_to_max_then_refuses():
    lim = SoundLimiter(3)
    assert [lim.allow(t) for t in (0.0, 1.0, 2.0)] == [True, True, True]
    assert lim.allow(3.0) is False
    assert lim.stats() == {"allowed": 3, "suppressed": 1, "in_last_hour": 3}


def test_limiter_window_slides_with_fake_clock():
    clock = FakeClock(100.0)
    lim = SoundLimiter(2, clock=clock)
    assert lim.allow() and lim.allow()
    assert lim.remaining() == 0
    clock.t = 100.0 + 3599.0
    assert lim.allow() is False
    clock.t = 100.0 + 3600.0
    assert lim.remaining() == 2
    assert lim.allow() is True
    assert lim.remaining() == 1
    assert lim.stats() == {"allowed": 3, "suppressed": 1, "in_last_hour": 1}


def test_remaining_explicit_t():
    lim = SoundLimiter(6)
    assert lim.remaining(0.0) == 6
    lim.allow(10.0)
    assert lim.remaining(20.0) == 5
    assert lim.remaining(10.0 + 3600.0) == 6


def test_zero_cap_never_allows():
    lim = SoundLimiter(0)
    assert lim.allow(0.0) is False
    assert lim.remaining(0.0) == 0


@pytest.mark.parametrize("name", SOUND_NAMES)
def test_sound_act_shape(name):
    act = sound_act(name)
    assert act == {"type": "act", "kind": "sound", "args": {"name": name}}
    assert sound_act(name, act_id="a1")["id"] == "a1"


def test_sound_act_fits_the_wire():
    act = sound_act("chime", act_id="a1")
    msg = json.loads(wire.act_message(act["id"], act["kind"], act["args"]))
    assert msg["type"] == "act"
    assert msg["id"] == "a1"
    assert msg["kind"] == "sound"
    assert msg["args"] == {"name": "chime"}


def test_sound_act_rejects_unknown_name():
    with pytest.raises(ValueError):
        sound_act("klaxon")


def test_is_sound_act_dict_and_object():
    assert is_sound_act(sound_act("tick"))
    assert is_sound_act({"kind": "sound", "args": {"name": "soft"}})
    assert is_sound_act(SimpleNamespace(type="act", kind="sound", args={"name": "chime"}))
    assert is_sound_act(SimpleNamespace(kind="sound"))
    assert not is_sound_act({"type": "act", "kind": "calendar_block", "args": {}})
    assert not is_sound_act(SimpleNamespace(type="act", kind="app_shield"))
    assert not is_sound_act(SpeakAction(text="hi"))
    assert not is_sound_act({"type": "speak", "text": "hi"})


def test_drop_sound_if_speaking():
    sound = sound_act("chime")
    shield = {"type": "act", "kind": "app_shield", "args": {}}
    note = {"type": "log_insight", "text": "x"}
    speak = SpeakAction(text="go outside")

    kept = drop_sound_if_speaking([note, sound, shield, speak])
    assert kept == [note, shield, speak]

    obj_sound = SimpleNamespace(type="act", kind="sound", args={"name": "tick"})
    kept = drop_sound_if_speaking([obj_sound, {"type": "speak", "text": "hi"}, shield])
    assert kept == [{"type": "speak", "text": "hi"}, shield]


def test_drop_sound_without_speak_is_unchanged():
    actions = [sound_act("soft"), {"type": "act", "kind": "app_shield", "args": {}},
               sound_act("tick")]
    assert drop_sound_if_speaking(actions) == actions
    assert drop_sound_if_speaking([]) == []
