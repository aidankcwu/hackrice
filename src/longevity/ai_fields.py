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

SCENE = [
    "home", "office", "restaurant", "gym", "sauna", "cold_plunge",
    "park", "trail", "vehicle", "street", "unknown",
]

ACTIVITY = ["seated", "standing", "walking", "exercising", "eating", "unknown"]

FOOD_TYPE = [
    "vegetables", "fruit", "grains", "fish", "poultry", "red_meat",
    "processed", "sweets", "mixed", "none",
]

BOOL_FIELDS = [
    "food_present",
    "caffeine_visible",
    "alcohol_visible",
    "screen_present",
    "vegetation_visible",
    "people_present",
]

ENUM_FIELDS = {"scene": SCENE, "activity": ACTIVITY, "food_type": FOOD_TYPE}

# Field order as it appears in the §12 example tick, for readable JSON output.
FIELD_ORDER = [
    "scene", "activity", "food_present", "food_type", "caffeine_visible",
    "alcohol_visible", "screen_present", "vegetation_visible", "people_present",
    "conf",
]

# `conf` appears in the §12 tick example but is absent from §9's field table. We ask
# the model for it, because §12 is the contract Person B consumes and §12.2 tells B to
# weigh confidence. If §9 is ever reconciled, this is the line to revisit.
DEFAULTS: dict[str, Any] = {
    "scene": "unknown",
    "activity": "unknown",
    "food_type": "none",
    "conf": 0.0,
    **{f: False for f in BOOL_FIELDS},
}

# --- Prompt -------------------------------------------------------------------

PROMPT = (
    "You are tagging a single first-person photo from smart glasses for a health "
    "tracker. Report only what is plainly visible in this frame. Do not infer, "
    "remember, or guess from context. If something is unclear, use the unknown / "
    "false / none value rather than a confident answer.\n\n"
    "conf is your overall confidence in this whole tagging, from 0.0 to 1.0."
)

# --- Structured-output schema -------------------------------------------------


def response_schema() -> dict[str, Any]:
    """OpenAPI-subset schema for Gemini structured output (`response_schema`)."""
    props: dict[str, Any] = {
        "scene": {"type": "STRING", "enum": SCENE},
        "activity": {"type": "STRING", "enum": ACTIVITY},
        "food_present": {"type": "BOOLEAN"},
        "food_type": {"type": "STRING", "enum": FOOD_TYPE},
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

    try:
        out["conf"] = max(0.0, min(1.0, float(raw.get("conf", 0.0))))
    except (TypeError, ValueError):
        out["conf"] = 0.0

    return {k: out[k] for k in FIELD_ORDER}
