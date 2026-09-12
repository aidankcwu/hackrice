"""The ``ask`` action and the answer parse (docs/ASK_DESIGN.md §5, §8.6, §8.8).

Two contracts are under test here. First, ``ask`` is a first-class member of
the strict Responses schema, so the model can emit one and it survives the
round trip. Second, ``AnswerParse`` is where a language model's idea of a
number stops being authoritative: an out-of-range count or an invented food is
dropped to ``None`` rather than stored as something the wearer said.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterator

import pytest

from pipeline.models import FOOD_TYPES
from pipeline.reasoner.schema import (
    ANSWER_JSON_SCHEMA,
    ANSWER_TEXT_FORMAT,
    COUNT_MAX,
    FOLLOWUP_MAX_CHARS,
    NOTE_MAX_CHARS,
    T1_JSON_SCHEMA,
    AnnotateAction,
    AnswerParse,
    AskAction,
    NothingAction,
    SpeakAction,
    T1Response,
    normalize,
)

T0 = 1_757_700_000.0


def _walk_objects(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


def _variant(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        obj
        for obj in _walk_objects(schema)
        if obj["properties"].get("type", {}).get("enum") == [name]
    )


# -- the action ----------------------------------------------------------


def test_ask_round_trips_through_the_response_model() -> None:
    resp = T1Response.model_validate(
        {
            "interpretation": "a glass of wine on the table",
            "confidence": 0.7,
            "actions": [
                {"type": "annotate", "line": "alcohol in frame"},
                {
                    "type": "ask",
                    "text": "That yours?",
                    "answer_kind": "yes_no",
                    "fills": "confirmed",
                    "reason": "the frames cannot say whose drink it is",
                },
            ],
        }
    )
    ask = resp.of_type("ask")[0]
    assert isinstance(ask, AskAction)
    assert ask.text == "That yours?"
    assert ask.answer_kind == "yes_no"
    assert ask.fills == "confirmed"


def test_ask_defaults_to_a_yes_no_confirmation() -> None:
    """The common case -- "is that yours" -- needs only the text."""

    ask = T1Response.model_validate(
        {"actions": [{"type": "ask", "text": "Is that coffee yours?"}]}
    ).actions[0]
    assert (ask.answer_kind, ask.fills, ask.reason) == ("yes_no", "confirmed", "")


@pytest.mark.parametrize(
    "field, value",
    [("answer_kind", "essay"), ("fills", "mood")],
)
def test_ask_rejects_a_value_outside_the_menu(field: str, value: str) -> None:
    with pytest.raises(Exception):
        AskAction(text="How many?", **{field: value})  # type: ignore[arg-type]


# -- the JSON schemas ----------------------------------------------------


def test_ask_is_in_the_strict_t1_schema() -> None:
    ask = _variant(T1_JSON_SCHEMA, "ask")
    assert ask["additionalProperties"] is False
    assert set(ask["required"]) == set(ask["properties"])
    assert ask["properties"]["answer_kind"]["enum"] == ["yes_no", "count", "free"]
    assert ask["properties"]["fills"]["enum"] == [
        "confirmed",
        "count",
        "food_type",
        "note",
    ]


def test_the_answer_schema_is_strict_and_nullable_by_union() -> None:
    objects = list(_walk_objects(ANSWER_JSON_SCHEMA))
    assert objects, "the answer schema has at least a root object"
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])

    props = ANSWER_JSON_SCHEMA["properties"]
    assert props["understood"]["type"] == "boolean"
    assert props["note"]["type"] == "string"
    for optional in ("confirmed", "count", "food_type", "followup"):
        assert props[optional]["type"][-1] == "null", optional


def test_both_schemas_are_json_serialisable() -> None:
    json.dumps(T1_JSON_SCHEMA)
    json.dumps(ANSWER_TEXT_FORMAT)


def test_answer_text_format_is_a_strict_responses_format() -> None:
    fmt = ANSWER_TEXT_FORMAT["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["strict"] is True
    assert fmt["schema"] is ANSWER_JSON_SCHEMA


# -- normalisation (§8.6) ------------------------------------------------


def test_normalize_drops_the_speak_when_the_response_also_asks() -> None:
    """Nothing may talk over the answer window -- the question wins."""

    resp = T1Response(
        interpretation="wine on the table",
        confidence=0.7,
        actions=[
            SpeakAction(text="Second one tonight."),
            AskAction(text="That yours?"),
            AnnotateAction(line="alcohol in frame"),
        ],
    )
    kinds = [a.type for a in normalize(resp, t=T0).actions]
    assert "speak" not in kinds
    assert kinds.count("ask") == 1
    assert "annotate" in kinds


def test_normalize_keeps_the_speak_when_nothing_asks() -> None:
    resp = T1Response(
        interpretation="wine on the table",
        actions=[SpeakAction(text="Second one tonight."), AnnotateAction(line="x")],
    )
    assert "speak" in [a.type for a in normalize(resp, t=T0).actions]


@pytest.mark.parametrize("text", ["", " ", "nothing", '{"type":"ask"}', "[1]"])
def test_normalize_drops_an_unspeakable_ask_like_a_bad_speak(text: str) -> None:
    """An ask is read aloud by the same mouth, so it is held to the same bar."""

    resp = T1Response(actions=[AskAction(text=text), SpeakAction(text="Hello there.")])
    kinds = [a.type for a in normalize(resp, t=T0).actions]
    assert "ask" not in kinds
    # ... and a dropped ask must not take the legitimate speak with it.
    assert "speak" in kinds


def test_normalize_still_annotates_a_response_that_only_asks() -> None:
    resp = T1Response(interpretation="wine on the table", actions=[AskAction(text="That yours?")])
    kinds = [a.type for a in normalize(resp, t=T0).actions]
    assert kinds.count("ask") == 1
    assert "annotate" in kinds


def test_normalize_drops_nothing_alongside_an_ask() -> None:
    resp = T1Response(actions=[AskAction(text="That yours?"), NothingAction()])
    assert "nothing" not in [a.type for a in normalize(resp, t=T0).actions]


# -- AnswerParse validation (§8.8) ---------------------------------------


def test_answer_parse_defaults_say_nothing() -> None:
    parse = AnswerParse()
    assert parse.understood is False
    assert (parse.confirmed, parse.count, parse.food_type, parse.followup) == (
        None,
        None,
        None,
        None,
    )
    assert parse.note == ""


@pytest.mark.parametrize("value", [0, 1, 2.5, COUNT_MAX])
def test_answer_parse_keeps_a_plausible_count(value: float) -> None:
    assert AnswerParse(understood=True, count=value).count == float(value)


@pytest.mark.parametrize(
    "value",
    [-1, -0.5, COUNT_MAX + 0.5, 1000, math.inf, -math.inf, math.nan, "lots", None],
)
def test_answer_parse_nulls_a_count_it_cannot_stand_behind(value: Any) -> None:
    """Out of range is not a fact about the day; it is a number nobody said."""

    assert AnswerParse(understood=True, count=value).count is None


@pytest.mark.parametrize(
    "given, expected",
    [
        ("rice_bowl", "rice_bowl"),
        ("Rice Bowl", "rice_bowl"),
        (" SALAD ", "salad"),
        ("rice-bowl", "rice_bowl"),
        ("latte", None),
        ("none", None),
        ("", None),
        (None, None),
        (7, None),
    ],
)
def test_answer_parse_only_accepts_menu_food_types(given: Any, expected: Any) -> None:
    parsed = AnswerParse(understood=True, food_type=given)
    assert parsed.food_type == expected
    if parsed.food_type is not None:
        assert parsed.food_type in FOOD_TYPES


def test_answer_parse_truncates_the_note() -> None:
    parse = AnswerParse(understood=True, note="x" * 200)
    assert len(parse.note) == NOTE_MAX_CHARS
    assert AnswerParse(understood=True, note=None).note == ""
    assert AnswerParse(understood=True, note="  trimmed  ").note == "trimmed"


def test_answer_parse_truncates_the_followup_and_nulls_a_blank_one() -> None:
    long = AnswerParse(understood=True, followup="y" * 300)
    assert long.followup is not None and len(long.followup) == FOLLOWUP_MAX_CHARS
    assert AnswerParse(understood=True, followup="   ").followup is None
    assert AnswerParse(understood=True, followup="How many today?").followup == (
        "How many today?"
    )


def test_answer_parse_not_understood_keeps_only_the_note() -> None:
    """§8.8: ``understood=false`` -> note only, no semantic fields.

    The parser is allowed to admit it could not read the sentence. What it is
    not allowed to do is admit that *and* still hand back a ``confirmed`` --
    the field would be a guess dressed as an answer.
    """

    parse = AnswerParse(
        understood=False,
        confirmed=True,
        count=2,
        food_type="rice_bowl",
        note="mumbling",
        followup="How many today?",
    )
    assert parse.understood is False
    assert parse.confirmed is None
    assert parse.count is None
    assert parse.food_type is None
    assert parse.followup is None
    assert parse.note == "mumbling"


def test_answer_parse_not_understood_strips_fields_from_a_model_payload() -> None:
    """The same rule on the path that actually matters: model JSON in."""

    parse = AnswerParse.model_validate(
        {
            "understood": False,
            "confirmed": False,
            "count": 3,
            "food_type": "salad",
            "note": "could not tell",
            "followup": "Say again?",
        }
    )
    assert (parse.confirmed, parse.count, parse.food_type, parse.followup) == (
        None,
        None,
        None,
        None,
    )
    assert parse.note == "could not tell"


def test_answer_parse_understood_keeps_its_fields() -> None:
    """The stripping is conditional, not a blanket clear."""

    parse = AnswerParse(
        understood=True,
        confirmed=True,
        count=2,
        food_type="salad",
        note="yeah two",
        followup="Anything else?",
    )
    assert (parse.confirmed, parse.count, parse.food_type) == (True, 2.0, "salad")
    assert parse.followup == "Anything else?"


def test_answer_parse_not_understood_round_trips_empty() -> None:
    """A stripped parse dumps and reloads as itself -- ``parsed`` stores it."""

    parse = AnswerParse(understood=False, confirmed=True, note="mumbling")
    again = AnswerParse.model_validate(json.loads(json.dumps(parse.model_dump())))
    assert again == parse
    assert again.confirmed is None


def test_answer_parse_survives_a_count_too_big_for_a_float() -> None:
    """``float(10**400)`` raises ``OverflowError``, not ``ValueError``.

    A model that writes an absurd integer literal into its JSON must not take
    the answer path down with it -- the count is simply not a fact.
    """

    parse = AnswerParse.model_validate({"understood": True, "count": 10**400})
    assert parse.count is None
    assert AnswerParse(understood=True, count=-(10**400)).count is None


def test_answer_parse_round_trips_through_its_own_dump() -> None:
    """``PendingQuestion.parsed`` stores exactly this dict."""

    parse = AnswerParse(understood=True, confirmed=True, count=2, note="yeah two")
    assert AnswerParse.model_validate(json.loads(json.dumps(parse.model_dump()))) == parse
