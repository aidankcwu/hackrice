"""Tick schema round-trip against the literal example in SPEC §12."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from longevity import ai_fields
from pipeline.models import (
    ACTIVITIES,
    DRINKS,
    EXERTION_ACTIVITIES,
    FOOD_TYPES,
    HEALTHY_FOOD_TYPES,
    HOME_SCENES,
    OUTDOOR_SCENES,
    PEOPLE_COUNTS,
    SCENES,
    UNHEALTHY_FOOD_TYPES,
    AiBlock,
    SensorBlock,
    Tick,
    WatchBlock,
    phash_distance,
)

# Copied verbatim from SPEC §12. Image bytes are not part of the tick (SPEC §2.5
# rule 3: ticks contain no pixels), so there is nothing to remove here -- the
# tick carries only `frame_ref`.
SPEC_TICK_JSON = """
{
  "v": 1,
  "tick_id": "t_00001742",
  "t": 1757700842.000,
  "seq": 1742,

  "sensor": {
    "lux_proxy": 340,
    "cct": 4100,
    "hist_spread": 0.62,
    "frame_delta": 0.12,
    "flow_mag": 0.04,
    "sharpness": 88,
    "phash": "e3a91c04b7d2f855"
  },

  "device": {
    "accel_rms": 0.04,
    "gps_speed": 0.2
  },

  "ai": {
    "as_of": 1757700840.100,
    "age_ms": 1900,
    "scene": "office",
    "activity": "seated",
    "food_present": false,
    "food_type": "none",
    "caffeine_visible": true,
    "alcohol_visible": false,
    "screen_present": true,
    "vegetation_visible": false,
    "people_present": true,
    "conf": 0.83
  },

  "frame_ref": "f_00001742"
}
"""


def test_spec_tick_round_trips_exactly() -> None:
    original = json.loads(SPEC_TICK_JSON)
    tick = Tick.model_validate(original)

    assert tick.tick_id == "t_00001742"
    assert tick.seq == 1742
    assert tick.sensor.phash == "e3a91c04b7d2f855"
    assert tick.device is not None and tick.device.gps_speed == 0.2
    assert tick.ai is not None and tick.ai.scene == "office"
    assert tick.ai.caffeine_visible is True

    round_tripped = json.loads(tick.model_dump_json())
    assert round_tripped == original


def test_ai_block_may_be_absent() -> None:
    """SPEC §12.2 state 2: the tick is still valid, sensor unaffected."""

    data = json.loads(SPEC_TICK_JSON)
    del data["ai"]
    tick = Tick.model_validate(data)
    assert tick.ai is None
    assert tick.sensor.lux_proxy == 340


def test_device_block_may_be_absent() -> None:
    """SPEC §12.1: absent under webcam / replay adapters."""

    data = json.loads(SPEC_TICK_JSON)
    del data["device"]
    assert Tick.model_validate(data).device is None


def test_unknown_fields_are_tolerated() -> None:
    """SPEC §12: A may add fields freely; B must not break on them."""

    data = json.loads(SPEC_TICK_JSON)
    data["sensor"]["new_stat"] = 1.5
    data["something_new"] = "hello"
    tick = Tick.model_validate(data)
    assert tick.sensor.lux_proxy == 340


def test_bad_enum_rejected() -> None:
    data = json.loads(SPEC_TICK_JSON)
    data["ai"]["scene"] = "submarine"
    with pytest.raises(ValidationError):
        Tick.model_validate(data)


def test_phash_must_be_16_hex() -> None:
    with pytest.raises(ValidationError):
        SensorBlock(
            lux_proxy=1,
            cct=1,
            hist_spread=0,
            frame_delta=0,
            flow_mag=0,
            sharpness=1,
            phash="zzzz",
        )


def test_ai_block_defaults() -> None:
    ai = AiBlock(as_of=1.0, age_ms=0)
    assert ai.scene == "unknown"
    # Unreported flags are unknown (None), never False (plan review, SPEC §12).
    assert ai.food_type is None
    assert ai.people_present is None


def test_new_ai_booleans_round_trip_as_tri_state() -> None:
    names = (
        "people_interacting",
        "direct_sunlight_visible",
        "outdoor_visible",
        "smoking_or_vaping_visible",
        "medication_visible",
    )
    absent = AiBlock()
    present = AiBlock(**{name: True for name in names})
    absent_round_trip = AiBlock.model_validate(absent.model_dump())
    present_round_trip = AiBlock.model_validate(present.model_dump())
    for name in names:
        assert getattr(absent, name) is None
        assert getattr(absent_round_trip, name) is None
        assert getattr(present_round_trip, name) is True
        assert present.model_dump()[name] is True


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("0000000000000000", "0000000000000000", 0),
        ("0000000000000000", "0000000000000001", 1),
        ("0000000000000000", "ffffffffffffffff", 64),
        ("e3a91c04b7d2f855", "e3a91c04b7d2f854", 1),
    ],
)
def test_phash_distance(a: str, b: str, expected: int) -> None:
    assert phash_distance(a, b) == expected


def test_phash_distance_length_mismatch() -> None:
    with pytest.raises(ValueError):
        phash_distance("abcd", "abcdef")


# -- amendments from plan review: tri-state flags and freshness ----------------


def _bare_tick(ai: dict | None) -> Tick:
    return Tick(
        tick_id="t_1", t=1.0, seq=1, frame_ref="f_1",
        sensor={"lux_proxy": 1.0, "phash": "e3a91c04b7d2f855"},
        ai=ai,
    )


def test_missing_boolean_is_unknown_not_false():
    tick = _bare_tick({"as_of": 1.0, "age_ms": 100, "scene": "office"})
    assert tick.ai is not None
    assert tick.ai.food_present is None
    assert tick.flag("food_present") is None
    assert tick.enum("scene") == "office"
    assert tick.enum("activity") is None  # "unknown" reads as None


def test_stale_ai_reads_as_unknown():
    tick = _bare_tick({"as_of": 1.0, "age_ms": 5000, "food_present": True})
    assert tick.ai_fresh() is False
    assert tick.flag("food_present") is None
    assert tick.flag("food_present", max_age_ms=10_000) is True


def test_freshness_budget_is_the_callers_to_set():
    """The 3 s default is the 1 Hz contract; a slower stream widens it (S9).

    ``Timings.ai_max_age_ms`` is 3750 at the glasses' 1.5 s cadence -- a block
    3.5 s old is ~2 ticks and still evidence there, but unknown at 1 Hz. The
    model keeps the conservative default and the pipeline passes the number.
    """

    tick = _bare_tick({"as_of": 1.0, "age_ms": 3500, "scene": "office",
                       "food_present": True})
    assert tick.ai_fresh() is False
    assert tick.flag("food_present") is None
    assert tick.enum("scene") is None

    assert tick.ai_fresh(3750) is True
    assert tick.flag("food_present", 3750) is True
    assert tick.enum("scene", 3750) == "office"


def test_absent_ai_reads_as_unknown():
    tick = _bare_tick(None)
    assert tick.ai_fresh() is False
    assert tick.flag("screen_present") is None


def test_sparse_sensor_block_parses():
    tick = _bare_tick(None)
    assert tick.sensor.cct is None
    assert tick.sensor.phash == "e3a91c04b7d2f855"


# -- the seam on the enums themselves ------------------------------------


def test_enum_menus_mirror_person_as_source_of_truth() -> None:
    """A's ``longevity.ai_fields`` owns the §9 menus; these Literals are a copy.

    A copy that drifts is worse than no copy: a value A's coercion happily emits
    but this side does not list fails tick validation, and the whole tick is
    dropped at the seam. Order is asserted too -- the lists are what goes into
    the Gemini ``response_schema``, and a diff should read as one appended value.
    """

    assert list(SCENES) == ai_fields.SCENE
    assert list(ACTIVITIES) == ai_fields.ACTIVITY
    assert list(FOOD_TYPES) == ai_fields.FOOD_TYPE
    assert list(DRINKS) == ai_fields.DRINK
    assert list(PEOPLE_COUNTS) == ai_fields.PEOPLE_COUNT


def test_every_field_a_emits_is_a_declared_ai_block_field() -> None:
    """A field A adds must be mirrored here, or it rides along untyped via extra."""

    assert set(ai_fields.FIELD_ORDER) <= set(AiBlock.model_fields)


def test_coerced_block_validates_and_round_trips_the_hand_and_crowd_fields() -> None:
    raw = ai_fields.coerce({
        "scene": "office", "in_hand": "Rice Krispies Treat",
        "phone_in_hand": False, "people_count": "6+",
    })
    tick = _bare_tick({"as_of": 1.0, "age_ms": 0, **raw})
    assert tick.held() == "rice krispies treat"
    assert tick.flag("phone_in_hand") is False
    assert tick.enum("people_count") == "6+"
    assert Tick.model_validate_json(tick.model_dump_json()) == tick


def test_hand_and_crowd_fields_are_tri_state() -> None:
    # Not reported (an older producer), or reported as unknown/null: all None.
    tick = _bare_tick({"as_of": 1.0, "age_ms": 0, "scene": "office"})
    assert tick.held() is None
    assert tick.flag("phone_in_hand") is None
    assert tick.enum("people_count") is None
    assert "in_hand" not in tick.ai.model_dump()  # absent stays absent downstream
    unknown = _bare_tick({"as_of": 1.0, "age_ms": 0, "people_count": "unknown",
                          "in_hand": None, "phone_in_hand": None})
    assert unknown.enum("people_count") is None and unknown.held() is None
    # Stale ai is unknown too, even when the hand was reported.
    stale = _bare_tick({"as_of": 1.0, "age_ms": 9000, "in_hand": "cucumber",
                        "phone_in_hand": True, "people_count": "3-5"})
    assert (stale.held(), stale.flag("phone_in_hand"), stale.enum("people_count")) == (
        None, None, None)
    with pytest.raises(ValidationError):
        _bare_tick({"as_of": 1.0, "age_ms": 0, "people_count": "lots"})


def test_named_families_mirror_and_stay_inside_their_menus() -> None:
    assert OUTDOOR_SCENES == ai_fields.OUTDOOR_SCENES
    assert HOME_SCENES == ai_fields.HOME_SCENES
    assert EXERTION_ACTIVITIES == ai_fields.EXERTION_ACTIVITIES
    assert HEALTHY_FOOD_TYPES == ai_fields.HEALTHY_FOOD_TYPES
    assert UNHEALTHY_FOOD_TYPES == ai_fields.UNHEALTHY_FOOD_TYPES

    assert OUTDOOR_SCENES <= set(SCENES)
    assert HOME_SCENES <= set(SCENES)
    assert not OUTDOOR_SCENES & HOME_SCENES
    assert EXERTION_ACTIVITIES <= set(ACTIVITIES)
    assert HEALTHY_FOOD_TYPES <= set(FOOD_TYPES)
    assert UNHEALTHY_FOOD_TYPES <= set(FOOD_TYPES)
    assert not HEALTHY_FOOD_TYPES & UNHEALTHY_FOOD_TYPES


def test_widened_menus_validate_and_read_back() -> None:
    tick = _bare_tick(
        {
            "as_of": 1.0,
            "age_ms": 0,
            "scene": "hospital",
            "activity": "computer_use",
            "food_present": True,
            "food_type": "rice_bowl",
            "drink": "boba",
        }
    )
    assert tick.enum("scene") == "hospital"
    assert tick.enum("activity") == "computer_use"
    assert tick.enum("food_type") == "rice_bowl"
    assert tick.ai is not None and tick.ai.drink == "boba"


# -- watch block (docs/PERCEPTION.md "Tick") ---------------------------------

WATCH = {
    "v": 1,
    "model": "mobileclip2-s0",
    "frames": 10,
    "usable": 9,
    "scores": {"food_present": 0.71, "caffeine_visible": 0.12},
    "novelty": 0.34,
    "hot": ["food_present"],
    "woke": "food_present",
}


def _spec_with_watch() -> dict:
    data = json.loads(SPEC_TICK_JSON)
    return {**{k: v for k, v in data.items() if k not in ("ai", "frame_ref")},
            "watch": dict(WATCH), "ai": data["ai"], "frame_ref": data["frame_ref"]}


def test_watch_block_defaults() -> None:
    block = WatchBlock()
    assert (block.v, block.model, block.frames, block.usable) == (1, "", 0, 0)
    assert block.scores == {} and block.hot == [] and block.novelty == 0.0
    assert block.woke is None
    assert WatchBlock().scores is not block.scores


def test_tick_without_watch_validates_and_dumps_without_it() -> None:
    tick = Tick.model_validate(json.loads(SPEC_TICK_JSON))
    assert tick.watch is None
    assert "watch" not in json.loads(tick.model_dump_json())
    assert "watch" not in tick.model_dump()


def test_tick_with_watch_round_trips() -> None:
    original = _spec_with_watch()
    tick = Tick.model_validate(original)
    assert tick.watch is not None and tick.watch.model == "mobileclip2-s0"
    assert json.loads(tick.model_dump_json()) == original
    assert tick.model_dump()["watch"] == WATCH
    assert list(tick.model_dump()) == list(original)


def test_watch_null_woke_round_trips() -> None:
    original = _spec_with_watch()
    original["watch"]["woke"] = None
    assert json.loads(Tick.model_validate(original).model_dump_json()) == original


def test_watch_unknown_keys_ignored() -> None:
    data = _spec_with_watch()
    data["watch"]["future_stat"] = 3
    tick = Tick.model_validate(data)
    assert tick.watch is not None
    assert "future_stat" not in tick.watch.model_dump()


def test_watch_score_and_hot() -> None:
    tick = Tick.model_validate(_spec_with_watch())
    assert tick.watch_score("food_present") == 0.71
    assert tick.watch_score("screen_present") is None
    assert tick.watch_hot("food_present") is True
    assert tick.watch_hot("caffeine_visible") is False


def test_watch_helpers_without_block() -> None:
    tick = _bare_tick(None)
    assert tick.watch_score("food_present") is None
    assert tick.watch_hot("food_present") is False
