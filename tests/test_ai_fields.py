from longevity.ai_fields import FIELD_ORDER, PROMPT, coerce, response_schema


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
    out = coerce({"caption": None, "objects": "mug", "drink": "juice"})
    assert out["caption"] == ""
    assert out["objects"] == []
    assert out["drink"] == "none"
