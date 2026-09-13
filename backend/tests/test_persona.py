"""The persona that grows: `remember`, `profile_lines`, and episode labels.

Three contracts live here.

1. ``remember`` is a first-class action -- in the pydantic union *and* in the
   strict Responses schema, which are two hand-maintained lists that must not
   drift -- and :func:`normalize` is where a 400-character "fact" gets cut back
   to one line.
2. The persona handed to T1 is rebuilt on every wake-up: the operator's
   override wins over the one passed at construction, and everything
   ``remember`` has learned is read back into the system prompt.
3. An episode gets its name from the first ``annotate`` written about it, and
   an answer extends that name with what it settled.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterator

import httpx
import pytest

from pipeline.actions.speech import SpeechLimiter
from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.models import AiBlock, Episode, Escalation, SensorBlock, Tick
from pipeline.reasoner.prompts import (
    DEFAULT_PERSONA,
    LEARNED_HEADING,
    LEARNED_MAX,
    build_system_prompt,
)
from pipeline.reasoner.reasoner import Reasoner, settled_fact
from pipeline.reasoner.schema import (
    REMEMBER_MAX_CHARS,
    T1_JSON_SCHEMA,
    AnnotateAction,
    AnswerParse,
    RememberAction,
    T1Response,
    normalize,
)

T0 = 1_757_700_000.0


# -- fixtures -------------------------------------------------------------


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(demo_mode=True, openai_api_key=None)


def make_window(n: int = 8) -> list[Tick]:
    return [
        Tick(
            tick_id=f"t_{i:08d}",
            t=T0 + i,
            seq=i,
            sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.1,
                               phash="0" * 16 if i < n // 2 else "f" * 16),
            ai=AiBlock(as_of=T0 + i, age_ms=0, scene="office", activity="seated"),
            frame_ref=f"f_{i:08d}",
        )
        for i in range(n)
    ]


def make_escalation(trigger: str = "food_in_frame",
                    episode_id: str | None = None) -> Escalation:
    window = make_window()
    return Escalation(trigger=trigger, t=window[-1].t, tick=window[-1],
                      window=window, reason="synthetic", episode_id=episode_id)


@pytest.fixture
def frame_store() -> InMemoryFrameStore:
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(8):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    return store


class ScriptedClient:
    """Returns one fixed response and keeps the envelope it was handed."""

    model = "scripted"

    def __init__(self, response: T1Response) -> None:
        self.response = response
        self.last_envelope: list[dict[str, Any]] | None = None

    async def complete(self, input_messages: list[dict[str, Any]]):
        self.last_envelope = input_messages
        return self.response, {"model": self.model, "latency_ms": 3}


def build_reasoner(db, frame_store, settings, client, **kwargs) -> Reasoner:
    return Reasoner(
        db, frame_store, client,
        SpeechLimiter(settings.timings.speech_min_gap,
                      settings.timings.speech_max_per_hour),
        settings, **kwargs,
    )


async def drain(reasoner: Reasoner, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while reasoner.busy:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("reasoner never released the T1 slot")
        await asyncio.sleep(0.005)
    await asyncio.sleep(0)


def system_text(envelope: list[dict[str, Any]]) -> str:
    return next(
        item["text"]
        for message in envelope if message["role"] == "system"
        for item in message["content"]
    )


# -- 1. the action --------------------------------------------------------


def _walk_objects(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


def _variant(name: str) -> dict[str, Any]:
    return next(obj for obj in _walk_objects(T1_JSON_SCHEMA)
                if obj["properties"].get("type", {}).get("enum") == [name])


def test_remember_is_in_the_strict_schema() -> None:
    """The pydantic union and the JSON schema are two lists; they must agree."""

    variant = _variant("remember")
    assert set(variant["properties"]) == {"type", "line"}
    # Strict mode: closed object, everything required.
    assert variant["additionalProperties"] is False
    assert set(variant["required"]) == {"type", "line"}
    assert variant["properties"]["line"]["description"]


def test_remember_round_trips_through_the_response_model() -> None:
    raw = {
        "interpretation": "cold brew at the desk, second today",
        "confidence": 0.7,
        "actions": [
            {"type": "annotate", "line": "14:20 cold brew, desk"},
            {"type": "remember", "line": "Drinks his coffee black."},
        ],
    }
    resp = normalize(T1Response.model_validate(raw), t=T0)
    remembered = resp.of_type("remember")
    assert len(remembered) == 1
    assert isinstance(remembered[0], RememberAction)
    assert remembered[0].line == "Drinks his coffee black."
    # `remember` is not a substitute for the memory line.
    assert len(resp.of_type("annotate")) == 1


def test_normalize_caps_and_drops_remember_lines() -> None:
    resp = normalize(
        T1Response(
            interpretation="x",
            actions=[
                RememberAction(line="  " + "a" * 400 + "  "),
                RememberAction(line="   "),
            ],
        ),
        t=T0,
    )
    lines = [a.line for a in resp.of_type("remember")]
    assert len(lines) == 1, "the empty line should be gone, not stored blank"
    assert len(lines[0]) == REMEMBER_MAX_CHARS


# -- 2a. the store --------------------------------------------------------


def test_profile_lines_dedupe_on_casefolded_text(db: Database) -> None:
    first = db.add_profile_line("Drinks his coffee black.", T0, "d_0001")
    assert first is not None
    # The model re-derives the same fact an hour later, in a different case.
    assert db.add_profile_line("drinks HIS coffee black.", T0 + 3600, "d_0009") is None
    assert db.add_profile_line("   ", T0, "d_0010") is None
    assert [row["line"] for row in db.profile_lines()] == ["Drinks his coffee black."]

    second = db.add_profile_line("Runs on the bayou trail.", T0 + 60, "d_0002")
    assert second != first
    rows = db.profile_lines()
    assert [row["line"] for row in rows] == [
        "Drinks his coffee black.", "Runs on the bayou trail.",
    ], "oldest first -- that is the order they go into the prompt"
    assert rows[0]["source_decision_id"] == "d_0001"


def test_deactivating_a_line_hides_it_and_frees_the_text(db: Database) -> None:
    line_id = db.add_profile_line("Roommate is called Dev.", T0, "d_0001")
    assert line_id is not None
    assert db.deactivate_profile_line(line_id) is True
    assert db.deactivate_profile_line(line_id) is False, "not active any more"
    assert db.profile_lines() == []
    # The row stays for history, so the dedupe must not see it -- the model
    # re-deriving a struck-out fact should be able to write it again.
    assert db.add_profile_line("Roommate is called Dev.", T0 + 1, "d_0002") is not None


def test_profile_lines_limit_keeps_the_newest(db: Database) -> None:
    for i in range(10):
        db.add_profile_line(f"fact {i}", T0 + i, f"d_{i:04d}")
    rows = db.profile_lines(limit=3)
    assert [row["line"] for row in rows] == ["fact 7", "fact 8", "fact 9"]


def test_persona_override_round_trips_and_clears(db: Database) -> None:
    assert db.get_persona() is None
    db.set_persona("  The wearer is a night-shift nurse.  ", T0)
    assert db.get_persona() == "The wearer is a night-shift nurse."
    db.set_persona("", T0 + 1)
    assert db.get_persona() is None, "clearing falls back to the built-in persona"
    db.set_persona("again", T0 + 2)
    db.set_persona("   ", T0 + 3)
    assert db.get_persona() is None, "whitespace is empty, not a persona"


# -- 2b. the prompt -------------------------------------------------------


def test_system_prompt_carries_the_learned_lines() -> None:
    prompt = build_system_prompt(
        "persona text", "seven day text",
        ["Drinks his coffee black.", "Runs on the bayou trail."],
    )
    assert LEARNED_HEADING in prompt
    assert "- Drinks his coffee black." in prompt
    assert "- Runs on the bayou trail." in prompt
    # Order: persona, learned, 7-day, objective.
    assert (prompt.index("persona text") < prompt.index(LEARNED_HEADING)
            < prompt.index("seven day text") < prompt.index("You are T1"))


def test_system_prompt_omits_the_section_when_nothing_is_learned() -> None:
    for learned in (None, [], ["", "   "]):
        prompt = build_system_prompt("persona text", "seven day text", learned)
        assert LEARNED_HEADING not in prompt


def test_system_prompt_caps_the_learned_lines() -> None:
    prompt = build_system_prompt(
        "p", "s", [f"fact {i}" for i in range(LEARNED_MAX + 5)]
    )
    assert "- fact 0" not in prompt, "the oldest lines are the ones to drop"
    assert f"- fact {LEARNED_MAX + 4}" in prompt
    assert prompt.count("\n- ") == LEARNED_MAX


def test_the_objective_states_the_ask_and_remember_rules() -> None:
    """The live finding this work exists for: GPT never asked, in 38 decisions.

    The rule used to call a question "spent budget"; if that framing comes
    back, the model goes quiet again.
    """

    from pipeline.reasoner.prompts import OBJECTIVE

    assert "ASK WHEN THE MOMENT IS NEW" in OBJECTIVE
    assert "REMEMBER WHAT LASTS" in OBJECTIVE
    assert "a question you have spent" not in OBJECTIVE
    assert '"change"' in OBJECTIVE, "the transition trigger is a reason to ask"
    for kept in ("ONE question per episode", 'trigger\n  starts with "answer:"'):
        assert kept in OBJECTIVE


# -- 2c. the reasoner rebuilds the prompt every wake-up -------------------


async def test_envelope_uses_the_db_persona_and_learned_lines(
    db, frame_store, settings
) -> None:
    client = ScriptedClient(T1Response(interpretation="ok", confidence=0.6,
                                       actions=[AnnotateAction(line="ok")]))
    reasoner = build_reasoner(db, frame_store, settings, client,
                              persona="passed at init")

    assert reasoner.current_persona() == "passed at init"
    assert reasoner.try_escalate(make_escalation())
    await drain(reasoner)
    assert "passed at init" in system_text(client.last_envelope)

    # Both halves change while the process runs.
    db.set_persona("the operator's persona", T0)
    db.add_profile_line("Drinks his coffee black.", T0, "d_0001")
    assert reasoner.current_persona() == "the operator's persona"

    assert reasoner.try_escalate(make_escalation())
    await drain(reasoner)
    prompt = system_text(client.last_envelope)
    assert "the operator's persona" in prompt
    assert "passed at init" not in prompt
    assert "- Drinks his coffee black." in prompt


async def test_a_remember_action_is_stored_against_its_decision(
    db, frame_store, settings
) -> None:
    client = ScriptedClient(T1Response(
        interpretation="second cold brew today",
        confidence=0.8,
        actions=[AnnotateAction(line="14:20 cold brew, desk"),
                 RememberAction(line="Drinks his coffee black.")],
    ))
    reasoner = build_reasoner(db, frame_store, settings, client)

    assert reasoner.try_escalate(make_escalation())
    await drain(reasoner)

    rows = db.profile_lines()
    assert [row["line"] for row in rows] == ["Drinks his coffee black."]
    assert rows[0]["source_decision_id"] == db.list_decisions(1)[0].id
    assert reasoner.stats()["remembered"] == 1

    # The same fact on the next wake-up is dropped, not duplicated.
    assert reasoner.try_escalate(make_escalation())
    await drain(reasoner)
    assert len(db.profile_lines()) == 1
    assert reasoner.stats()["remembered"] == 1


# -- 3. episode labels ----------------------------------------------------


def _episode(db: Database, episode_id: str = "e_0001") -> Episode:
    episode = Episode(id=episode_id, kind="food_sighting", start_t=T0,
                      dominant={"scene": "office"}, tick_count=1)
    db.upsert_episode(episode)
    return episode


def test_a_label_survives_the_builders_next_upsert(db: Database) -> None:
    """The builder re-upserts an open episode every tick (SPEC §10)."""

    episode = _episode(db)
    assert db.set_episode_label(episode.id, "14:20 cold brew, desk") is True
    episode.tick_count = 2
    db.upsert_episode(episode)
    assert db.episode_label(episode.id) == "14:20 cold brew, desk"
    assert db.episode_labels() == {episode.id: "14:20 cold brew, desk"}
    assert db.set_episode_label("e_nope", "x") is False
    assert db.set_episode_label(episode.id, "   ") is False


async def test_the_first_annotate_names_the_episode(
    db, frame_store, settings
) -> None:
    _episode(db)
    client = ScriptedClient(T1Response(
        interpretation="cold brew at the desk", confidence=0.8,
        actions=[AnnotateAction(line="14:20 cold brew, desk")],
    ))
    reasoner = build_reasoner(db, frame_store, settings, client)
    assert reasoner.try_escalate(make_escalation(episode_id="e_0001"))
    await drain(reasoner)
    assert db.episode_label("e_0001") == "14:20 cold brew, desk"

    # A later wake-up in the same episode must not rename it: the last line
    # ("still at the desk") is a worse name than the first.
    client.response = T1Response(interpretation="still there", confidence=0.5,
                                 actions=[AnnotateAction(line="14:31 still at the desk")])
    assert reasoner.try_escalate(make_escalation(episode_id="e_0001"))
    await drain(reasoner)
    assert db.episode_label("e_0001") == "14:20 cold brew, desk"


def test_settled_fact_reads_the_parse_not_the_transcript() -> None:
    assert settled_fact(AnswerParse(understood=True, confirmed=True, count=2)) \
        == "confirmed, 2"
    assert settled_fact(AnswerParse(understood=True, confirmed=True, count=1.5)) \
        == "confirmed, 1.5"
    assert settled_fact(AnswerParse(understood=True, confirmed=False)) == "not theirs"
    assert settled_fact(AnswerParse(understood=True, note="it was cold brew")) \
        == "it was cold brew"
    # An answer nobody could read settles nothing, so it names nothing.
    assert settled_fact(AnswerParse(understood=False, note="…")) == ""


def test_an_answer_extends_the_label(db, frame_store, settings) -> None:
    _episode(db)
    db.set_episode_label("e_0001", "14:20 cold brew, desk")
    reasoner = build_reasoner(db, frame_store, settings,
                              ScriptedClient(T1Response()))
    parse = AnswerParse(understood=True, confirmed=True, count=2)

    reasoner._extend_episode_label("e_0001", parse)
    assert db.episode_label("e_0001") == "14:20 cold brew, desk · confirmed, 2"
    # A re-delivered answer must not stutter the label.
    reasoner._extend_episode_label("e_0001", parse)
    assert db.episode_label("e_0001") == "14:20 cold brew, desk · confirmed, 2"
    # No episode, or nothing settled: a no-op, never a crash.
    reasoner._extend_episode_label(None, parse)
    reasoner._extend_episode_label("e_0001", AnswerParse(understood=False))
    assert db.episode_label("e_0001") == "14:20 cold brew, desk · confirmed, 2"


# -- 4. the routes --------------------------------------------------------


@pytest.fixture
async def client(tmp_path):
    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "persona.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    app = create_app(pipeline)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as http:
        yield http, pipeline
    pipeline.db.close()


async def test_persona_routes(client) -> None:
    http, pipeline = client

    body = (await http.get("/api/persona")).json()
    assert body["source"] == "default"
    assert body["text"] == DEFAULT_PERSONA

    put = await http.put("/api/persona", json={"text": "  A night-shift nurse.  "})
    assert put.status_code == 200
    assert put.json() == {"text": "A night-shift nurse.", "source": "custom"}
    assert (await http.get("/api/persona")).json()["source"] == "custom"
    assert pipeline.reasoner.current_persona() == "A night-shift nurse."

    cleared = (await http.put("/api/persona", json={"text": ""})).json()
    assert cleared["source"] == "default"
    assert cleared["text"] == DEFAULT_PERSONA

    assert (await http.put("/api/persona", json={"text": 7})).status_code == 400


async def test_profile_routes(client) -> None:
    http, pipeline = client
    first = pipeline.db.add_profile_line("Drinks his coffee black.", T0, "d_0001")
    pipeline.db.add_profile_line("Runs on the bayou trail.", T0 + 60, "d_0002")

    rows = (await http.get("/api/profile")).json()
    assert [row["line"] for row in rows] == [
        "Drinks his coffee black.", "Runs on the bayou trail.",
    ]
    assert set(rows[0]) == {"id", "t", "line", "source_decision_id"}
    assert rows[0]["source_decision_id"] == "d_0001"

    assert (await http.delete(f"/api/profile/{first}")).json() == {
        "id": first, "removed": True,
    }
    assert [row["line"] for row in (await http.get("/api/profile")).json()] == [
        "Runs on the bayou trail.",
    ]
    # Already gone, and never existed, are the same 404.
    assert (await http.delete(f"/api/profile/{first}")).status_code == 404
    assert (await http.delete("/api/profile/p_nope")).status_code == 404


async def test_episodes_carry_their_label(client) -> None:
    http, pipeline = client
    episode = Episode(id="e_0001", kind="food_sighting", start_t=T0,
                      dominant={}, tick_count=1)
    pipeline.db.upsert_episode(episode)
    from pipeline.db import day_key

    day = day_key(T0)
    rows = (await http.get(f"/api/episodes?day={day}")).json()
    assert rows and rows[0]["label"] is None

    pipeline.db.set_episode_label("e_0001", "14:20 cold brew, desk")
    rows = (await http.get(f"/api/episodes?day={day}")).json()
    assert rows[0]["label"] == "14:20 cold brew, desk"
    assert "reported" in rows[0]
