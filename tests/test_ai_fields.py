from longevity.ai_fields import (
    ACTIVITY,
    ALCOHOL_DRINKS,
    CAFFEINE_DRINKS,
    DRINK,
    EXERTION_ACTIVITIES,
    FIELD_ORDER,
    FOOD_TYPE,
    HEALTHY_FOOD_TYPES,
    HOME_SCENES,
    OUTDOOR_SCENES,
    PROMPT,
    SCENE,
    UNHEALTHY_FOOD_TYPES,
    coerce,
    response_schema,
)


def test_richer_tags_are_in_schema_and_prompt():
    schema = response_schema()
    assert schema["required"] == FIELD_ORDER
    assert schema["properties"]["objects"]["maxItems"] == 5
    assert "caption is one short phrase" in PROMPT
    assert "objects lists up to 5" in PROMPT
    assert "drink identifies" in PROMPT


def test_richer_tags_coerce_and_bound_values():
    raw = {
        "caption": "x" * 120,
        "objects": [" Coffee Mug ", "LAPTOP", 3, "chips", "desk", "extra"],
        "drink": "coffee",
    }
    out = coerce(raw)
    assert out["caption"] == "x" * 100
    assert out["objects"] == ["coffee mug", "laptop", "3", "chips", "desk"]
    assert out["drink"] == "coffee"
    assert out["caffeine_visible"] is True
    assert out["alcohol_visible"] is False


def test_drink_derivation_preserves_explicit_false_and_defaults():
    assert coerce({"drink": "alcohol"})["alcohol_visible"] is True
    assert coerce({"drink": "tea", "caffeine_visible": False})["caffeine_visible"] is False
    out = coerce({"caption": None, "objects": "mug", "drink": "mead"})
    assert out["caption"] == ""
    assert out["objects"] == []
    assert out["drink"] == "none"


# -- the widened menus ---------------------------------------------------


def test_menus_are_descriptive_and_still_closed():
    """More options, no free text: every enum is still a fixed list in the schema.

    The schema is what keeps latency flat -- the model emits one short value per
    field whether the list has 11 entries or 32 -- so the test that matters is
    that the longer lists are still *lists*, and that the prompt still spends one
    line on each rather than explaining the taxonomy.
    """

    props = response_schema()["properties"]
    for field, menu in (
        ("scene", SCENE), ("activity", ACTIVITY),
        ("food_type", FOOD_TYPE), ("drink", DRINK),
    ):
        assert props[field] == {"type": "STRING", "enum": menu}
        assert len(menu) == len(set(menu)), f"{field} has a duplicate"

    # Nothing was dropped when the menus grew.
    assert {"home", "office", "park", "trail", "street", "unknown"} <= set(SCENE)
    assert {"seated", "walking", "exercising", "eating", "unknown"} <= set(ACTIVITY)
    assert {"vegetables", "red_meat", "mixed", "none"} <= set(FOOD_TYPE)
    assert {"water", "coffee", "alcohol", "none", "unknown"} <= set(DRINK)

    for line in ("scene is the most specific", "activity is the most specific",
                 "food_type is the most specific", "drink identifies"):
        assert sum(line in row for row in PROMPT.splitlines()) == 1


def test_named_families_are_subsets_of_their_menus():
    assert OUTDOOR_SCENES <= set(SCENE)
    assert HOME_SCENES <= set(SCENE)
    assert not OUTDOOR_SCENES & HOME_SCENES
    assert EXERTION_ACTIVITIES <= set(ACTIVITY)
    assert HEALTHY_FOOD_TYPES <= set(FOOD_TYPE)
    assert UNHEALTHY_FOOD_TYPES <= set(FOOD_TYPE)
    assert not HEALTHY_FOOD_TYPES & UNHEALTHY_FOOD_TYPES
    assert CAFFEINE_DRINKS <= set(DRINK)
    assert ALCOHOL_DRINKS <= set(DRINK)
    assert not CAFFEINE_DRINKS & ALCOHOL_DRINKS


def test_new_values_survive_coercion_and_bad_ones_still_fall_back():
    out = coerce({
        "scene": "hospital", "activity": "phone_use",
        "food_type": "rice_bowl", "drink": "boba", "conf": 0.5,
    })
    assert out["scene"] == "hospital"
    assert out["activity"] == "phone_use"
    assert out["food_type"] == "rice_bowl"
    assert out["drink"] == "boba"
    # boba is caffeinated, and the model did not report the flag itself.
    assert out["caffeine_visible"] is True

    # An out-of-vocabulary value is still the §9 default, never passed through.
    bad = coerce({
        "scene": "spaceship", "activity": "levitating",
        "food_type": "ambrosia", "drink": "nectar",
    })
    assert bad["scene"] == "unknown"
    assert bad["activity"] == "unknown"
    assert bad["food_type"] == "none"
    assert bad["drink"] == "none"
    assert bad["caffeine_visible"] is False
    assert bad["alcohol_visible"] is False


def test_every_named_alcohol_and_caffeine_drink_derives_its_flag():
    for value in ALCOHOL_DRINKS:
        assert coerce({"drink": value})["alcohol_visible"] is True, value
    for value in CAFFEINE_DRINKS:
        assert coerce({"drink": value})["caffeine_visible"] is True, value
    for value in ("water", "juice", "milk", "smoothie", "soda", "sports_drink"):
        assert coerce({"drink": value})["caffeine_visible"] is False, value
        assert coerce({"drink": value})["alcohol_visible"] is False, value
