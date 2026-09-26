"""The session thread (pipeline.reasoner.thread): one continuing conversation.

What the thread promises: the model reads its own earlier turns; images ride
only on the newest turns; the oldest turn folds into the running picture as
the window slides, and nothing leaves the context before it does; a new
session starts a new conversation; a restart rebuilds from the decisions
table; the voice agent reads the picture.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from pipeline.actions.speech import SpeechLimiter
from pipeline.config import Settings
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.models import Decision, Session
from pipeline.reasoner.client import FakeReasonerClient
from pipeline.reasoner.prompts import THREAD_OBJECTIVE, build_system_prompt
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.schema import (
    SUMMARY_MAX_CHARS, THINKING_MAX_CHARS, T1_JSON_SCHEMA, AnnotateAction,
    SpeakAction, T1Response, normalize,
)
from pipeline.reasoner.thread import (
    PICTURE_EMPTY, PICTURE_HEADING, SessionThread, Turn, render_reply, residue_line,
)
from pipeline.conversation.prompts import PICTURE_HEADING as VOICE_PICTURE, build_voice_system_prompt

from test_reasoner import T0, build_reasoner, drain, make_escalation  # noqa: E402


def esc_at(trigger: str, t: float):
    """A synthetic escalation stamped at ``t`` (the window's ticks stay at T0..)."""

    return make_escalation(trigger=trigger).model_copy(update={"t": t})


def _turn(i: int, with_image: bool = True) -> Turn:
    user: list[dict[str, Any]] = [{"type": "input_text", "text": f"Trigger: change at 16:{i:02d}:00"}]
    if with_image:
        user.append({"type": "input_text", "text": "trigger frame, t-0s"})
        user.append({"type": "input_image", "image_url": "data:image/jpeg;base64,AAA", "detail": "high"})
    return Turn(t=T0 + i, user=user, assistant=f'{{"thinking": "turn {i}"}}', residue=f"16:{i:02d} change: turn {i}")


# -- the window ---------------------------------------------------------------


def test_messages_carry_system_picture_and_every_raw_turn_in_order():
    thread = SessionThread(max_turns=5, image_turns=2)
    for i in range(3):
        thread.commit(_turn(i), summary=None)
    msgs = thread.messages("SYS", [{"type": "input_text", "text": "now"}])
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert msgs[0]["content"][0]["text"] == "SYS"
    # The picture is rewritten every reply, so it rides with the newest turn,
    # behind the stable prefix the prompt cache can reuse.
    assert msgs[-1]["content"][0]["text"].startswith(PICTURE_HEADING)
    assert PICTURE_EMPTY in msgs[-1]["content"][0]["text"]
    assert [m["content"][0]["text"] for m in msgs if m["role"] == "assistant"] == [
        '{"thinking": "turn 0"}', '{"thinking": "turn 1"}', '{"thinking": "turn 2"}']
    assert msgs[-1]["content"][-1]["text"] == "now"


def test_images_ride_only_on_the_newest_turns():
    thread = SessionThread(max_turns=10, image_turns=2)
    for i in range(4):
        thread.commit(_turn(i), summary=None)
    msgs = thread.messages("SYS", [{"type": "input_text", "text": "now"}])
    users = [m for m in msgs if m["role"] == "user"][:-1]  # all but this turn
    has_image = [any(c.get("type") == "input_image" for c in m["content"]) for m in users]
    assert has_image == [False, False, True, True]
    # A stripped turn says so, and its dangling frame label went with the image.
    stripped = users[0]["content"]
    assert all(not str(c.get("text", "")).startswith("trigger frame") for c in stripped)
    assert "frame(s) omitted" in stripped[-1]["text"]


def test_oldest_turn_folds_into_the_picture_then_the_rewritten_summary_absorbs_it():
    thread = SessionThread(max_turns=2, image_turns=1)
    thread.commit(_turn(0), summary="he sat down")
    thread.commit(_turn(1), summary=None)
    assert len(thread.turns) == 2 and thread.aged == []
    # Third turn: turn 0 falls off the window and waits in the picture block.
    thread.commit(_turn(2), summary=None)
    assert [t.t for t in thread.turns] == [T0 + 1, T0 + 2]
    assert thread.aged == ["16:00 change: turn 0"]
    block = thread.picture_block()
    assert "he sat down" in block and "16:00 change: turn 0" in block and "fold these" in block
    # The next reply rewrites the picture: the aged line is absorbed.
    thread.commit(_turn(3), summary="he sat down; turn 0 was nothing")
    assert thread.aged == []
    assert thread.summary == "he sat down; turn 0 was nothing"
    assert thread.folded == 2


def test_bind_resets_on_a_new_session_only():
    thread = SessionThread()
    thread.commit(_turn(0), summary="picture")
    assert thread.bind(None) is False  # unchanged
    assert thread.bind("s_1") is True and thread.empty
    thread.commit(_turn(1), summary="p2")
    assert thread.bind("s_1") is False and not thread.empty
    assert thread.bind("s_2") is True and thread.summary == "" and not thread.turns


def test_rebuild_from_decisions_keeps_text_and_skips_drops():
    thread = SessionThread(max_turns=2)
    rows = [
        Decision(id="d1", t=T0, trigger="change", trigger_tick_id="x", interpretation="one",
                 actions=[{"type": "annotate", "line": "a"}], thinking="think one"),
        Decision(id="d2", t=T0 + 1, trigger="cue", trigger_tick_id="x", dropped=True,
                 drop_reason="t1_busy"),
        Decision(id="d3", t=T0 + 2, trigger="change", trigger_tick_id="x", interpretation="three",
                 actions=[{"type": "speak", "text": "hi", "urgency": "low"}]),
        Decision(id="d4", t=T0 + 3, trigger="change", trigger_tick_id="x", interpretation="four"),
    ]
    n = thread.rebuild(rows, "the picture", lambda t: "16:00")
    assert n == 3
    assert [t.rebuilt for t in thread.turns] == [True, True]
    assert thread.aged == ["16:00 change: one"]  # the third rebuilt turn aged out
    assert thread.summary == "the picture"
    reply = json.loads(thread.turns[0].assistant)
    assert reply["interpretation"] == "three" and reply["actions"][0]["text"] == "hi"
    assert "not kept" in thread.turns[0].user[0]["text"]


def test_render_reply_and_residue_are_compact_and_bounded():
    resp = T1Response(thinking="x" * 2000, interpretation="picked up a tub",
                      actions=[SpeakAction(text="creatine, huh?", urgency="low"),
                               AnnotateAction(line="held a tub")])
    text = render_reply(resp)
    body = json.loads(text) if not text.endswith("…") else None
    assert len(text) <= 1200
    if body is not None:
        assert body["thinking"] == "x" * 600 and body["actions"][0]["text"] == "creatine, huh?"
    assert residue_line("16:41", "change", resp) == \
        '16:41 change: picked up a tub -> handed off: "creatine, huh?"'


# -- the schema -----------------------------------------------------------------


def test_schema_and_normalize_carry_thinking_and_summary():
    assert "thinking" in T1_JSON_SCHEMA["properties"]
    assert T1_JSON_SCHEMA["properties"]["summary"]["type"] == ["string", "null"]
    assert "thinking" in T1_JSON_SCHEMA["required"] and "summary" in T1_JSON_SCHEMA["required"]
    resp = T1Response(thinking="  a \n b " + "c" * 1000, interpretation="i",
                      summary="  " + "s" * 5000, actions=[AnnotateAction(line="x")])
    norm = normalize(resp, t=T0)
    assert norm.thinking.startswith("a b ") and len(norm.thinking) == THINKING_MAX_CHARS
    assert len(norm.summary) == SUMMARY_MAX_CHARS
    assert normalize(T1Response(summary="   ", actions=[AnnotateAction(line="x")]), t=T0).summary is None


def test_thread_objective_is_appended_only_in_thread_mode_after_the_cached_prefix():
    plain = build_system_prompt("P", "7d", ["l"])
    threaded = build_system_prompt("P", "7d", ["l"], thread=True)
    assert THREAD_OBJECTIVE not in plain and THREAD_OBJECTIVE in threaded
    # The objective stays first and byte-identical; the addendum follows it.
    assert threaded.startswith(plain.split("\n\n## Who you are working for")[0])
    assert "ONE CONTINUING CONVERSATION" in threaded


def test_voice_prompt_carries_the_clerks_picture_between_learned_and_job():
    without = build_voice_system_prompt("P", ["l"])
    with_ = build_voice_system_prompt("P", ["l"], picture="bottle is furniture")
    assert VOICE_PICTURE not in without and without == build_voice_system_prompt("P", ["l"], picture="  ")
    assert VOICE_PICTURE in with_ and "bottle is furniture" in with_
    assert with_.index("learned") < with_.index(VOICE_PICTURE) < with_.index("## Your job")


# -- the reasoner in thread mode ------------------------------------------------


class RecordingFake(FakeReasonerClient):
    """The fake, remembering every input and answering with a summary."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[list[dict[str, Any]]] = []

    async def complete(self, input_messages):
        self.inputs.append(input_messages)
        resp, meta = await super().complete(input_messages)
        resp.thinking = f"thought on call {self.calls}"
        resp.summary = f"picture after call {self.calls}"
        return resp, meta


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "t.db") as database:
        yield database


@pytest.fixture
def frame_store():
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(30):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    return store


@pytest.fixture
def settings():
    return Settings(demo_mode=True, openai_api_key=None)


def test_second_wakeup_sees_the_first_turn_and_the_picture(db, frame_store, settings):
    db.insert_session(Session(id="s_a", started_t=T0 - 1))
    client = RecordingFake()
    reasoner = build_reasoner(db, frame_store, settings, client, thread=SessionThread())

    async def go():
        assert reasoner.try_escalate(esc_at("food_in_frame", T0 + 20))
        await drain(reasoner)
        assert reasoner.try_escalate(esc_at("change", T0 + 25))
        await drain(reasoner)

    asyncio.run(go())
    first, second = client.inputs
    assert [m["role"] for m in first] == ["system", "user"]
    assert PICTURE_EMPTY in first[1]["content"][0]["text"]
    assert THREAD_OBJECTIVE in first[0]["content"][0]["text"]
    roles = [m["role"] for m in second]
    assert roles == ["system", "user", "assistant", "user"]
    assert "picture after call 1" in second[-1]["content"][0]["text"]
    assert "thought on call 1" in second[2]["content"][0]["text"]
    assert second[-1]["content"][1]["text"].startswith("Trigger: change")
    assert "Spoken aloud since your last wake-up: nothing." in json.dumps(second[-1]["content"])
    # The row carries the thinking; the picture is persisted for the session.
    rows = db.decisions_between(T0, T0 + 100)
    assert [r.thinking for r in rows] == ["thought on call 1", "thought on call 2"]
    assert db.get_thread_summary("s_a") == "picture after call 2"
    assert reasoner.stats()["thread"]["turns"] == 2


def test_a_new_session_starts_a_new_conversation(db, frame_store, settings):
    db.insert_session(Session(id="s_a", started_t=T0 - 1))
    client = RecordingFake()
    reasoner = build_reasoner(db, frame_store, settings, client, thread=SessionThread())

    async def go():
        reasoner.try_escalate(esc_at("food_in_frame", T0 + 20))
        await drain(reasoner)
        db.end_session("s_a", T0 + 21)
        reasoner.bump_epoch()
        db.insert_session(Session(id="s_b", started_t=T0 + 22))
        reasoner.try_escalate(esc_at("change", T0 + 25))
        await drain(reasoner)

    asyncio.run(go())
    second = client.inputs[1]
    assert [m["role"] for m in second] == ["system", "user"]
    assert PICTURE_EMPTY in second[1]["content"][0]["text"]
    assert reasoner.thread.session_id == "s_b"


def test_restart_rebuilds_the_thread_from_the_decisions_table(db, frame_store, settings):
    db.insert_session(Session(id="s_a", started_t=T0 - 1))
    client = RecordingFake()
    first = build_reasoner(db, frame_store, settings, client, thread=SessionThread())

    async def go(reasoner, trigger, t):
        reasoner.try_escalate(esc_at(trigger, t))
        await drain(reasoner)

    asyncio.run(go(first, "food_in_frame", T0 + 20))
    # "Restart": a fresh reasoner and thread over the same database.
    client2 = RecordingFake()
    second = build_reasoner(db, frame_store, settings, client2, thread=SessionThread())
    asyncio.run(go(second, "change", T0 + 30))
    msgs = client2.inputs[0]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "picture after call 1" in msgs[-1]["content"][0]["text"]
    assert "not kept" in msgs[1]["content"][0]["text"]
    assert "thought on call 1" in msgs[2]["content"][0]["text"]
    assert second.thread.turns[0].rebuilt is True


def test_without_a_thread_the_envelope_is_unchanged(db, frame_store, settings):
    client = RecordingFake()
    reasoner = build_reasoner(db, frame_store, settings, client)

    async def go():
        reasoner.try_escalate(esc_at("food_in_frame", T0 + 20))
        await drain(reasoner)

    asyncio.run(go())
    msgs = client.inputs[0]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert THREAD_OBJECTIVE not in msgs[0]["content"][0]["text"]
    assert reasoner.stats()["thread"] is None
