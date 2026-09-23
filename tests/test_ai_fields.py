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
    for value in ("water", "juice", "milk", "smoothie", "sports_drink"):
        assert coerce({"drink": value})["caffeine_visible"] is False, value
        assert coerce({"drink": value})["alcohol_visible"] is False, value


# -- held item, phone in hand, head count ----------------------------------


def test_new_hand_and_crowd_fields_are_in_schema_prompt_and_order():
    from longevity.ai_fields import PEOPLE_COUNT, TRISTATE_BOOL_FIELDS

    props = response_schema()["properties"]
    assert props["in_hand"] == {"type": "STRING", "nullable": True}
    assert props["phone_in_hand"] == {"type": "BOOLEAN", "nullable": True}
    assert props["people_count"] == {"type": "STRING", "enum": PEOPLE_COUNT}
    assert PEOPLE_COUNT == ["0", "1-2", "3-5", "6+", "unknown"]
    assert TRISTATE_BOOL_FIELDS == ["phone_in_hand"]
    # Generated first, so everything after it is conditioned on the held item.
    assert FIELD_ORDER[:2] == ["in_hand", "phone_in_hand"]
    assert set(props) == set(FIELD_ORDER)
    for line in ("in_hand names what is in the wearer's hand",
                 "phone_in_hand is true", "people_count buckets"):
        assert sum(line in row for row in PROMPT.splitlines()) == 1
    assert "rice krispies treat" in PROMPT and "coffee milkshake" in PROMPT


def test_every_existing_field_and_enum_value_survived_the_additions():
    """Downstream (gate, envelope, episodes, B's models) reads all of these."""
    for name in ("scene", "activity", "food_present", "food_type", "caffeine_visible",
                 "alcohol_visible", "screen_present", "vegetation_visible",
                 "people_present", "people_interacting", "direct_sunlight_visible",
                 "outdoor_visible", "smoking_or_vaping_visible", "medication_visible",
                 "caption", "objects", "drink", "conf"):
        assert name in FIELD_ORDER, name
    assert (len(SCENE), len(ACTIVITY), len(FOOD_TYPE), len(DRINK)) == (30, 24, 36, 16)


def test_in_hand_is_bounded_lowercased_and_empty_spellings_are_null():
    assert coerce({"in_hand": "  Rice Krispies   Treat "})["in_hand"] == "rice krispies treat"
    assert len(coerce({"in_hand": "x" * 200})["in_hand"]) == 60
    for empty in (None, "", "none", "None.", "nothing", "N/A", 3, ["cup"]):
        assert coerce({"in_hand": empty})["in_hand"] is None, empty
    assert coerce({})["in_hand"] is None


def test_phone_in_hand_is_tri_state_and_derived_only_when_unreported():
    assert coerce({"phone_in_hand": True})["phone_in_hand"] is True
    assert coerce({"phone_in_hand": False})["phone_in_hand"] is False
    # Unreported / null / junk is unknown, never a negative observation.
    for raw in ({}, {"phone_in_hand": None}, {"phone_in_hand": "yes"}):
        assert coerce(raw)["phone_in_hand"] is None, raw
    # A phone named in the hand fills the gap ...
    assert coerce({"in_hand": "iPhone"})["phone_in_hand"] is True
    assert coerce({"in_hand": "smart-phone"})["phone_in_hand"] is True
    # ... but never overrules an explicit answer, and other held items stay unknown.
    assert coerce({"in_hand": "phone", "phone_in_hand": False})["phone_in_hand"] is False
    assert coerce({"in_hand": "cucumber"})["phone_in_hand"] is None


def test_people_count_is_a_closed_menu_with_an_unknown_default():
    assert coerce({"people_count": "3-5"})["people_count"] == "3-5"
    assert coerce({"people_count": "6+"})["people_count"] == "6+"
    for bad in ({}, {"people_count": 4}, {"people_count": "lots"}):
        assert coerce(bad)["people_count"] == "unknown", bad


def test_coerce_output_keys_follow_field_order():
    assert list(coerce({})) == FIELD_ORDER


# -- watcher prompt bank ---------------------------------------------------


def test_every_boolean_field_has_two_to_five_watch_prompts():
    from longevity.ai_fields import BOOL_FIELDS, TRISTATE_BOOL_FIELDS, WATCH_PROMPTS

    assert set(WATCH_PROMPTS) == set(BOOL_FIELDS) | set(TRISTATE_BOOL_FIELDS)
    for name, prompts in WATCH_PROMPTS.items():
        assert isinstance(prompts, tuple), name
        assert 2 <= len(prompts) <= 5, name


def test_watch_prompts_are_short_and_unique():
    from longevity.ai_fields import WATCH_NULL_PROMPTS, watch_prompt_bank

    assert len(WATCH_NULL_PROMPTS) >= 5
    prompts = [p for _, p in watch_prompt_bank()]
    for p in prompts:
        assert p.strip() and 8 <= len(p) <= 60, p
    assert len(prompts) == len(set(prompts))


def test_watch_prompt_bank_order_is_stable_and_ends_with_null():
    from longevity.ai_fields import (
        BOOL_FIELDS, TRISTATE_BOOL_FIELDS, WATCH_NULL_PROMPTS, WATCH_PROMPTS,
        watch_prompt_bank,
    )

    bank = watch_prompt_bank()
    assert bank == watch_prompt_bank()
    expected = [(c, p) for c in BOOL_FIELDS + TRISTATE_BOOL_FIELDS
                for p in WATCH_PROMPTS[c]]
    expected += [("_null", p) for p in WATCH_NULL_PROMPTS]
    assert bank == expected
    concepts = list(dict.fromkeys(c for c, _ in bank))
    assert concepts == BOOL_FIELDS + TRISTATE_BOOL_FIELDS + ["_null"]
    assert bank[-len(WATCH_NULL_PROMPTS):] == [("_null", p) for p in WATCH_NULL_PROMPTS]
