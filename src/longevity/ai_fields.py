"""The T0 AI field set (SPEC §9) — the single source of truth.

§9 is flagged in CLAUDE.md as AI-generated and not fully reasoned: "expect the field
list to change." PERSON_A.md A2 settles it operationally — build against §9 exactly as
written, but keep the list in *one* place so a change costs one edit rather than a
search across the pipeline. That place is this module.

Everything derived from the field set lives here too: the Gemini structured-output
schema, the defaults, and the coercion of a raw model response into a tick `ai` block.
Nothing else in the codebase should name a §9 field or enum value.
"""

from __future__ import annotations

from typing import Any

# --- Enums (§9) ---------------------------------------------------------------
# "Booleans default to false; enums include `unknown`."  Note `food_type` is the one
# exception: it has no `unknown`, its null value is `none`.

# The menus are deliberately long. A VLM picking from a closed list costs the same
# whether the list has 11 entries or 32 -- one short token either way -- but a wearer
# in a dorm room tagged `home` and a wearer in a lecture hall tagged `unknown` are
# both information we threw away. More options, no free text, same latency.
#
# `unknown` stays last in every list that has it (`food_type`'s null value is `none`).

SCENE = [
    # Indoor, by what the place is for.
    "home", "office", "classroom", "library", "lab",
    "restaurant", "cafe", "bar", "gym", "store", "grocery_store",
    "hospital", "hotel",
    # Outdoor.
    "park", "trail", "campus", "street", "parking_lot", "beach",
    "nature", "sports_venue", "construction_site",
    # In transit.
    "car", "public_transit", "airport",
    # Recovery, and the reason this project cares about them (SPEC §7).
    "sauna", "cold_plunge",
    # Catch-alls. A frame that is plainly indoors should never fall to `unknown`
    # just because the room has no name on the list -- indoor/outdoor is the one
    # distinction the scorer cannot reconstruct from anything else.
    "indoor_other", "outdoor_other",
    "unknown",
]

ACTIVITY = [
    # Posture and locomotion.
    "seated", "standing", "walking", "running", "cycling", "driving",
    # Deliberate exercise, as distinct from incidental movement above.
    "exercising", "lifting_weights", "stretching",
    # Intake.
    "eating", "drinking", "cooking",
    # Focused attention. `computer_use` replaces the narrower `typing`.
    "reading", "computer_use", "phone_use",
    # Social and errands.
    "talking", "shopping", "cleaning",
    # Rest.
    "lying_down", "sleeping",
    # Everything else. `other` means "seen but not on this list"; `unknown`
    # means "could not tell" -- the scorer should treat them differently.
    "personal_care", "commuting", "other",
    "unknown",
]

FOOD_TYPE = [
    # Whole-food groups.
    "vegetables", "fruit", "grains", "beans_legumes", "fish", "seafood",
    "poultry", "red_meat", "eggs", "dairy", "nuts",
    # Dish-shaped options: what a plate actually looks like in a frame, which a
    # VLM can see directly and does not have to reason its way back to a food group.
    "salad", "sandwich", "burger", "pizza", "pasta", "rice_bowl", "noodles",
    "soup", "wrap_taco", "breakfast",
    # Processed and prepared.
    "processed", "fried_food", "fast_food", "snack", "chips", "candy",
    "baked_goods", "cereal", "protein_bar", "sweets", "dessert",
    # Staples that carry no verdict on their own.
    "bread", "potatoes",
    "mixed", "none",
]

DRINK = [
    "none", "water", "coffee", "tea", "energy_drink", "soda", "alcohol",
    "juice", "smoothie", "milk", "sports_drink", "boba",
    # Named alcohols, so the caption does not have to carry the distinction.
    "beer", "wine", "cocktail",
    "unknown",
]

# --- Named groupings ----------------------------------------------------------
# Downstream (the trigger gate, the episode builder, the scorer) switches on these
# enums by *family*, never by a single literal. The families live here so that
# adding one more scene is one edit, not a grep across two codebases. Person B's
# `pipeline.models` mirrors these exactly and a test asserts the two agree.

#: Scenes that count as being outside (the `outdoor_sustained` trigger, §7 nature).
OUTDOOR_SCENES = frozenset({
    "park", "trail", "campus", "street", "parking_lot", "beach",
    "nature", "sports_venue", "construction_site", "outdoor_other",
})

#: Scenes that count as being at home -- every room of one.
HOME_SCENES = frozenset({"home"})

#: Activities that explain an elevated heart rate on their own (SPEC §14.3).
EXERTION_ACTIVITIES = frozenset({
    "exercising", "walking", "running", "lifting_weights", "cycling", "stretching",
})

#: `food_type` values counted as on-pattern for PREDIMED-style diet scoring.
HEALTHY_FOOD_TYPES = frozenset({
    "vegetables", "fruit", "grains", "beans_legumes", "fish", "seafood",
    "poultry", "salad", "rice_bowl", "soup", "eggs", "nuts", "mixed",
})

#: Explicitly off-pattern. Everything in neither set (`sandwich`, `pasta`,
#: `red_meat`, `dairy`, `cereal`, `noodles`, `protein_bar`, `wrap_taco`,
#: `breakfast`, `snack`, `bread`, `potatoes`, `none`) is neutral. `snack` is
#: deliberately neutral: a handful of nuts and a bag of chips are both snacks.
UNHEALTHY_FOOD_TYPES = frozenset({
    "processed", "fried_food", "sweets", "chips", "candy", "baked_goods",
    "fast_food", "dessert", "burger", "pizza",
})

#: Drinks that set `caffeine_visible` when the model did not report the flag.
CAFFEINE_DRINKS = frozenset({"coffee", "tea", "energy_drink", "boba"})

#: Drinks that set `alcohol_visible` the same way.
ALCOHOL_DRINKS = frozenset({"alcohol", "beer", "wine", "cocktail"})

BOOL_FIELDS = [
    "food_present",
    "caffeine_visible",
    "alcohol_visible",
    "screen_present",
    "vegetation_visible",
    "people_present",
]

ENUM_FIELDS = {
    "scene": SCENE,
    "activity": ACTIVITY,
    "food_type": FOOD_TYPE,
    "drink": DRINK,
}

# Field order as it appears in the §12 example tick, for readable JSON output.
FIELD_ORDER = [
    "scene", "activity", "food_present", "food_type", "caffeine_visible",
    "alcohol_visible", "screen_present", "vegetation_visible", "people_present",
    "caption", "objects", "drink", "conf",
]

# `conf` appears in the §12 tick example but is absent from §9's field table. We ask
# the model for it, because §12 is the contract Person B consumes and §12.2 tells B to
# weigh confidence. If §9 is ever reconciled, this is the line to revisit.
DEFAULTS: dict[str, Any] = {
    "scene": "unknown",
    "activity": "unknown",
    "food_type": "none",
    "caption": "",
    "objects": [],
    "drink": "none",
    "conf": 0.0,
    **{f: False for f in BOOL_FIELDS},
}

# --- Prompt -------------------------------------------------------------------

PROMPT = (
    "You are tagging a single first-person photo from smart glasses for a health "
    "tracker. Report only what is plainly visible in this frame. Do not infer, "
    "remember, or guess from context. If something is unclear, use the unknown / "
    "false / none value rather than a confident answer.\n\n"
    "conf is your overall confidence in this whole tagging, from 0.0 to 1.0.\n"
    "caption is one short phrase of at most 12 words describing what is plainly visible.\n"
    "objects lists up to 5 lowercase nouns that are plainly visible.\n"
    "scene is the most specific matching location from this list.\n"
    "activity is the most specific matching thing the wearer is doing from this list.\n"
    "food_type is the most specific matching food visible from this list, or none.\n"
    "drink identifies the plainly visible drink, or none when no drink is visible."
)

# --- Structured-output schema -------------------------------------------------


def response_schema() -> dict[str, Any]:
    """OpenAPI-subset schema for Gemini structured output (`response_schema`)."""
    props: dict[str, Any] = {
        "scene": {"type": "STRING", "enum": SCENE},
        "activity": {"type": "STRING", "enum": ACTIVITY},
        "food_present": {"type": "BOOLEAN"},
        "food_type": {"type": "STRING", "enum": FOOD_TYPE},
        "caption": {"type": "STRING"},
        "objects": {"type": "ARRAY", "items": {"type": "STRING"}, "maxItems": 5},
        "drink": {"type": "STRING", "enum": DRINK},
        "conf": {"type": "NUMBER"},
    }
    for f in BOOL_FIELDS:
        props.setdefault(f, {"type": "BOOLEAN"})
    return {
        "type": "OBJECT",
        "properties": props,
        "required": FIELD_ORDER,
        "propertyOrdering": FIELD_ORDER,
    }


# --- Coercion -----------------------------------------------------------------


def coerce(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise a raw model response into a valid §9 block.

    Never raises. An out-of-vocabulary enum or a missing key falls back to the §9
    default rather than propagating a bad value into the tick stream: a wrong tag is
    a trigger misfire on B's side, but an exception here stops the 1 Hz clock.
    """
    raw = raw or {}
    out: dict[str, Any] = {}

    for name, allowed in ENUM_FIELDS.items():
        val = raw.get(name)
        out[name] = val if isinstance(val, str) and val in allowed else DEFAULTS[name]

    for name in BOOL_FIELDS:
        out[name] = bool(raw.get(name, False))

    caption = raw.get("caption", "")
    out["caption"] = "" if caption is None else str(caption)[:100]

    objects = raw.get("objects", [])
    if not isinstance(objects, list):
        objects = []
    out["objects"] = [str(item).strip().lower()[:40] for item in objects[:5]]

    if "caffeine_visible" not in raw and out["drink"] in CAFFEINE_DRINKS:
        out["caffeine_visible"] = True
    if "alcohol_visible" not in raw and out["drink"] in ALCOHOL_DRINKS:
        out["alcohol_visible"] = True

    try:
        out["conf"] = max(0.0, min(1.0, float(raw.get("conf", 0.0))))
    except (TypeError, ValueError):
        out["conf"] = 0.0

    return {k: out[k] for k in FIELD_ORDER}
