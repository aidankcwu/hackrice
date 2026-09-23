"""Answer parsing (docs/ASK_DESIGN.md §8.11): the fake, the real one, the fake T1's ask.

The fake parser is what a key-less demo actually runs, so "yeah two" has to
come out as ``confirmed=True, count=2`` from a regex, not from a model. The
OpenAI parser is exercised against a stub whose only contract is
``responses.create`` returning something with ``output_text`` -- including the
case where the model rejects ``reasoning`` and the call has to go again
without it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline.config import Settings
from pipeline.models import Episode, PendingQuestion
from pipeline.reasoner.client import (
    FAKE_FOLLOWUP,
    FakeAnswerParser,
    FakeReasonerClient,
    OpenAIAnswerParser,
    make_answer_parser,
)
from pipeline.reasoner.schema import AnswerParse, AskAction

T0 = 1_757_700_000.0


def question(answer_kind: str = "yes_no", fills: str = "confirmed") -> PendingQuestion:
    return PendingQuestion(
        id="q_00000001",
        created_t=T0,
        expires_t=T0 + 25.0,
        episode_id="ep_1",
        question="That yours?",
        answer_kind=answer_kind,
        fills=fills,
    )


# -- the fake parser ------------------------------------------------------


@pytest.mark.parametrize(
    "transcript, confirmed, count",
    [
        ("yeah two", True, 2.0),
        ("yeah, two", True, 2.0),
        ("yes", True, None),
        ("yep, it's mine", True, None),
        ("sure", True, None),
        ("nope", False, None),
        ("no", False, None),
        ("nah not tonight", False, None),
        ("someone else's", False, None),
        ("that's not mine", False, None),
        ("three", None, 3.0),
        ("I've had 4 today", None, 4.0),
        ("it's decaf", None, None),
    ],
)
async def test_fake_parser_reads_the_obvious_answers(
    transcript: str, confirmed: bool | None, count: float | None
) -> None:
    parsed = await FakeAnswerParser().parse(question(), transcript, None)
    assert parsed.understood is True
    assert parsed.confirmed is confirmed
    assert parsed.count == count
    assert parsed.note == transcript


async def test_fake_parser_hears_nothing_in_an_empty_transcript() -> None:
    """Silence is never a yes: no fields at all, and understood is false."""

    parsed = await FakeAnswerParser().parse(question(), "", None)
    assert parsed.understood is False
    assert parsed.confirmed is None
    assert parsed.count is None
    assert parsed.note == ""
    assert parsed.followup is None

    assert (await FakeAnswerParser().parse(question(), "   ", None)).understood is False


async def test_fake_parser_prefers_disowning_over_the_word_mine() -> None:
    """"It's not mine" contains "mine"; the denial has to be read first."""

    parsed = await FakeAnswerParser().parse(question(), "no, it's not mine", None)
    assert parsed.confirmed is False


@pytest.mark.parametrize(
    "transcript",
    [
        "no",
        "no it is not",
        "nope",
        "nah",
        "not mine",
        "that belongs to someone else",
        "mine but I am not drinking it",
        "I am not having it",
        "I didn\u2019t",
        "I didn't",
        "I don't",
        "it isn't",
        "it is not",
        "it's not",
    ],
)
async def test_fake_parser_reads_every_shape_of_no(transcript: str) -> None:
    """Negation is checked first and alone -- §8.8's "ownership is not enough".

    "mine but I am not drinking it" is the case that caught the old order:
    it owns the drink and declines it, and affirmative-first matching turned
    that into a yes plus a follow-up asking how many.
    """

    parsed = await FakeAnswerParser().parse(question(), transcript, None)
    assert parsed.understood is True
    assert parsed.confirmed is False
    assert parsed.followup is None


async def test_fake_parser_lets_a_no_outrank_a_yes_in_one_sentence() -> None:
    """"yeah but I'm not drinking it" is a no, however it starts."""

    parsed = await FakeAnswerParser().parse(
        question(), "yeah but I'm not drinking it", None
    )
    assert parsed.confirmed is False
    assert parsed.followup is None


@pytest.mark.parametrize(
    "transcript", ["yes", "yeah", "yep", "sure", "mine", "it is", "i am"]
)
async def test_fake_parser_reads_a_clean_yes(transcript: str) -> None:
    parsed = await FakeAnswerParser().parse(question(), transcript, None)
    assert parsed.confirmed is True


async def test_fake_parser_still_reads_a_yes_with_a_number() -> None:
    """The demo sentence: one pass, both fields, no follow-up needed."""

    parsed = await FakeAnswerParser().parse(question(), "yeah two", None)
    assert parsed.confirmed is True
    assert parsed.count == 2.0
    assert parsed.followup is None


async def test_fake_parser_takes_an_unplaceable_answer_as_a_note() -> None:
    parsed = await FakeAnswerParser().parse(question(), "it's decaf", None)
    assert parsed.understood is True
    assert parsed.confirmed is None
    assert parsed.count is None
    assert parsed.note == "it's decaf"


async def test_fake_parser_reads_an_empty_transcript_as_nothing_at_all() -> None:
    parsed = await FakeAnswerParser().parse(question(), "", None)
    assert parsed.understood is False
    assert (parsed.confirmed, parsed.count, parsed.followup) == (None, None, None)


@pytest.mark.parametrize(
    "transcript, count",
    [
        ("two yesterday", None),
        ("three last night", None),
        ("I had 2 last week", None),
        ("one tomorrow", None),
        ("two earlier this week", None),
        ("-3", None),
        ("three", 3.0),
        ("2", 2.0),
        ("2.5", 2.5),
        ("0", 0.0),
    ],
)
async def test_fake_parser_counts_only_what_is_about_today(
    transcript: str, count: float | None
) -> None:
    """A number the fake cannot place on today is no count at all (§8.8).

    ``-3`` is the other half: the digits are there, but the sign says they
    were not a serving count, and a bare ``\\b`` match would have read a 3.
    """

    parsed = await FakeAnswerParser().parse(question("count", "count"), transcript, None)
    assert parsed.count == count


async def test_fake_parser_names_a_menu_food_type() -> None:
    parsed = await FakeAnswerParser().parse(
        question("free", "food_type"), "it was a rice bowl", None
    )
    assert parsed.food_type == "rice_bowl"
    assert parsed.followup is None  # not a yes_no question


async def test_fake_parser_ignores_a_food_it_does_not_know() -> None:
    parsed = await FakeAnswerParser().parse(
        question("free", "food_type"), "a croissant", None
    )
    assert parsed.food_type is None
    assert parsed.note == "a croissant"


async def test_fake_parser_follows_up_on_a_bare_yes() -> None:
    """A yes with no number is exactly the case §8.5 allows one child for."""

    parsed = await FakeAnswerParser().parse(question(), "yeah", None)
    assert parsed.confirmed is True
    assert parsed.count is None
    assert parsed.followup == FAKE_FOLLOWUP


@pytest.mark.parametrize(
    "kind, transcript",
    [("yes_no", "yeah two"), ("yes_no", "nope"), ("count", "yes"), ("free", "yes")],
)
async def test_fake_parser_does_not_follow_up_otherwise(
    kind: str, transcript: str
) -> None:
    parsed = await FakeAnswerParser().parse(question(kind), transcript, None)
    assert parsed.followup is None


async def test_fake_parser_accepts_an_episode_and_ignores_it() -> None:
    episode = Episode(id="ep_1", kind="alcohol_sighting", start_t=T0, end_t=T0 + 5)
    parsed = await FakeAnswerParser().parse(question(), "yeah two", episode)
    assert parsed.count == 2.0


async def test_fake_parser_truncates_a_rambling_note() -> None:
    parsed = await FakeAnswerParser().parse(question(), "yes " + "x" * 200, None)
    assert len(parsed.note) == 80


# -- the OpenAI parser ----------------------------------------------------


class StubResponses:
    """Just enough of ``client.responses`` to answer one call."""

    def __init__(self, payload: str, reject_reasoning: bool = False) -> None:
        self.payload = payload
        self.reject_reasoning = reject_reasoning
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.reject_reasoning and "reasoning" in kwargs:
            raise RuntimeError(
                "Unsupported parameter: 'reasoning' is not supported with this model"
            )
        return SimpleNamespace(output_text=self.payload, model="stub-model")


class StubClient:
    def __init__(self, payload: str, reject_reasoning: bool = False) -> None:
        self.responses = StubResponses(payload, reject_reasoning)


PAYLOAD = json.dumps(
    {
        "understood": True,
        "confirmed": True,
        "count": 2,
        "food_type": None,
        "note": "yes, second one",
        "followup": None,
    }
)


async def test_openai_parser_returns_the_parsed_fields() -> None:
    stub = StubClient(PAYLOAD)
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=stub)

    parsed = await parser.parse(question(), "yeah, second one", None)

    assert parsed == AnswerParse(
        understood=True, confirmed=True, count=2.0, note="yes, second one"
    )
    (call,) = stub.responses.calls
    assert call["model"] == "gpt-5.4-mini"
    assert call["text"]["format"]["name"] == "answer_parse"
    assert call["reasoning"] == {"effort": "none"}


async def test_openai_parser_prompt_carries_question_episode_and_transcript() -> None:
    stub = StubClient(PAYLOAD)
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=stub)
    episode = Episode(
        id="ep_1",
        kind="alcohol_sighting",
        start_t=T0,
        dominant={"drink": "wine"},
    )

    await parser.parse(question(), "yeah, second one", episode)

    messages = stub.responses.calls[0]["input"]
    assert messages[0]["role"] == "system"
    assert "understood" in messages[0]["content"]
    user = messages[1]["content"]
    assert "That yours?" in user
    assert "alcohol_sighting" in user and "drink=wine" in user
    assert "yeah, second one" in user


async def test_openai_parser_says_so_when_nothing_was_heard() -> None:
    stub = StubClient(PAYLOAD)
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=stub)
    await parser.parse(question(), "   ", None)
    assert "(nothing heard)" in stub.responses.calls[0]["input"][1]["content"]


async def test_openai_parser_retries_without_reasoning_when_rejected() -> None:
    """Same fallback the T1 client has: the model, not the call site, decides."""

    stub = StubClient(PAYLOAD, reject_reasoning=True)
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=stub)

    parsed = await parser.parse(question(), "yeah, second one", None)

    assert parsed.understood is True
    assert len(stub.responses.calls) == 2
    assert "reasoning" in stub.responses.calls[0]
    assert "reasoning" not in stub.responses.calls[1]


async def test_openai_parser_propagates_an_unrelated_failure() -> None:
    class Boom:
        responses = SimpleNamespace()

    async def create(**kwargs: Any) -> Any:
        raise RuntimeError("rate limited")

    client = Boom()
    client.responses.create = create
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=client)

    with pytest.raises(RuntimeError, match="rate limited"):
        await parser.parse(question(), "yes", None)


async def test_openai_parser_strips_code_fences() -> None:
    stub = StubClient(f"```json\n{PAYLOAD}\n```")
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=stub)
    assert (await parser.parse(question(), "yes", None)).count == 2.0


async def test_openai_parser_clamps_what_the_model_returns() -> None:
    """The schema permits a number; §8.8 permits 0..20."""

    stub = StubClient(
        json.dumps(
            {
                "understood": True,
                "confirmed": True,
                "count": 400,
                "food_type": "latte",
                "note": "lots",
                "followup": "",
            }
        )
    )
    parser = OpenAIAnswerParser("sk-test", "gpt-5.4-mini", client=stub)
    parsed = await parser.parse(question(), "loads of them", None)
    assert parsed.count is None
    assert parsed.food_type is None
    assert parsed.followup is None


async def test_openai_parser_survives_an_absurd_count_literal() -> None:
    """A JSON integer no float can hold raises ``OverflowError``, not ``ValueError``.

    Structured output does not stop a model writing ``10**400`` worth of
    digits into ``count``; ``_clean_count`` has to drop it rather than let
    the exception escape and lose the whole answer.
    """

    raw = json.dumps(
        {
            "understood": True,
            "confirmed": True,
            "count": 10**400,
            "food_type": None,
            "note": "a great many",
            "followup": None,
        }
    )
    assert "e+" not in raw  # the literal really is written out in full

    parsed = OpenAIAnswerParser._parse(raw)
    assert parsed.count is None
    assert parsed.understood is True
    assert parsed.confirmed is True
    assert parsed.note == "a great many"

    assert AnswerParse.model_validate({"understood": True, "count": 10**400}).count is None


def test_openai_parser_requires_a_key() -> None:
    with pytest.raises(RuntimeError):
        OpenAIAnswerParser("", "gpt-5.4-mini", client=StubClient(PAYLOAD))


def test_openai_parser_defaults_to_a_ten_second_timeout() -> None:
    import inspect

    default = inspect.signature(OpenAIAnswerParser.__init__).parameters["timeout"]
    assert default.default == 10.0


# -- the factory ----------------------------------------------------------


def test_make_answer_parser_fake_needs_no_key() -> None:
    parser = make_answer_parser(Settings(_env_file=None, openai_api_key=None), "fake")
    assert isinstance(parser, FakeAnswerParser)


def test_make_answer_parser_refuses_openai_without_a_key() -> None:
    with pytest.raises(RuntimeError):
        make_answer_parser(Settings(_env_file=None, openai_api_key=None), "openai")


def test_make_answer_parser_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError):
        make_answer_parser(Settings(_env_file=None), "gemini")  # type: ignore[arg-type]


# -- the fake T1 now asks (§7's key-less demo path) ------------------------


def envelope(trigger: str, clock: str = "10:15:00") -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": f"Trigger: {trigger} at {clock} because"},
            ],
        },
    ]


async def test_fake_reasoner_asks_whose_drink_it_is() -> None:
    resp, _ = await FakeReasonerClient().complete(envelope("alcohol_seen"))
    (ask,) = [a for a in resp.actions if a.type == "ask"]
    assert isinstance(ask, AskAction)
    assert ask.answer_kind == "yes_no"
    assert ask.fills == "confirmed"
    assert ask.text == "That yours?"
    assert [a.type for a in resp.actions][0] == "annotate"  # annotate comes first


async def test_fake_reasoner_asks_about_coffee_before_the_cutoff() -> None:
    resp, _ = await FakeReasonerClient().complete(envelope("caffeine_seen", "09:00:00"))
    (ask,) = [a for a in resp.actions if a.type == "ask"]
    assert ask.text == "Is that coffee yours?"
    assert not [a for a in resp.actions if a.type == "speak"]


async def test_fake_reasoner_speaks_rather_than_asks_after_the_cutoff() -> None:
    """One mouth: §8.6 would drop the speak, so the fake does not propose both."""

    resp, _ = await FakeReasonerClient().complete(envelope("caffeine_seen", "16:30:00"))
    assert not [a for a in resp.actions if a.type == "ask"]
    assert [a for a in resp.actions if a.type == "speak"]


async def test_fake_reasoner_does_not_ask_again_on_an_answer_wake_up() -> None:
    """A wake-up that is itself the reply must not start another question."""

    resp, _ = await FakeReasonerClient().complete(envelope("answer:q_00000001"))
    assert [a.type for a in resp.actions] == ["annotate"]
