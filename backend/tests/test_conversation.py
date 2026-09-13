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
    """§1: a short quiet window after a close, then hand-offs land again."""

    h.agent.request("wine glass", "statement", decision_id="d", episode_id=None,
                    esc=make_escalation())
    await settle()
    assert h.agent.current() is None

    assert h.agent.request("the cereal", "statement", decision_id="d2",
                           episode_id=None, esc=None) == "conversation_cooldown"

    h.clock.t += h.settings.timings.conversation_cooldown_s + 1
    assert h.agent.request("the cereal", "statement", decision_id="d3",
                           episode_id=None, esc=None).startswith("handed_off:")
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
