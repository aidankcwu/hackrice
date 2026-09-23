"""The decider's JSON state (docs/PERCEPTION.md, "Decider and writers")."""

from __future__ import annotations

import json
import re

from longevity.ai_fields import BOOL_FIELDS

from pipeline.models import AiBlock, Episode, Escalation, SensorBlock, Tick
from pipeline.reasoner.decider import build_state, state_size_ok

T0 = 1_757_700_000.0

KEYS = {"trigger", "recent", "episodes", "today", "persona", "trends", "clock"}


def tick(seq: int, ai: AiBlock | None = None, t: float | None = None, **extra: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq:08d}",
        t=T0 + seq if t is None else t,
        seq=seq,
        sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.12, phash=f"{seq:016x}"),
        ai=ai,
        frame_ref=f"f_{seq:08d}",
        **extra,
    )


def office_ai(**flags: object) -> AiBlock:
    return AiBlock(as_of=T0, age_ms=0, scene="office", activity="seated", **flags)


def escalation(window: list[Tick], trigger: str = "food_in_frame") -> Escalation:
    return Escalation(
        trigger=trigger,
        t=window[-1].t,
        tick=window[-1],
        window=window,
        reason="food_present on 3 of the last 10 ticks",
    )


def state_for(window: list[Tick], **kw: object) -> dict:
    args: dict = dict(
        summary_lines=["09:00 coffee"], open_episodes=[], persona="p",
        seven_day="trend", now=window[-1].t + 0.5,
    )
    args.update(kw)
    return build_state(escalation(window), window, **args)


def test_keys_trigger_and_newest_first() -> None:
    window = [tick(i, office_ai(caption=f"c{i}")) for i in range(10)]
    state = state_for(window)
    assert set(state) == KEYS
    assert state["trigger"] == {
        "name": "food_in_frame", "reason": "food_present on 3 of the last 10 ticks"}
    assert len(state["recent"]) == 6
    assert [r["caption"] for r in state["recent"]] == [f"c{i}" for i in range(9, 3, -1)]
    assert state["recent"][0]["age_s"] == 0.5
    assert state["recent"][0]["scene"] == "office"
    assert state["recent"][0]["activity"] == "seated"
    json.dumps(state)


def test_true_list_follows_bool_fields_order() -> None:
    flags = {name: True for name in reversed(BOOL_FIELDS)}
    flags["screen_present"] = False
    state = state_for([tick(0, office_ai(**flags))])
    assert state["recent"][0]["true"] == [n for n in BOOL_FIELDS if n != "screen_present"]


def test_tick_without_ai_is_no_ai() -> None:
    state = state_for([tick(0, office_ai()), tick(1)])
    assert state["recent"][0] == {"age_s": 0.5, "true": [], "no_ai": True}
    assert "no_ai" not in state["recent"][1]


def test_no_sensor_numbers_or_phash() -> None:
    text = json.dumps(state_for([tick(i, office_ai()) for i in range(3)]))
    for leaked in ("phash", "lux", "frame_delta", "frame_ref", "340"):
        assert leaked not in text


def test_hot_read_from_watch_extra() -> None:
    state = state_for([tick(0, office_ai(), watch={"hot": ["food_present"]})])
    assert state["recent"][0]["hot"] == ["food_present"]
    assert state_for([tick(0, office_ai())])["recent"][0]["hot"] == []


def test_strings_are_truncated() -> None:
    ai = office_ai(caption="c" * 500, objects=["o" * 100, "cup"])
    state = state_for(
        [tick(0, ai)],
        summary_lines=["s" * 500],
        persona="p" * 5000,
        seven_day="t" * 5000,
    )
    rec = state["recent"][0]
    assert rec["caption"] == "c" * 120
    assert rec["objects"] == ["o" * 40, "cup"]
    assert state["today"] == ["s" * 160]
    assert len(state["persona"]) == 1200
    assert len(state["trends"]) == 1200


def test_today_keeps_last_lines_oldest_first() -> None:
    lines = [f"line {i}" for i in range(20)]
    state = state_for([tick(0, office_ai())], summary_lines=lines)
    assert state["today"] == lines[-12:]


def test_episodes() -> None:
    ep = Episode(id="e1", kind="meal", start_t=T0 - 600, dominant={"scene": "cafe"})
    state = state_for([tick(0, office_ai())], open_episodes=[ep])
    assert state["episodes"] == [{"kind": "meal", "minutes_open": 10.0, "label": "scene cafe"}]


def test_size_bound_holds_for_long_captions() -> None:
    window = [tick(i, office_ai(caption="x" * 400, objects=["cup", "laptop"])) for i in range(6)]
    state = state_for(window, summary_lines=["y" * 200] * 12)
    assert state_size_ok(state)
    assert len(state["recent"]) == 6 and len(state["today"]) == 12


def test_trims_oldest_today_first_when_over_budget() -> None:
    objects = [f"{i:02d}" + "o" * 60 for i in range(30)]
    window = [tick(i, office_ai(caption="x" * 400, objects=objects)) for i in range(6)]
    lines = [f"{i:02d}" + "y" * 200 for i in range(12)]
    state = state_for(window, summary_lines=lines, persona="p" * 1200, seven_day="t" * 1200)
    assert state_size_ok(state)
    assert 1 <= len(state["today"]) < 12
    assert state["today"] == [line[:160] for line in lines][-len(state["today"]):]
    json.dumps(state)


def test_trim_stops_at_one_of_each() -> None:
    objects = ["o" * 40] * 400
    window = [tick(i, office_ai(objects=objects)) for i in range(6)]
    state = state_for(window, summary_lines=["y"] * 12)
    assert not state_size_ok(state)
    assert len(state["today"]) == 1 and len(state["recent"]) == 1
    assert state["recent"][0]["age_s"] == 0.5


def test_clock_shape() -> None:
    clock = state_for([tick(0, office_ai())])["clock"]
    assert re.fullmatch(r"\d{2}:\d{2}", clock["local"])
    assert clock["weekday"] in {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
