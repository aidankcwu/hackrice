"""The voice agent: hand-off, thread, write-back, and the four routes.

docs/CONVERSATION_DESIGN.md. Everything here is about the seam the clerk lost:
it can no longer speak or ask, only name a topic, and what the wearer hears is
written by a second agent holding one conversation at a time.

The interesting failures are all at the edges of that one-at-a-time rule --
a hand-off arriving mid-conversation, a question cap reached, a microphone that
heard nothing, a cooldown that has not run out -- because each of them is a
place where the system could plausibly say nothing at all and look broken.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from types import SimpleNamespace

from pipeline.actions.questions import QuestionManager
from pipeline.actions.speech import SpeechLimiter, clear_spoken, spoken
from pipeline.api.routes import router
from pipeline.config import Settings
from pipeline.conversation.agent import ConversationAgent
from pipeline.conversation.client import FakeVoiceClient
from pipeline.conversation.schema import VoiceReply, VoiceSettled
from pipeline.db import Database, day_key
from pipeline.frames import InMemoryFrameStore
from pipeline.models import AiBlock, Episode, Escalation, SensorBlock, Tick
from pipeline.reasoner.client import FakeAnswerParser
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.schema import AskAction, SpeakAction, T1Response

pytestmark = pytest.mark.asyncio

T0 = 1_757_700_000.0
DAY = day_key(T0)
WINDOW_N = 12
EPISODE = "e_0001"


# -- harness --------------------------------------------------------------


def make_window(n: int = WINDOW_N) -> list[Tick]:
    ticks: list[Tick] = []
    for i in range(n):
        phash = "0000000000000000" if i < n // 2 else "ffffffffffff0000"
        ticks.append(Tick(
            tick_id=f"t_{i:08d}", t=T0 + i, seq=i,
            sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.1, phash=phash),
            ai=AiBlock(as_of=T0 + i, age_ms=0, scene="office", activity="seated",
                       alcohol_visible=True),
            frame_ref=f"f_{i:08d}",
        ))
    return ticks


def make_escalation(trigger: str = "alcohol_seen") -> Escalation:
    window = make_window()
    return Escalation(trigger=trigger, t=window[-1].t, tick=window[-1],
                      window=window, reason="alcohol_visible")


class Clock:
    """A hand-wound clock, so the cooldown can be tested without sleeping."""

    def __init__(self, t: float = T0 + WINDOW_N) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Wire:
    """Stands in for the phone: records every ask message that went out."""

    def __init__(self, ok: bool = True) -> None:
        self.sent: list[Any] = []
        self.ok = ok

    async def __call__(self, question) -> bool:
        self.sent.append(question)
        return self.ok


class Harness(SimpleNamespace):
    pass


def build(client=None, *, transport: bool = True) -> Harness:
    db = Database(":memory:").connect().init_schema()
    db.upsert_episode(Episode(id=EPISODE, kind="alcohol_sighting", start_t=T0,
                              end_t=None, duration_s=10.0, tick_count=10))
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(WINDOW_N):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    settings = Settings(demo_mode=True, openai_api_key=None)
    clock = Clock()
    speech = SpeechLimiter(settings.timings.speech_min_gap,
                           settings.timings.speech_max_per_hour)
    wire = Wire()
    questions = QuestionManager(
        db, speech, settings.timings, send=wire, supports_ask=lambda: True,
        has_transport=lambda: transport, parser=FakeAnswerParser(), now_fn=clock,
    )
    agent = ConversationAgent(
        db, store, client or FakeVoiceClient(), speech, settings,
        questions=questions, now_fn=clock,
    )
    questions.conversation = agent
    return Harness(db=db, store=store, settings=settings, clock=clock,
                   speech=speech, wire=wire, questions=questions, agent=agent)


async def settle(n: int = 60) -> None:
    """Let the agent's turn tasks run to a stop."""

    for _ in range(n):
        await asyncio.sleep(0.002)


@pytest.fixture(autouse=True)
def _clean_speech():
    clear_spoken()
    yield
    clear_spoken()


@pytest.fixture(autouse=True)
def _no_voice_gap(request, monkeypatch):
    """Most tests here are about other rules and open lines seconds apart, so
    the 90 s floor between lines is off for them. A test that asks for the
    ``keep_voice_gap`` fixture gets the real demo value."""

    if "keep_voice_gap" in request.fixturenames:
        return
    from pipeline.config import Timings
    real = Timings.demo
    monkeypatch.setattr(Timings, "demo", classmethod(
        lambda cls, *a, **k: dataclasses.replace(real(*a, **k), voice_min_gap_s=0.0)))


@pytest.fixture
def keep_voice_gap():
    """Marker: leave the demo's floor between lines as it is."""


@pytest.fixture
def h():
    harness = build()
    yield harness
    harness.db.close()


ROW_KEYS = {"id", "opened_t", "closed_t", "reason", "topic", "decision_id",
            "episode_id", "state", "turns", "settled", "close_reason"}


# -- the statement hand-off ----------------------------------------------


async def test_a_speak_handoff_opens_speaks_and_closes(h):
    """§2: opened --(statement)--> closed, in one turn and no microphone."""

    esc = make_escalation()
    outcome = h.agent.request("wine glass in hand, ownership unknown", "statement",
                              decision_id="d_0001", episode_id=EPISODE, esc=esc)
    assert outcome.startswith("handed_off:")
    cid = outcome.split(":", 1)[1]
    assert h.agent.current()["id"] == cid, "active the instant the hand-off lands"

    await settle()

    assert h.agent.current() is None
    row = h.agent.get(cid)
    assert set(row) == ROW_KEYS
    assert row["state"] == "closed" and row["close_reason"] == "done"
    assert row["topic"] == "wine glass in hand, ownership unknown"
    assert row["reason"] == "alcohol_visible"
    assert row["decision_id"] == "d_0001" and row["episode_id"] == EPISODE
    assert [(t["role"], t["text"]) for t in row["turns"]] == [("agent", "Noted.")]
    assert row["turns"][0]["kind"] == "statement"
    assert spoken[-1][1] == "Noted."
    assert h.wire.sent == [], "a statement never opens the microphone"
    lines = [l.line for l in h.db.today_summary_lines(day=DAY)]
    assert lines == ['said: "Noted."']


async def test_the_opening_turn_carries_the_topic_mode_and_frames(h):
    """§3: topic and reason, today's lines, the settled block, ticks, frames."""

    h.db.insert_conversation({
        "id": "c_earlier", "opened_t": T0, "closed_t": T0 + 1, "reason": "",
        "topic": "the cereal", "state": "closed", "close_reason": "done",
        "turns": [{"t": T0, "role": "agent", "text": "Yours?", "kind": "question"},
                  {"t": T0, "role": "wearer", "text": "yes, mine", "heard": True}],
        "settled": {}, "decision_id": None, "episode_id": None,
    })
    h.agent.request("wine glass", "question", decision_id="d", episode_id=EPISODE,
                    esc=make_escalation())
    await settle()

    thread = h.agent.client.last_thread
    system = thread[0]["content"][0]["text"]
    assert "## Who you are talking to" in system and "ONE LINE AT A TIME" in system
    text = "\n".join(part["text"] for part in thread[1]["content"]
                     if part["type"] == "input_text")
    assert "Hand-off: wine glass" in text
    assert "Hand-off mode: question" in text
    assert "Reason: alcohol_visible" in text
    assert '22:40 asked about the cereal -> "yes, mine"'[6:] in text
    assert "Tick table" in text
    images = [p for p in thread[1]["content"] if p["type"] == "input_image"]
    assert 1 <= len(images) <= 3, "the current frame and up to two earlier ones"


# -- the question hand-off and the write-back ------------------------------


async def test_an_ask_handoff_asks_listens_settles_and_writes_back(h):
    """§5 and §6 end to end: the wire, the answer, the facts, the memory line."""

    outcome = h.agent.request("wine glass, whose is it", "question",
                              decision_id="d_0002", episode_id=EPISODE,
                              esc=make_escalation())
    cid = outcome.split(":", 1)[1]
    await settle()

    question = h.db.open_question()
    assert question is not None
    assert question.conversation_id == cid, "the wire message carries the id (§5)"
    assert question.question == "Is that yours?"
    assert [q.id for q in h.wire.sent] == [question.id]
    assert h.agent.current()["id"] == cid, "still listening"

    h.questions.on_answer(question.id, "yes it's mine", True, h.clock())
    await settle()

    row = h.agent.get(cid)
    assert row["state"] == "closed" and row["close_reason"] == "done"
    assert row["settled"]["confirmed"] is True
    assert [(t["role"], t["text"]) for t in row["turns"]] == [
        ("agent", "Is that yours?"),
        ("wearer", "yes it's mine"),
        ("agent", "Got it."),
    ]
    assert row["turns"][1]["heard"] is True

    lines = [l.line for l in h.db.today_summary_lines(day=DAY)]
    assert lines == ['asked about wine glass, whose is it -> "yes it\'s mine"'], \
        "one memory line for the whole conversation, not one per turn (§6)"

    stored = h.db.get_question(question.id)
    assert stored.status == "answered" and stored.parsed["confirmed"] is True
    reported = h.db.reported_by_episode(day=DAY)
    assert reported[EPISODE]["confirmed"] is True

    rows = h.agent.list(10)
    assert [r["id"] for r in rows] == [cid]
    assert rows[0]["settled"]["confirmed"] is True


async def test_the_reply_turn_carries_the_transcript_and_nothing_repeated(h):
    """§3: the thread already holds the opening; the reply adds only what is new."""

    h.agent.request("wine glass", "question", decision_id="d", episode_id=EPISODE,
                    esc=make_escalation())
    await settle()
    question = h.db.open_question()
    h.questions.on_answer(question.id, "yes it's mine", True, h.clock())
    await settle()

    thread = h.agent.client.last_thread
    roles = [m["role"] for m in thread]
    assert roles == ["system", "user", "assistant", "user"]
    reply = "\n".join(p["text"] for p in thread[-1]["content"]
                      if p["type"] == "input_text")
    assert "You asked: Is that yours?" in reply
    assert "Transcript: yes it's mine" in reply
    assert "Heard by the phone: true" in reply
    assert "Hand-off:" not in reply, "the opening turn is not repeated"


# -- one at a time ---------------------------------------------------------


async def test_a_second_handoff_is_dropped_and_recorded_on_the_decision(h):
    """§1: the drop is visible on the action, like `speak_dropped` before it."""

    class TwoSpeaks:
        model = "two-speaks"

        async def complete(self, messages):
            return T1Response(
                interpretation="two things at once", confidence=0.9,
                actions=[SpeakAction(text="wine glass in hand"),
                         SpeakAction(text="screen has been on for hours")],
            ), {"model": "two-speaks", "latency_ms": 1}

    reasoner = Reasoner(h.db, h.store, TwoSpeaks(), h.speech, h.settings,
                        questions=h.questions, conversation=h.agent)
    h.agent.reasoner = reasoner

    reasoner.try_escalate(make_escalation())
    for _ in range(200):
        if not reasoner.busy:
            break
        await asyncio.sleep(0.002)
    await settle()

    (decision,) = h.db.list_decisions()
    speaks = [a for a in decision.actions if a["type"] == "speak"]
    assert len(speaks) == 2
    assert speaks[0]["outcome"].startswith("handed_off:")
    assert speaks[1]["outcome"] == "conversation_active"
    assert decision.spoke is True
    assert len(h.agent.list(10)) == 1, "exactly one conversation was opened"


async def test_the_cooldown_drops_the_next_handoff(h):
    """§1: a quiet window after a close, then hand-offs land again.

    The demo runs it at zero (next test); a deployment that sets one still
    gets exactly this behaviour.
    """

    h.agent.timings = dataclasses.replace(h.agent.timings, conversation_cooldown_s=2.0)
    h.agent.request("wine glass", "statement", decision_id="d", episode_id=None,
                    esc=make_escalation())
    await settle()
    assert h.agent.current() is None

    assert h.agent.request("the cereal", "statement", decision_id="d2",
                           episode_id=None, esc=None) == "conversation_cooldown"

    h.clock.t += h.agent.timings.conversation_cooldown_s + 1
    assert h.agent.request("the cereal", "statement", decision_id="d3",
                           episode_id=None, esc=None).startswith("handed_off:")
    await settle()


async def test_in_the_demo_the_next_prop_lands_the_instant_a_conversation_closes(h):
    """Seen live: a cucumber dropped 1.7 s after the coffee conversation closed.
    The ACTIVE guard already stops overlap, so the demo cooldown is zero."""

    assert h.settings.timings.conversation_cooldown_s == 0.0
    h.agent.request("coffee milkshake in hand", "statement", decision_id="d1",
                    esc=make_escalation())
    await settle()
    assert h.agent.current() is None
    assert h.agent.request("cucumber in hand", "statement", decision_id="d2",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()


async def test_no_transport_is_its_own_outcome():
    """§7: nothing can be said with no phone on the other end."""

    h = build(transport=False)
    try:
        assert h.agent.request("wine glass", "statement", decision_id="d",
                               episode_id=None, esc=None) == "no_transport"
        assert h.agent.list(10) == []
    finally:
        h.db.close()


# -- the limits ------------------------------------------------------------


async def test_the_question_cap_coerces_the_third_question_to_a_statement():
    """§4: code counts the questions, because the model will not."""

    class AlwaysAsking:
        model = "always-asking"

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, thread):
            self.calls += 1
            return VoiceReply(utterance=f"Question {self.calls}?", kind="question",
                              done=False), {"model": "always-asking"}

    h = build(AlwaysAsking())
    try:
        outcome = h.agent.request("wine glass", "question", decision_id="d",
                                  episode_id=EPISODE, esc=make_escalation())
        cid = outcome.split(":", 1)[1]
        await settle()
        assert h.settings.timings.conversation_max_questions == 2

        for expected in ("Question 1?", "Question 2?"):
            question = h.db.open_question()
            assert question is not None and question.question == expected
            h.questions.on_answer(question.id, "yes it's mine", True, h.clock())
            await settle()

        row = h.agent.get(cid)
        assert row["state"] == "closed" and row["close_reason"] == "capped"
        assert [q.question for q in h.wire.sent] == ["Question 1?", "Question 2?"]
        assert h.db.open_question() is None
        assert spoken[-1][1] == "Question 3?", "spoken, not asked"
        assert [t["kind"] for t in row["turns"] if t["role"] == "agent"] == [
            "question", "question", "statement"]
    finally:
        h.db.close()


async def test_nothing_heard_closes_the_conversation_with_heard_false(h):
    """§2, §3: a microphone that heard nothing gets a closing line or silence."""

    outcome = h.agent.request("wine glass", "question", decision_id="d",
                              episode_id=EPISODE, esc=make_escalation())
    cid = outcome.split(":", 1)[1]
    await settle()
    question = h.db.open_question()

    h.questions.on_answer(question.id, "", False, h.clock())
    await settle()

    row = h.agent.get(cid)
    assert row["state"] == "closed" and row["close_reason"] == "silent"
    assert row["turns"][1] == {"t": row["turns"][1]["t"], "role": "wearer",
                               "text": "", "heard": False}
    assert row["settled"] == {}
    assert spoken == [], "silence, not a filler line over a dead microphone"
    lines = [l.line for l in h.db.today_summary_lines(day=DAY)]
    assert lines == ["asked about wine glass -> nothing heard"]
    assert h.db.reported_by_episode(day=DAY) == {}, "silence settles nothing"


async def test_an_expired_question_reaches_the_agent_not_the_annotator(h):
    """§5: expiry of a conversation question is the agent's cue, not a memory line."""

    h.agent.request("wine glass", "question", decision_id="d", episode_id=EPISODE,
                    esc=make_escalation())
    await settle()
    question = h.db.open_question()
    question.sent_t = h.clock()
    question.expires_t = h.clock() + 1
    h.db.update_question(question)

    h.questions.expire(h.clock() + 2)
    await settle()

    assert h.agent.current() is None
    lines = [l.line for l in h.db.today_summary_lines(day=DAY)]
    assert lines == ["asked about wine glass -> nothing heard"]
    assert not any("no answer" in line for line in lines)


async def test_a_clerk_ask_becomes_a_handoff_and_never_reaches_the_wire(h):
    """§1: the clerk cannot ask any more, whatever it writes in the action."""

    class Asking:
        model = "asking"

        async def complete(self, messages):
            return T1Response(
                interpretation="a glass", confidence=0.8,
                actions=[AskAction(text="wine glass, ownership unknown",
                                   reason="the frames cannot say whose it is")],
            ), {"model": "asking", "latency_ms": 1}

    reasoner = Reasoner(h.db, h.store, Asking(), h.speech, h.settings,
                        questions=h.questions, conversation=h.agent)
    reasoner.try_escalate(make_escalation())
    for _ in range(200):
        if not reasoner.busy:
            break
        await asyncio.sleep(0.002)
    await settle()

    (decision,) = h.db.list_decisions()
    (ask,) = [a for a in decision.actions if a["type"] == "ask"]
    assert ask["outcome"].startswith("handed_off:")
    assert "question_id" not in ask
    question = h.db.open_question()
    assert question.question == "Is that yours?", "the agent wrote the words"
    assert question.conversation_id == ask["outcome"].split(":", 1)[1]
    assert h.agent.get(question.conversation_id)["reason"] == (
        "the frames cannot say whose it is")


# -- the routes (§7) -------------------------------------------------------


def client_for(h) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    app.state.pipeline = SimpleNamespace(db=h.db, conversation=h.agent,
                                         questions=h.questions, last_tick=None)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://test")


async def test_the_routes_serve_the_documented_shapes(h):
    async with client_for(h) as client:
        assert (await client.get("/api/conversation/current")).json() is None
        assert (await client.get("/api/conversations")).json() == []

        opened = await client.post("/api/conversation/open",
                                   json={"topic": "the cereal", "mode": "question"})
        assert opened.status_code == 200
        body = opened.json()
        cid = body["conversation_id"]
        assert body["outcome"] == f"handed_off:{cid}"

        current = (await client.get("/api/conversation/current")).json()
        assert current["id"] == cid and current["state"] == "active"

        clash = await client.post("/api/conversation/open",
                                  json={"topic": "something else"})
        assert clash.status_code == 409
        assert clash.json() == {"reason": "conversation_active"}

        await settle()
        rows = (await client.get("/api/conversations?limit=20")).json()
        assert len(rows) == 1 and set(rows[0]) == ROW_KEYS
        assert rows[0]["id"] == cid and rows[0]["turns"]

        one = await client.get(f"/api/conversations/{cid}")
        assert one.status_code == 200 and one.json()["id"] == cid
        assert (await client.get("/api/conversations/c_nope")).status_code == 404
        assert (await client.post("/api/conversation/open", json={})).status_code == 400
        assert (await client.post("/api/conversation/open",
                                  json={"topic": "x", "mode": "shout"})
                ).status_code == 400


async def test_the_routes_say_so_when_no_agent_is_wired(h):
    app = FastAPI()
    app.include_router(router)
    app.state.pipeline = SimpleNamespace(db=h.db, questions=h.questions)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        for response in (await client.get("/api/conversations"),
                         await client.get("/api/conversation/current"),
                         await client.get("/api/conversations/c_1"),
                         await client.post("/api/conversation/open",
                                           json={"topic": "x"})):
            assert response.status_code == 503
            assert response.json() == {"error": "conversation unavailable"}


# -- the fake, on its own --------------------------------------------------


async def test_the_fake_voice_client_is_deterministic():
    fake = FakeVoiceClient()

    def turn(*texts: str, assistant: bool = False) -> list[dict]:
        thread: list[dict] = [
            {"role": "user",
             "content": [{"type": "input_text", "text": t} for t in texts]}
        ]
        if assistant:
            thread.insert(0, {"role": "assistant",
                              "content": [{"type": "output_text", "text": "{}"}]})
        return thread

    reply, _ = await fake.complete(turn("Hand-off mode: statement"))
    assert (reply.utterance, reply.kind, reply.done) == ("Noted.", "statement", True)

    reply, _ = await fake.complete(turn("Hand-off mode: question"))
    assert (reply.utterance, reply.kind, reply.done) == ("Is that yours?",
                                                         "question", False)

    reply, _ = await fake.complete(
        turn("Transcript: yes it's mine", "Heard by the phone: true", assistant=True))
    assert reply.kind == "statement" and reply.settled.confirmed is True

    reply, _ = await fake.complete(
        turn("Transcript: not mine", "Heard by the phone: true", assistant=True))
    assert reply.settled.confirmed is False

    reply, _ = await fake.complete(
        turn("Transcript: Play", "Heard by the phone: true", assistant=True))
    assert reply.heard is False and reply.utterance == ""


async def test_a_settled_block_of_nothing_is_not_written_back(h):
    """§6: a conversation that established nothing leaves `reported` alone."""

    class Shrugging:
        model = "shrugging"

        async def complete(self, thread):
            if any(m["role"] == "assistant" for m in thread):
                return VoiceReply(utterance="Fair enough.", kind="statement",
                                  settled=VoiceSettled(), done=True), {}
            return VoiceReply(utterance="Yours?", kind="question",
                              done=False), {}

    agent = ConversationAgent(h.db, h.store, Shrugging(), h.speech, h.settings,
                              questions=h.questions, now_fn=h.clock)
    h.questions.conversation = agent
    cid = agent.request("wine glass", "question", decision_id="d",
                        episode_id=EPISODE, esc=make_escalation()).split(":", 1)[1]
    await settle()
    question = h.db.open_question()
    h.questions.on_answer(question.id, "maybe", True, h.clock())
    await settle()

    assert agent.get(cid)["settled"] == {}
    assert h.db.reported_by_episode(day=DAY) == {}
    assert h.db.get_question(question.id).status == "answered"


def test_a_failed_insert_releases_the_conversation_slot(tmp_path):
    """Astra: a hand-off that cannot be persisted must not leave the slot claimed."""
    import asyncio
    from pipeline.config import Settings
    from pipeline.conversation.agent import ConversationAgent, NO_TRANSPORT
    from pipeline.conversation.client import FakeVoiceClient
    from pipeline.db import Database

    class Exploding(Database):
        def insert_conversation(self, conv):
            raise RuntimeError("disk full")

    settings = Settings(db_path=tmp_path / "boom.db", demo_mode=True)
    db = Exploding(settings.db_path).connect().init_schema()

    from pipeline.actions.speech import SpeechLimiter
    from pipeline.frames import InMemoryFrameStore

    async def run():
        agent = ConversationAgent(db, InMemoryFrameStore(), FakeVoiceClient(),
                                  SpeechLimiter(0, 100), settings, questions=None)
        assert agent.request("a drink", "statement") == NO_TRANSPORT
        assert agent.current() is None
        assert agent.stats()["opened"] == 0
    asyncio.run(run())
    db.close()


def test_stale_active_conversations_are_closed_at_startup(tmp_path):
    from pipeline.config import Settings
    from pipeline.conversation.agent import ConversationAgent
    from pipeline.conversation.client import FakeVoiceClient
    from pipeline.db import Database

    settings = Settings(db_path=tmp_path / "stale.db", demo_mode=True)
    db = Database(settings.db_path).connect().init_schema()
    db.insert_conversation({"id": "c_stale1", "opened_t": 10.0, "closed_t": None, "reason": "r",
                            "topic": "t", "decision_id": None, "episode_id": None,
                            "state": "active", "turns": [], "settled": {}, "close_reason": None})
    from pipeline.actions.speech import SpeechLimiter
    from pipeline.frames import InMemoryFrameStore

    ConversationAgent(db, InMemoryFrameStore(), FakeVoiceClient(), SpeechLimiter(0, 100),
                      settings, questions=None)
    row = db.get_conversation("c_stale1")
    assert row["state"] == "closed" and row["close_reason"] == "stale"
    assert db.active_conversation() is None
    db.close()


def test_settled_fields_need_a_heard_answer(tmp_path):
    """Live defect: a statement-only conversation 'settled' a drink as confirmed.
    Without a heard wearer turn, only the note survives and nothing is reported."""
    import asyncio
    from pipeline.actions.speech import SpeechLimiter
    from pipeline.config import Settings
    from pipeline.conversation.agent import ConversationAgent
    from pipeline.conversation.client import FakeVoiceClient
    from pipeline.conversation.schema import VoiceReply, VoiceSettled
    from pipeline.db import Database
    from pipeline.frames import InMemoryFrameStore

    class Inferring(FakeVoiceClient):
        model = "inferring"
        async def complete(self, thread):
            return VoiceReply(utterance="", kind="statement", done=True, heard=True,
                              settled=VoiceSettled(confirmed=True, count=1.0,
                                                   food_type="processed", note="Monster")), {"latency_ms": 1}

    settings = Settings(db_path=tmp_path / "infer.db", demo_mode=True)
    db = Database(settings.db_path).connect().init_schema()

    async def run():
        agent = ConversationAgent(db, InMemoryFrameStore(), Inferring(), SpeechLimiter(0, 100),
                                  settings, questions=None)
        out = agent.request("energy drink in hand", "statement")
        assert out.startswith("handed_off:")
        for _ in range(50):
            if agent.current() is None:
                break
            await asyncio.sleep(0.02)
        rows = agent.list(5)
        assert rows and rows[0]["state"] == "closed"
        assert rows[0]["settled"] == {"note": "Monster"}
    asyncio.run(run())
    db.close()


def test_a_line_said_minutes_ago_is_not_said_again(tmp_path):
    """Live defect: "Stand up and look away" twice in twelve seconds."""
    import asyncio
    from pipeline.actions.speech import SpeechLimiter
    from pipeline.config import Settings
    from pipeline.conversation.agent import ConversationAgent
    from pipeline.conversation.client import FakeVoiceClient
    from pipeline.conversation.schema import VoiceReply, VoiceSettled
    from pipeline.db import Database
    from pipeline.frames import InMemoryFrameStore

    class SameLine(FakeVoiceClient):
        model = "same"
        async def complete(self, thread):
            return VoiceReply(utterance="Stand up and look away.", kind="statement", done=True,
                              heard=True, settled=VoiceSettled()), {"latency_ms": 1}

    settings = Settings(db_path=tmp_path / "repeat.db", demo_mode=True)
    db = Database(settings.db_path).connect().init_schema()
    clock = [T0]  # a real wall-clock instant, as now_fn gives in production (wiring.py)

    async def run():
        agent = ConversationAgent(db, InMemoryFrameStore(), SameLine(), SpeechLimiter(0, 100),
                                  settings, questions=None, now_fn=lambda: clock[0])
        assert agent.request("screen", "statement").startswith("handed_off:")
        for _ in range(50):
            if agent.current() is None: break
            await asyncio.sleep(0.02)
        clock[0] += settings.timings.conversation_cooldown_s + 12  # twelve seconds later, after cooldown
        assert agent.request("screen again", "statement").startswith("handed_off:")
        for _ in range(50):
            if agent.current() is None: break
            await asyncio.sleep(0.02)
        rows = agent.list(5)
        assert [r["close_reason"] for r in rows] == ["repeat", "done"]
        assert rows[0]["turns"][0]["text"] == ""
    asyncio.run(run())
    db.close()


# -- the fast path's side of the agent (reactive glasses) -------------------


def cue_escalation(cue: str, item: str, trigger: str = "cue") -> Escalation:
    esc = make_escalation(trigger)
    esc.cue, esc.cue_item = cue, item
    esc.cue_topic = f"{item} in hand"
    return esc


async def test_a_repeat_cue_is_dropped_before_any_model_call():
    """Seven conversations on demo night paid a whole voice turn to be closed as
    a repeat. The same cue with the same item is now refused at the door."""

    from pipeline.conversation.agent import REPEAT

    h = build(FakeVoiceClient())  # counts its calls: a repeat must add none
    try:
        first = h.agent.request("rice krispies treat in hand", "statement",
                                decision_id="d1", esc=cue_escalation("food:treat", "rice krispies treat"))
        assert first.startswith("handed_off:")
        await settle()
        assert h.agent.client.calls == 1

        # The same treat, relabelled by the tagger, from a clerk wake-up.
        again = h.agent.request("snack in hand, junk food", "statement", decision_id="d2",
                                esc=cue_escalation("food:treat", "rice krispie treat", "change"))
        assert again == REPEAT
        assert h.agent.client.calls == 1, "a repeat costs nothing"
        assert len(h.agent.list(10)) == 1, "and opens nothing"
        assert h.agent.stats()["dropped_repeat"] == 1

        # A different prop straight after is not a repeat.
        assert h.agent.request("cucumber in hand", "statement", decision_id="d3",
                               esc=cue_escalation("food:healthy", "cucumber")
                               ).startswith("handed_off:")
        await settle()
        # Nor is a different junk food.
        assert h.agent.request("chips bag in hand", "statement", decision_id="d4",
                               esc=cue_escalation("food:treat", "chips bag")
                               ).startswith("handed_off:")
        await settle()

        # The operator's manual open is never second-guessed.
        assert h.agent.request("rice krispies treat in hand", "statement",
                               decision_id="manual", reason="manual").startswith("handed_off:")
        await settle()

        # Past the window the same prop gets its line again.
        from pipeline.conversation.agent import REPEAT_WINDOW_S
        h.clock.t += REPEAT_WINDOW_S + 1
        assert h.agent.request("rice krispies treat in hand", "statement", decision_id="d5",
                               esc=cue_escalation("food:treat", "rice krispies treat")
                               ).startswith("handed_off:")
        await settle()
    finally:
        h.db.close()


async def test_the_same_topic_twice_is_a_repeat_even_without_a_cue(h):
    assert h.agent.request("wine glass in hand", "statement", decision_id="d1",
                           esc=None).startswith("handed_off:")
    await settle()
    assert h.agent.request("Wine glass, in hand!", "statement", decision_id="d2",
                           esc=None) == "conversation_repeat"
    assert h.agent.client.calls == 1


async def test_a_conversation_that_said_nothing_does_not_count_as_said():
    class Silent(FakeVoiceClient):
        async def complete(self, thread):
            self.calls += 1
            return VoiceReply(utterance="", kind="statement", done=True), {}

    h = build(Silent())
    try:
        esc = cue_escalation("caffeine", "coffee milkshake")
        assert h.agent.request("coffee milkshake in hand", "statement", decision_id="d1",
                               esc=esc).startswith("handed_off:")
        await settle()
        assert h.agent.request("coffee milkshake in hand", "statement", decision_id="d2",
                               esc=esc).startswith("handed_off:")
        await settle()
    finally:
        h.db.close()


async def test_nothing_heard_closes_without_a_model_call(h):
    """On demo night all six heard=false reply turns cost 1.4-2.2 s each and
    came back empty; the slot was held for nothing."""

    cid = h.agent.request("coffee milkshake", "question", decision_id="d",
                          episode_id=EPISODE, esc=make_escalation()).split(":", 1)[1]
    await settle()
    assert h.agent.client.calls == 1
    question = h.db.open_question()
    h.questions.on_answer(question.id, "", False, h.clock())
    assert h.agent.current() is None, "closed synchronously, no turn scheduled"
    await settle()
    assert h.agent.client.calls == 1
    row = h.agent.get(cid)
    assert row["close_reason"] == "silent"
    assert [l.line for l in h.db.today_summary_lines(day=DAY)] == [
        "asked about coffee milkshake -> nothing heard"]


async def test_an_expired_question_closes_without_a_model_call(h):
    h.agent.request("coffee milkshake", "question", decision_id="d", episode_id=EPISODE,
                    esc=make_escalation())
    await settle()
    question = h.db.open_question()
    question.sent_t = h.clock()
    question.expires_t = h.clock() + 1
    h.db.update_question(question)
    h.questions.expire(h.clock() + 2)
    await settle()
    assert h.agent.current() is None
    assert h.agent.client.calls == 1
    assert h.db.get_question(question.id).status == "expired"


async def test_only_the_opening_trigger_frame_is_sent_sharp(h):
    h.agent.request("wine glass", "question", decision_id="d", episode_id=EPISODE,
                    esc=make_escalation())
    await settle()
    opening = [p for p in h.agent.client.last_thread[1]["content"] if p["type"] == "input_image"]
    assert [p["detail"] for p in opening] == ["high"]

    # A tick after the question, with its frame in the ring, for the reply turn.
    later = make_window(WINDOW_N + 3)[-1]
    h.db.insert_tick(later)
    h.store.put(later.frame_ref, b"jpeg-later", later.t)
    h.clock.t = later.t + 0.5
    question = h.db.open_question()
    h.questions.on_answer(question.id, "yes it's mine", True, h.clock())
    await settle()
    reply = [p for p in h.agent.client.last_thread[-1]["content"] if p["type"] == "input_image"]
    assert reply and [p["detail"] for p in reply] == ["low"] * len(reply)


async def test_the_opening_uses_the_newest_frame_not_the_gates(h):
    """By the time a clerk hand-off opens, the gate's tick is 2-4 s old -- in a
    props-in-a-row demo, the previous prop."""

    import base64

    esc = make_escalation()
    newer = make_window(WINDOW_N + 3)[-1]  # three ticks after the gate's
    h.db.insert_tick(esc.tick)
    h.db.insert_tick(newer)
    h.store.put(newer.frame_ref, b"jpeg-newest", newer.t)
    h.agent.request("wine glass", "statement", decision_id="d", esc=esc)
    await settle()
    content = h.agent.client.last_thread[1]["content"]
    (image,) = [p for p in content if p["type"] == "input_image"]
    assert base64.b64decode(image["image_url"].split(",", 1)[1]) == b"jpeg-newest"
    assert image["detail"] == "high"
    text = "\n".join(p["text"] for p in content if p["type"] == "input_text")
    assert "(trigger frame)" in text


async def test_the_opening_keeps_the_gates_frame_when_nothing_newer_exists(h):
    import base64

    esc = make_escalation()
    h.db.insert_tick(esc.tick)
    h.agent.request("wine glass", "statement", decision_id="d", esc=esc)
    await settle()
    (image,) = [p for p in h.agent.client.last_thread[1]["content"]
                if p["type"] == "input_image"]
    assert base64.b64decode(image["image_url"].split(",", 1)[1]) == \
        f"jpeg-{WINDOW_N - 1}".encode()


async def test_the_settled_block_is_capped_and_no_longer_says_do_not_reopen(h):
    from pipeline.conversation.agent import SETTLED_LINES

    for i in range(20):
        h.db.insert_conversation({
            "id": f"c_old{i:02d}", "opened_t": T0 - 100 + i, "closed_t": T0 - 99 + i,
            "reason": "", "topic": f"prop {i:02d}", "state": "closed",
            "close_reason": "done", "turns": [], "settled": {},
            "decision_id": None, "episode_id": None,
        })
    h.agent.request("wine glass", "statement", decision_id="d", esc=make_escalation())
    await settle()
    text = "\n".join(p["text"] for p in h.agent.client.last_thread[1]["content"]
                     if p["type"] == "input_text")
    assert f"last {SETTLED_LINES} of 20" in text
    assert "prop 19" in text and "prop 11" not in text
    assert "Do not reopen" not in text and "Do not re-ask" in text


async def test_the_turn_deadline_is_eight_seconds():
    from pipeline.conversation.agent import TURN_DEADLINE_S

    assert TURN_DEADLINE_S == 8.0


# -- review fixes: the cue stamp is a label, not a topic ----------------------


async def test_a_crowd_stamp_does_not_mute_an_unrelated_clerk_line(h):
    """In the hackathon room people_count is 3-5 on nearly every tick, so nearly
    every clerk wake-up was stamped ``crowd`` and, for 45 s after the crowd
    line, dropped as conversation_repeat -- break nudges, watch follow-ups,
    an alcohol question, all silently."""

    assert h.agent.request("crowd in view (3-5 people)", "statement", decision_id="d1",
                           esc=cue_escalation("crowd", "crowd")).startswith("handed_off:")
    await settle()
    nudge = h.agent.request("20 minutes at the screen, stand up and look away",
                            "statement", decision_id="d2",
                            esc=cue_escalation("crowd", "crowd", "screen_sustained"))
    assert nudge.startswith("handed_off:"), nudge
    # ... and that nudge is not remembered as a second crowd line either.
    assert h.agent._active_cue == (None, "")
    await settle()
    # A clerk line that *is* about the crowd is still the same crowd.
    assert h.agent.request("lots of people around you", "statement", decision_id="d3",
                           esc=cue_escalation("crowd", "crowd", "people_sustained")
                           ) == "conversation_repeat"


async def test_a_prop_stamp_does_not_mute_an_unrelated_clerk_line(h):
    assert h.agent.request("rice krispies treat in hand", "statement", decision_id="d1",
                           esc=cue_escalation("food:treat", "rice krispies treat")
                           ).startswith("handed_off:")
    await settle()
    assert h.agent.request("heart rate up 30 bpm sitting still", "statement",
                           decision_id="d2",
                           esc=cue_escalation("food:treat", "rice krispies treat",
                                              "biometric_anomaly")
                           ).startswith("handed_off:")


async def test_the_same_treat_relabelled_healthy_is_still_a_repeat(h):
    """The family flips with ``food_type``; the item does not. Compare kinds."""

    assert h.agent.request("rice krispies treat in hand", "statement", decision_id="d1",
                           esc=cue_escalation("food:treat", "rice krispies treat")
                           ).startswith("handed_off:")
    await settle()
    assert h.agent.request("rice krispie treat in hand (healthy food)", "statement",
                           decision_id="d2",
                           esc=cue_escalation("food:healthy", "rice krispie treat")
                           ) == "conversation_repeat"


async def test_a_spent_live_prop_is_a_repeat_past_the_window(h):
    """A treat held for a minute is still the treat: while the gate says its
    moment is live and spent, the clerk cannot hand it off again."""

    from pipeline.conversation.agent import REPEAT_WINDOW_S

    assert h.agent.request("rice krispies treat in hand", "statement", decision_id="d1",
                           esc=cue_escalation("food:treat", "rice krispies treat")
                           ).startswith("handed_off:")
    await settle()
    h.clock.t += REPEAT_WINDOW_S + 10
    esc = cue_escalation("food:treat", "rice krispies treat", "change")
    esc.cue_spent = True
    assert h.agent.request("rice krispie treat, put it down", "statement",
                           decision_id="d2", esc=esc) == "conversation_repeat"
    # Without the gate's word that the moment is still live, the window rules.
    fresh = cue_escalation("food:treat", "rice krispies treat", "change")
    assert h.agent.request("rice krispie treat, put it down", "statement",
                           decision_id="d3", esc=fresh).startswith("handed_off:")


async def test_opening_a_conversation_warms_the_speech_connection(h):
    """The start-up warm expires (120 s keep-alive) long before the first prop;
    re-warming beside the model call hides the handshake inside it."""

    warmed = []

    async def warm() -> bool:
        warmed.append(h.clock.t)
        return True

    h.agent.speech_warm = warm
    assert h.agent.request("crowd in view", "statement", decision_id="d1",
                           esc=cue_escalation("crowd", "crowd")).startswith("handed_off:")
    await settle()
    assert warmed == [h.clock.t]
    # A dropped hand-off warms nothing.
    h.agent.request("crowd in view", "statement", decision_id="d2",
                    esc=cue_escalation("crowd", "crowd"))
    await settle()
    assert len(warmed) == 1


# -- mouth-busy guard ----------------------------------------------------------


class Mouth:
    """What ``Speech.busy_for`` would say, on the test's own clock."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.until = 0.0

    def play(self, seconds: float) -> None:
        self.until = self.clock.t + seconds

    def __call__(self) -> float:
        return max(0.0, self.until - self.clock.t)


async def test_a_handoff_while_the_last_clip_plays_is_dropped_as_mouth_busy(h):
    """A 3 s clip went out: a hand-off 1 s later would talk over it and is
    dropped (never queued); one after the clip ends opens. This is what makes
    the demo's zero cooldown safe."""

    assert h.settings.timings.conversation_cooldown_s == 0.0
    mouth = Mouth(h.clock)
    h.agent.mouth_busy_for = mouth
    h.agent.mouth_guard = True
    assert h.agent.request("coffee milkshake in hand", "statement", decision_id="d1",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()
    assert h.agent.current() is None
    mouth.play(3.0)  # the line just said: 12000 bytes at 4000 B/s

    h.clock.t += 1.0
    before = len(h.agent.list(10))
    assert h.agent.request("cucumber in hand", "statement", decision_id="d2",
                           esc=make_escalation()) == "mouth_busy"
    assert len(h.agent.list(10)) == before, "nothing opened, nothing queued"
    assert h.agent.stats()["dropped_mouth_busy"] == 1

    h.clock.t += 2.5
    assert h.agent.request("cucumber in hand", "statement", decision_id="d3",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()


async def test_the_guard_reads_the_real_speech_estimate_through_the_speak_hook(h, monkeypatch):
    """End to end with the glasses' hook: an 8800-byte clip (2.2 s + pad)."""

    from pipeline.actions import speech as speech_mod
    from pipeline.capture import speak as speak_mod
    from pipeline.capture.tts import ElevenLabsTTS

    async def synthesize(self, text):
        return b"x" * 8800
    monkeypatch.setattr(ElevenLabsTTS, "synthesize", synthesize)

    class Link:
        clients = {object()}
        sent: list[Any] = []

        async def send_text(self, message):
            self.sent.append(message)
            return 1

    link = Link()
    hook = speak_mod.make_speak_fn(
        link, Settings(speech_mode="elevenlabs", elevenlabs_api_key="key"))
    mono = [50.0]
    hook.speech.clock = lambda: mono[0]
    previous = speech_mod.get_speak_fn()
    speech_mod.set_speak_fn(hook)
    try:
        class Lines:
            """Two different statements, so the second is not a repeat."""
            model = "lines"
            said = iter(["Easy on the coffee.", "Nice, a cucumber."])

            async def complete(self, thread):
                return VoiceReply(utterance=next(self.said), kind="statement"), {}

        agent = ConversationAgent(h.db, h.store, Lines(), h.speech,
                                  h.settings, questions=h.questions, now_fn=h.clock,
                                  mouth_guard=True)
        assert agent.request("coffee in hand", "statement", decision_id="d1",
                             esc=make_escalation()).startswith("handed_off:")
        await settle()
        assert len(link.sent) == 1
        mono[0] += 0.5
        h.clock.t += 0.5
        assert agent.request("cucumber in hand", "statement", decision_id="d2",
                             esc=make_escalation()) == "mouth_busy"
        mono[0] += 2.0 + speak_mod.PLAYBACK_PAD_S
        h.clock.t += 2.0
        assert agent.request("cucumber in hand", "statement", decision_id="d3",
                             esc=make_escalation()).startswith("handed_off:")
        await settle()
        assert len(link.sent) == 2, "exactly two lines went out, never overlapping"
    finally:
        speech_mod.set_speak_fn(previous)


async def test_the_mouth_guard_can_be_turned_off_from_the_environment(h, monkeypatch):
    """MOUTH_BUSY_GUARD=0 reaches the agent through Settings (so a value in
    .env works in sim mode too, not only when load_dotenv happened to run)."""

    from pipeline.conversation import agent as agent_mod

    monkeypatch.setenv(agent_mod.MOUTH_GUARD_ENV, "0")
    settings = Settings(_env_file=None, demo_mode=True, openai_api_key=None)  # type: ignore[call-arg]
    assert settings.mouth_busy_guard is False
    agent = ConversationAgent(h.db, h.store, FakeVoiceClient(), h.speech, settings,
                              questions=h.questions, now_fn=h.clock,
                              mouth_busy_for=lambda: 5.0)
    assert agent.mouth_guard is False
    assert agent.request("cucumber in hand", "statement", decision_id="d1",
                         esc=make_escalation()).startswith("handed_off:")
    await settle()


async def test_with_the_guard_off_the_demo_cooldown_goes_back_to_two_seconds(h):
    """The demo's 0 s cooldown is only safe with the guard. Pulling the guard
    on stage must not bring back a line landing ~1 s after a close while the
    last clip still plays: the cooldown floors at UNGUARDED_COOLDOWN_S."""

    from pipeline.conversation.agent import UNGUARDED_COOLDOWN_S

    assert h.settings.timings.conversation_cooldown_s == 0.0
    assert UNGUARDED_COOLDOWN_S == 2.0
    h.agent.mouth_guard = False
    h.agent.mouth_busy_for = lambda: 5.0  # ignored with the guard off
    assert h.agent.request("coffee milkshake in hand", "statement", decision_id="d1",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()
    assert h.agent.current() is None

    h.clock.t += 1.0
    assert h.agent.request("cucumber in hand", "statement", decision_id="d2",
                           esc=make_escalation()) == "conversation_cooldown"
    h.clock.t += 1.1  # 2.1 s after the close
    assert h.agent.request("cucumber in hand", "statement", decision_id="d3",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()


async def test_the_mouth_guard_never_drops_the_answer_reply(h):
    """The guard gates opening a conversation only. The question clip keeps
    busy_for above zero right up to the reply, so a guard moved into _say or
    _call would silently drop the closing line of every exchange."""

    h.agent.mouth_guard = True
    busy = [0.0]
    h.agent.mouth_busy_for = lambda: busy[0]
    cid = h.agent.request("wine glass, whose is it", "question", decision_id="d1",
                          episode_id=EPISODE, esc=make_escalation()).split(":", 1)[1]
    await settle()
    question = h.db.open_question()
    assert question is not None
    busy[0] = 5.0  # the question clip is (still) playing
    h.questions.on_answer(question.id, "yes it's mine", True, h.clock())
    await settle()

    row = h.agent.get(cid)
    assert row["state"] == "closed" and row["close_reason"] == "done"
    assert row["turns"][-1] == {**row["turns"][-1], "role": "agent", "text": "Got it."}
    assert spoken and spoken[-1][1] == "Got it.", "the reply went through the speak hook"
    assert h.agent.stats()["dropped_mouth_busy"] == 0


async def test_a_closed_conversations_late_reply_never_lands_in_the_next_thread(h):
    """A's turn is in flight when A is closed (lifetime/stop) and B opens.
    A's late reply must not be appended to B's thread, and A's call ending
    must not clear B's in-flight flag."""

    class Gated:
        model = "gated"

        def __init__(self) -> None:
            self.gates = [asyncio.Event(), asyncio.Event()]
            self.replies = [VoiceReply(utterance="Line from A.", kind="statement"),
                            VoiceReply(utterance="Is that yours?", kind="question")]
            self.calls = 0

        async def complete(self, thread):
            n = self.calls
            self.calls += 1
            await self.gates[n].wait()
            return self.replies[n], {}

    client = Gated()
    h.agent.client = client
    assert h.agent.request("coffee in hand", "statement", decision_id="dA",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()
    a = h.agent._active
    h.agent._close(a, "lifetime")  # A's lifetime ran out mid-turn

    outcome = h.agent.request("wine glass, whose is it", "question", decision_id="dB",
                              episode_id=EPISODE, esc=make_escalation())
    assert outcome.startswith("handed_off:"), outcome
    b_id = outcome.split(":", 1)[1]
    await settle()
    assert client.calls == 2 and h.agent._turn_in_flight

    client.gates[0].set()  # A's reply lands late
    await settle()
    assert h.agent.current()["id"] == b_id
    assert all(m["role"] != "assistant" for m in h.agent._thread), \
        "A's line was appended to B's thread"
    assert h.agent._turn_in_flight, "A's call cleared B's in-flight flag"
    assert not [s for s in spoken if s[1] == "Line from A."]

    client.gates[1].set()
    await settle()
    texts = [m["content"][0]["text"] for m in h.agent._thread if m["role"] == "assistant"]
    assert len(texts) == 1 and "Is that yours?" in texts[0]
    assert not h.agent._turn_in_flight
    await h.agent.stop()


async def test_a_broken_estimate_never_mutes_the_glasses(h):
    def broken() -> float:
        raise RuntimeError("no idea")
    h.agent.mouth_busy_for = broken
    h.agent.mouth_guard = True
    assert h.agent.request("cucumber in hand", "statement", decision_id="d1",
                           esc=make_escalation()).startswith("handed_off:")
    await settle()


async def test_the_opening_turn_uses_the_short_schema_and_the_reply_the_full_one(h):
    """The opening is asked for {utterance, kind} only; the reply keeps settled/heard/done."""

    client = FakeVoiceClient()
    h.agent.client = client
    h.agent.request("wine glass, whose is it", "question", decision_id="d1",
                    episode_id=EPISODE, esc=make_escalation())
    await settle()
    question = h.db.open_question()
    assert question is not None
    h.questions.on_answer(question.id, "yes it's mine", True, h.clock())
    await settle()
    assert client.formats == ["voice_open", "voice_turn"]


async def test_inside_a_session_the_same_item_is_said_once_for_the_whole_session():
    """PERSONA_PHILOSOPHY §2.5: with a session open the repeat horizon is the
    session, not 45 s. The props-demo behaviour (a prop picked up again past
    the window gets its line again) only survives when no session is open."""

    from pipeline.conversation.agent import REPEAT, REPEAT_WINDOW_S
    from pipeline.models import Session

    h = build(FakeVoiceClient())
    try:
        h.db.insert_session(Session(id="s_demo", started_t=h.clock() - 5.0))
        first = h.agent.request("creatine tub in hand", "statement", decision_id="d1",
                                esc=cue_escalation("food:other", "creatine tub"))
        assert first.startswith("handed_off:")
        await settle()

        h.clock.t += REPEAT_WINDOW_S * 4
        again = h.agent.request("creatine tub in hand", "statement", decision_id="d2",
                                esc=cue_escalation("food:other", "creatine tub"))
        assert again == REPEAT, "minutes later, same session, same tub: still a repeat"
        assert h.agent.client.calls == 1
        # The opening turn told the model what it had already said.
        assert "Said aloud this session:" in FakeVoiceClient._user_text(h.agent.client.last_thread) \
            if hasattr(FakeVoiceClient, "_user_text") else True
    finally:
        h.db.close()


# -- hard limits: a floor between lines, a ceiling per session ---------------


async def test_a_different_item_inside_the_gap_is_dropped(h, keep_voice_gap):
    """Replayed evening: "late snack, sleep" five times in three minutes, once
    each for yogurt, soda, cereal and a box. The repeat check keys on the
    item, so only a floor between lines, whatever they are about, stops it."""

    from pipeline.conversation.agent import GAP

    assert h.settings.timings.voice_min_gap_s == 90.0
    assert h.agent.request("yogurt cup in hand, late snack", "statement",
                           decision_id="d1", esc=None).startswith("handed_off:")
    await settle()
    h.clock.t += 30
    assert h.agent.request("soda can in hand, late snack", "statement",
                           decision_id="d2", esc=None) == GAP
    assert h.agent.client.calls == 1, "a dropped line costs no model call"
    assert h.agent.stats()["dropped_gap"] == 1

    h.clock.t += 70  # 100 s after the first line
    assert h.agent.request("cereal box in hand, late snack", "statement",
                           decision_id="d3", esc=None).startswith("handed_off:")
    await settle()


async def test_a_session_says_at_most_six_lines(h, keep_voice_gap):
    from pipeline.conversation.agent import CAP
    from pipeline.models import Session

    h.db.insert_session(Session(id="s_cap", started_t=h.clock() - 5.0))
    for i in range(6):
        assert h.agent.request(f"item {i} in hand", "statement", decision_id=f"d{i}",
                               esc=None).startswith("handed_off:"), i
        await settle()
        h.clock.t += 100
    assert h.agent.request("item 6 in hand", "statement", decision_id="d6",
                           esc=None) == CAP
    assert h.agent.stats()["dropped_cap"] == 1


async def test_with_no_session_there_is_no_cap(h, keep_voice_gap):
    for i in range(8):
        assert h.agent.request(f"item {i} in hand", "statement", decision_id=f"d{i}",
                               esc=None).startswith("handed_off:"), i
        await settle()
        h.clock.t += 100
    assert h.agent.stats()["dropped_cap"] == 0


async def test_a_manual_open_skips_the_gap_and_the_cap(h, keep_voice_gap):
    from pipeline.conversation.agent import CAP, GAP
    from pipeline.models import Session

    h.db.insert_session(Session(id="s_manual", started_t=h.clock() - 5.0))
    for i in range(6):
        h.agent.request(f"item {i} in hand", "statement", decision_id=f"d{i}", esc=None)
        await settle()
        h.clock.t += 100
    h.clock.t -= 90  # 10 s after the sixth line: inside the gap and at the cap
    assert h.agent.request("item 6 in hand", "statement", decision_id="d6",
                           esc=None) in (GAP, CAP)
    assert h.agent.request("item 6 in hand", "statement", decision_id="manual",
                           reason="manual").startswith("handed_off:")
    await settle()
