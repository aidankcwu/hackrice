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
#: `soda` is in deliberately: caffeine-free sodas exist, but the common case is
#: caffeinated and a false negative costs more here than a false positive.
CAFFEINE_DRINKS = frozenset({"coffee", "tea", "energy_drink", "boba", "soda"})

#: Drinks that set `alcohol_visible` the same way.
ALCOHOL_DRINKS = frozenset({"alcohol", "beer", "wine", "cocktail"})

#: How many people are in frame, bucketed. `people_present` only says "anyone at
#: all", which cannot tell a desk-mate from a packed room; the crowd cue needs the
#: count. Buckets rather than an integer because a 288x512 frame cannot be counted
#: exactly past a handful and a closed list keeps the answer to one short token.
PEOPLE_COUNT = ["0", "1-2", "3-5", "6+", "unknown"]

BOOL_FIELDS = [
    "food_present",
    "caffeine_visible",
    "alcohol_visible",
    "screen_present",
    "vegetation_visible",
    # `people_present` is "anyone in frame at all"; `people_interacting` is the
    # social-connection signal. A crowded bus sets the first and not the second.
    "people_present",
    "people_interacting",
    # Light exposure. `outdoor_visible` is deliberately *not* the same question as
    # `scene in OUTDOOR_SCENES`: it is true through a window too, which is how a
    # desk by daylight is distinguished from a windowless room (SPEC §7 circadian).
    "direct_sunlight_visible",
    "outdoor_visible",
    # Substances. Nicotine outweighs both caffeine and alcohol in the longevity
    # literature and had no field at all until now.
    "smoking_or_vaping_visible",
    "medication_visible",
]

#: Booleans where "not reported" must stay distinguishable from "no". Unlike
#: BOOL_FIELDS (which default to false, §9), these coerce to None when the model
#: gives null: "hands out of frame" is not evidence that no phone is held.
TRISTATE_BOOL_FIELDS = ["phone_in_hand"]

# --- Watcher prompt bank ------------------------------------------------------
# The watcher (docs/PERCEPTION.md, "Watcher") scores every frame with a zero-shot
# image-text model against these prompts: per concept, the max over its prompts,
# softmaxed against the null group. One group per boolean above, so adding a field
# to BOOL_FIELDS or TRISTATE_BOOL_FIELDS means adding its prompts here (a test fails
# otherwise). Prompts are concrete things a first-person glasses camera sees: short
# noun phrases, no abstractions, no negations -- CLIP does not understand "not".

WATCH_PROMPTS: dict[str, tuple[str, ...]] = {
    "food_present": (
        "a plate of food on a table",
        "a sandwich held in a hand",
        "a bowl of food with a fork",
        "a snack bar in a wrapper",
        "a slice of pizza held in a hand",
    ),
    "caffeine_visible": (
        "a cup of coffee on a desk",
        "an espresso cup held in a hand",
        "a paper coffee cup with a lid",
        "a can of energy drink",
        "a mug of hot tea",
    ),
    "alcohol_visible": (
        "a bottle of beer in a hand",
        "a can of beer on a table",
        "a glass of red wine",
        "a cocktail glass on a bar",
        "a shot glass of liquor",
    ),
    "screen_present": (
        "a laptop screen on a desk",
        "a computer monitor showing text",
        "a television screen in a room",
        "a tablet screen held in hands",
    ),
    "vegetation_visible": (
        "green trees along a path",
        "a grassy lawn in a park",
        "hedges and planted flower beds",
        "a forest trail with leafy trees",
    ),
    "people_present": (
        "a person standing in a room",
        "people walking on a sidewalk",
        "a group of people in a hallway",
        "a crowd of people",
    ),
    "people_interacting": (
        "a person facing the camera and talking",
        "a friend smiling across a table",
        "a face turned toward the camera",
        "two people in conversation",
    ),
    "direct_sunlight_visible": (
        "bright sunlight with hard shadows",
        "the sun shining in a blue sky",
        "sunlight streaming onto the ground",
        "sharp shadows on a sunny sidewalk",
    ),
    "outdoor_visible": (
        "a view of the street through a window",
        "a window showing trees and sky",
        "an outdoor street with buildings",
        "an open sky above a parking lot",
        "a campus walkway outdoors",
    ),
    "smoking_or_vaping_visible": (
        "a lit cigarette held in fingers",
        "a vape pen held in a hand",
        "a person exhaling a cloud of smoke",
        "a cigar resting in an ashtray",
        "a hookah pipe on a table",
    ),
    "medication_visible": (
        "an orange prescription pill bottle",
        "pills in the palm of a hand",
        "a blister pack of tablets",
        "an inhaler held in a hand",
        "a syringe or insulin pen injector",
    ),
    "phone_in_hand": (
        "a smartphone held in a hand",
        "a phone screen in the palm of a hand",
        "thumbs typing on a phone",
    ),
}

#: The null group every concept is softmaxed against: frames with nothing in them.
WATCH_NULL_PROMPTS: tuple[str, ...] = (
    "an empty desk",
    "a blank wall",
    "a blurry frame",
    "a dark room",
    "a carpeted floor",
    "a white ceiling",
)


def watch_prompt_bank() -> list[tuple[str, str]]:
    """(concept, prompt) pairs: BOOL_FIELDS, then TRISTATE_BOOL_FIELDS, then "_null"."""
    bank = [(c, p) for c in BOOL_FIELDS + TRISTATE_BOOL_FIELDS for p in WATCH_PROMPTS[c]]
    return bank + [("_null", p) for p in WATCH_NULL_PROMPTS]


ENUM_FIELDS = {
    "scene": SCENE,
    "activity": ACTIVITY,
    "food_type": FOOD_TYPE,
    "drink": DRINK,
    "people_count": PEOPLE_COUNT,
}

# Field order is also the order Gemini *generates* in (propertyOrdering), and the
# model conditions each value on what it has already written. The held item goes
# first so the caption and food/drink tags that follow are about the thing in the
# wearer's hand rather than the room -- the persona cues all live in the hand.
# The rest keeps the §12 example order.
FIELD_ORDER = [
    "in_hand", "phone_in_hand",
    "scene", "activity", "food_present", "food_type", "caffeine_visible",
    "alcohol_visible", "screen_present", "vegetation_visible", "people_present",
    "people_interacting", "people_count",
    "direct_sunlight_visible", "outdoor_visible", "smoking_or_vaping_visible",
    "medication_visible",
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
    "in_hand": None,
    "people_count": "unknown",
    **{f: False for f in BOOL_FIELDS},
    **{f: None for f in TRISTATE_BOOL_FIELDS},
}

# Answers that mean "nothing in hand". The schema allows null, but a model asked for
# a noun phrase sometimes spells the null out; every spelling reads as empty hands.
_EMPTY_HAND = frozenset({"", "none", "null", "nothing", "n/a", "na", "empty", "no"})

# --- Prompt -------------------------------------------------------------------

PROMPT = (
    "You are tagging a single first-person photo from smart glasses for a health "
    "tracker. Look at the wearer's hands first: what they are holding matters more "
    "than the room. Report only what is visible in this frame. Do not infer, "
    "remember, or guess from context, and when a field is truly unclear use its "
    "unknown / false / none / null value. But an item that is held, wrapped, "
    "packaged or partly covered by fingers IS visible: name it.\n\n"
    "in_hand names what is in the wearer's hand as a short noun phrase, with the "
    "brand or product if the label is legible (e.g. rice krispies treat, cucumber, "
    "coffee milkshake, iphone); null when the hands are empty or out of view.\n"
    "phone_in_hand is true when the wearer holds a phone, false when their hands "
    "are visible without one, null when the hands are not visible.\n"
    "people_count buckets every visible person: 0, 1-2, 3-5 or 6+.\n"
    "conf is your overall confidence in this whole tagging, from 0.0 to 1.0.\n"
    "caption is one short phrase of at most 12 words describing what is plainly "
    "visible, starting with the held item if there is one.\n"
    "objects lists up to 5 lowercase nouns that are plainly visible, nearest first "
    "with the held item first; skip furniture, walls and people.\n"
    "scene is the most specific matching location from this list.\n"
    "activity is the most specific matching thing the wearer is doing from this "
    "list; a phone held in the hand is phone_use even with a laptop in view.\n"
    "food_type is the most specific matching food visible from this list, or none. "
    "Held packaged or wrapped food counts, and sets food_present true.\n"
    "drink identifies the plainly visible drink, or none when no drink is visible; "
    "a coffee-flavoured drink such as a coffee milkshake is coffee.\n"
    "vegetation_visible is true only for outdoor greenery such as trees, grass, "
    "hedges or planted beds. A houseplant, a vase of cut flowers, or a vegetable "
    "on a plate is not vegetation.\n"
    "people_present is true if any person is visible at all, including strangers "
    "in the background. people_interacting is true only when the wearer is engaged "
    "with someone -- facing them in conversation or a shared activity -- and is "
    "false for passers-by, crowds and people merely sharing the space.\n"
    "direct_sunlight_visible is true only for unobstructed sun or the hard shadows "
    "it casts, not for a merely bright or overcast sky.\n"
    "outdoor_visible is true whenever the outdoors appears in the frame at all, "
    "including when seen through a window from inside.\n"
    "smoking_or_vaping_visible covers a lit cigarette, cigar, pipe, hookah or vape.\n"
    "medication_visible covers pills, blister packs, prescription bottles and inhalers."
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
        # Nullable rather than a sentinel string: "nothing in hand" and "hands not
        # in view" are both null, and null costs one token where "none" invites a
        # sentence.
        "in_hand": {"type": "STRING", "nullable": True},
        "people_count": {"type": "STRING", "enum": PEOPLE_COUNT},
    }
    for f in TRISTATE_BOOL_FIELDS:
        props.setdefault(f, {"type": "BOOLEAN", "nullable": True})
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

    for name in TRISTATE_BOOL_FIELDS:
        val = raw.get(name)
        out[name] = val if isinstance(val, bool) else None

    out["in_hand"] = _in_hand(raw.get("in_hand"))
    # A phone named in the hand but not flagged is still a phone in the hand; only
    # fill the gap, never overrule an explicit answer (same rule as drink -> flags).
    if out["phone_in_hand"] is None and out["in_hand"] and _names_phone(out["in_hand"]):
        out["phone_in_hand"] = True

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


def _in_hand(val: Any) -> str | None:
    """A held-item phrase, bounded and lowercased like `objects`, or None."""
    if not isinstance(val, str):
        return None
    text = " ".join(val.split()).lower()[:60]
    return None if text.strip(" .") in _EMPTY_HAND else text


def _names_phone(text: str) -> bool:
    words = set(text.replace("-", " ").split())
    return bool(words & {"phone", "smartphone", "iphone", "cellphone", "android"})
