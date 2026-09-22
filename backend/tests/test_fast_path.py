"""The fast path end to end: a cue goes to the mouth, the clerk writes it down.

Before: gate -> clerk (p50 2.2 s) -> hand-off -> voice agent -> TTS, so every
persona cue paid the clerk's whole call before the voice model even started.
Now the gate hands the cue to the voice agent on the tick it appears
(``Reasoner.fast_path``), and the clerk runs beside it for the memory line and
the episode label with its own speak/ask dropped as ``fast_pathed``.

The invariants, each tested here: one conversation at a time, drop never
queue, at most one hand-off per moment, the clerk still annotates, and the
last test drives real ticks through the real ``Pipeline`` objects.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from pipeline.actions.handlers import FAST_PATHED, ActionHandler
from pipeline.actions.questions import QuestionManager
from pipeline.actions.speech import SpeechLimiter, clear_spoken, spoken
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings
from pipeline.conversation.agent import ConversationAgent
from pipeline.conversation.client import FakeVoiceClient
from pipeline.db import Database, day_key
from pipeline.frames import InMemoryFrameStore
from pipeline.gate import gate as gate_module
from pipeline.models import AiBlock, Escalation, SensorBlock, Tick
from pipeline.reasoner import reasoner as reasoner_module
from pipeline.reasoner.client import FakeAnswerParser, FakeReasonerClient
from pipeline.reasoner.reasoner import FAST_PATH_NOTE, Reasoner
from pipeline.reasoner.schema import (
    AnnotateAction,
    AskAction,
    SpeakAction,
    T1Response,
)

pytestmark = pytest.mark.asyncio

T0 = 1_757_700_000.0


@pytest.fixture(autouse=True)
def _clean_speech():
    clear_spoken()
    yield
    clear_spoken()


def cue_tick(seq: int, t: float, **fields: Any) -> Tick:
    base = dict(scene="office", activity="computer_use", objects=["laptop"],
                caption="laptop on a desk", food_present=False, food_type="none",
                drink="none", in_hand=None, phone_in_hand=None, people_count="1-2")
    return Tick(
        tick_id=f"t_{seq:08d}", t=t, seq=seq,
        sensor=SensorBlock(lux_proxy=300.0, frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock.model_validate({"as_of": t, "age_ms": 0, **base, **fields}),
        frame_ref=f"f_{seq:08d}",
    )


TREAT = dict(in_hand="rice krispies treat", food_present=True, food_type="baked_goods",
             caption="holding a rice krispies treat", objects=["rice krispies treat", "laptop"])


def cue_escalation(t: float = T0 + 5) -> Escalation:
    window = [cue_tick(i, T0 + i) for i in range(5)] + [cue_tick(5, t, **TREAT)]
    return Escalation(
        trigger="cue", t=t, tick=window[-1], window=window,
        reason="junk food: rice krispies treat", cue="food:treat",
        cue_item="rice krispies treat",
        cue_topic="rice krispies treat in hand (junk food)", cue_mode="statement",
    )


class Clerk:
    """A clerk that always wants to talk about what it sees, and remembers it."""

    model = "clerk"

    def __init__(self, ask: bool = False, gate: asyncio.Event | None = None) -> None:
        self.calls = 0
        self.ask = ask
        self.gate = gate
        self.last_envelope: list[dict] | None = None

    async def complete(self, messages):
        self.calls += 1
        self.last_envelope = messages
        if self.gate is not None:
            await self.gate.wait()
        talk = (AskAction(text="first coffee today?", reason="count")
                if self.ask else SpeakAction(text="put the treat down"))
        return T1Response(
            interpretation="rice krispy treat in hand", confidence=0.9,
            actions=[AnnotateAction(line="rice krispy treat in hand at the desk"), talk],
        ), {"model": "clerk", "latency_ms": 5}


def harness(clerk=None, transport: bool = True):
    db = Database(":memory:").connect().init_schema()
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(6):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    settings = Settings(demo_mode=True, openai_api_key=None)
    clock = [T0 + 6]
    speech = SpeechLimiter(settings.timings.speech_min_gap, settings.timings.speech_max_per_hour)

    async def send(question) -> bool:
        return True

    questions = QuestionManager(db, speech, settings.timings, send=send,
                                supports_ask=lambda: True,
                                has_transport=lambda: transport,
                                parser=FakeAnswerParser(), now_fn=lambda: clock[0])
    agent = ConversationAgent(db, store, FakeVoiceClient(), speech, settings,
                              questions=questions, now_fn=lambda: clock[0])
    reasoner = Reasoner(db, store, clerk or Clerk(), speech, settings,
                        questions=questions, conversation=agent)
    agent.reasoner = reasoner
    questions.conversation = agent
    questions.reasoner = reasoner
    return db, agent, reasoner, clock


async def drain(reasoner: Reasoner, agent: ConversationAgent) -> None:
    for _ in range(300):
        if not reasoner.busy and agent.current() is None:
            break
        await asyncio.sleep(0.002)
    for _ in range(20):
        await asyncio.sleep(0.002)


# -- Reasoner.fast_path ----------------------------------------------------


async def test_the_outcome_strings_the_gate_relies_on_match_their_owners() -> None:
    from pipeline.conversation import agent as agent_module

    assert gate_module.REPEAT == agent_module.REPEAT
    assert gate_module.NO_TRANSPORT == agent_module.NO_TRANSPORT
    assert gate_module.NO_AGENT == reasoner_module.NO_AGENT
    assert gate_module.HANDED_OFF == "handed_off:"


async def test_a_cue_is_spoken_and_the_clerk_only_writes_it_down() -> None:
    clerk = Clerk()
    db, agent, reasoner, _ = harness(clerk)
    try:
        esc = cue_escalation()
        outcome = reasoner.fast_path(esc)
        assert outcome.startswith("handed_off:")
        cid = outcome.split(":", 1)[1]
        assert esc.handed_off == cid
        assert agent.current()["id"] == cid, "the mouth has it before the clerk runs"
        assert reasoner.busy, "and the clerk was admitted beside it"
        await drain(reasoner, agent)

        (decision,) = db.list_decisions()
        conversation = agent.get(cid)
        assert conversation["decision_id"] == decision.id, "the row points at its decision"
        assert conversation["topic"] == "rice krispies treat in hand (junk food)"
        assert decision.trigger == "cue" and decision.spoke is True
        speak = next(a for a in decision.actions if a["type"] == "speak")
        assert speak["outcome"] == FAST_PATHED, "the clerk's own line is dropped"
        assert len(agent.list(10)) == 1, "one conversation for the moment"
        assert [text for _, text, _ in spoken] == ["Noted."]

        lines = {l.line: l.decision_id for l in db.today_summary_lines(day=day_key(esc.t))}
        assert lines["rice krispy treat in hand at the desk"] == decision.id
        assert 'said: "Noted."' in lines, "and the conversation's own memory line"

        # The clerk was told, in its envelope, that the words are already out.
        text = "\n".join(p.get("text", "") for p in clerk.last_envelope[1]["content"]
                         if p["type"] == "input_text")
        assert FAST_PATH_NOTE.split(":")[0] in text
        assert reasoner.stats()["fast_pathed"] == 1
    finally:
        db.close()


async def test_a_clerk_ask_on_a_fast_pathed_moment_is_dropped_too() -> None:
    db, agent, reasoner, _ = harness(Clerk(ask=True))
    try:
        reasoner.fast_path(cue_escalation())
        await drain(reasoner, agent)
        (decision,) = db.list_decisions()
        ask = next(a for a in decision.actions if a["type"] == "ask")
        assert ask["outcome"] == FAST_PATHED
        assert db.open_question() is None and len(agent.list(10)) == 1
    finally:
        db.close()


async def test_a_busy_mouth_means_nothing_happened_at_all() -> None:
    """Drop, never queue: no decision row, no clerk call, the id given back."""

    clerk = Clerk()
    db, agent, reasoner, _ = harness(clerk)
    try:
        assert agent.request("something else", "statement", decision_id="d_x").startswith(
            "handed_off:")
        before = reasoner._next_seq
        assert reasoner.fast_path(cue_escalation()) == "conversation_active"
        assert reasoner._next_seq == before, "no hole in the decision ids"
        assert clerk.calls == 0 and not reasoner.busy
        assert db.list_decisions() == []
        await drain(reasoner, agent)
    finally:
        db.close()


async def test_no_voice_agent_sends_the_cue_down_the_clerk_path() -> None:
    db, agent, reasoner, _ = harness()
    try:
        reasoner.conversation = None
        assert reasoner.fast_path(cue_escalation()) == reasoner_module.NO_AGENT
        assert not reasoner.busy and db.list_decisions() == []
    finally:
        db.close()


async def test_a_busy_clerk_does_not_stop_the_words() -> None:
    """The clerk's slot is the clerk's problem now: the hand-off happens first,
    and the clerk's half is dropped (and logged) on contention as ever."""

    hold = asyncio.Event()
    clerk = Clerk(gate=hold)
    db, agent, reasoner, clock = harness(clerk)
    try:
        other = cue_escalation()
        other.trigger, other.cue, other.cue_topic = "screen_sustained", None, ""
        assert reasoner.try_escalate(other)
        await asyncio.sleep(0.01)
        assert reasoner.busy

        esc = cue_escalation()
        outcome = reasoner.fast_path(esc)
        assert outcome.startswith("handed_off:")
        cid = outcome.split(":", 1)[1]
        dropped = [d for d in db.list_decisions() if d.dropped]
        assert [d.drop_reason for d in dropped] == ["t1_busy"]
        assert agent.get(cid)["decision_id"] == dropped[0].id
        hold.set()
        await drain(reasoner, agent)
        assert [text for _, text, _ in spoken] == ["Noted."]
    finally:
        db.close()


async def test_a_handler_without_the_flag_still_hands_off_as_before() -> None:
    db, agent, reasoner, _ = harness()
    try:
        handler = ActionHandler(db, reasoner.speech, reasoner.settings.timings,
                                reasoner.questions, agent)
        resp = T1Response(interpretation="x", confidence=0.5,
                          actions=[SpeakAction(text="the treat")])
        plain = cue_escalation()
        result = handler.apply("d_1", plain.t, resp, esc=plain)
        assert result["outcomes"][0]["outcome"].startswith("handed_off:")
        await drain(reasoner, agent)
        flagged = cue_escalation()
        flagged.handed_off = "c_elsewhere"
        result = handler.apply("d_2", flagged.t, resp, esc=flagged)
        assert result["outcomes"][0] == {"outcome": FAST_PATHED}
        assert result["spoke"] is False
    finally:
        db.close()


# -- the real pipeline -------------------------------------------------------


class SpeakingFakeClerk(FakeReasonerClient):
    """The shipped fake clerk, plus a speak on every cue -- the exact thing the
    fast path must stop from becoming a second conversation."""

    async def complete(self, input_messages):
        resp, meta = await super().complete(input_messages)
        text = self._user_text(input_messages)
        if "Trigger: cue" in text:
            resp.actions.append(SpeakAction(text="put the rice krispy treat down"))
        return resp, meta


class ScriptedTicks:
    """A capture source that plays a fixed tick list at the pipeline's clock.

    Each tick is stamped on the tick clock when it is emitted, and its frame is
    put in the ring first, exactly as the sim does.
    """

    def __init__(self, pipeline, script: list[dict[str, Any]], interval_s: float,
                 speed: float) -> None:
        self.pipeline = pipeline
        self.script = script
        self.interval_s = interval_s
        self.speed = speed
        self.emitted: list[Tick] = []

    def __aiter__(self):
        return self._run()

    async def _run(self):
        for seq, fields in enumerate(self.script):
            t = self.pipeline.clock.wall_to_tick(time.time())
            blind = fields.pop("_blind", False)
            current = cue_tick(seq, t, **fields)
            if blind:
                current.ai = None
            self.pipeline.frame_store.put(current.frame_ref, f"jpeg-{seq}".encode(), t)
            self.emitted.append(current)
            yield current
            await asyncio.sleep(self.interval_s / self.speed)


async def run_script(tmp_path, script, name: str):
    settings = Settings(db_path=tmp_path / f"{name}.db", openai_api_key=None,
                        auto_session=False)
    pipeline = build_pipeline(settings, source="sim", reasoner_mode="fake", speed=10,
                              seed_db=False)
    pipeline.reasoner.client = SpeakingFakeClerk()
    source = ScriptedTicks(pipeline, [dict(s) for s in script],
                           settings.tick_interval_s, pipeline.speed)
    pipeline.source = source
    await pipeline.start()
    try:
        for _ in range(2000):
            if (len(source.emitted) == len(script) and pipeline.last_tick is not None
                    and pipeline.last_tick.tick_id == source.emitted[-1].tick_id
                    and not pipeline.reasoner.busy
                    and pipeline.conversation.current() is None):
                break
            await asyncio.sleep(0.005)
        else:  # pragma: no cover - diagnostic
            raise AssertionError("the scripted run did not finish")
        await asyncio.sleep(0.05)
        return pipeline, source, {
            "decisions": pipeline.db.list_decisions(),
            "conversations": pipeline.conversation.list(50),
            "lines": pipeline.db.today_summary_lines(
                day=day_key(source.emitted[-1].t)),
            "gate": pipeline.gate.stats(),
            "episodes": {e.id: (e.kind, pipeline.db.episode_label(e.id))
                         for e in pipeline.db.list_episodes()},
        }
    finally:
        await pipeline.stop()


async def test_the_real_pipeline_speaks_on_the_first_cue_tick_and_only_once(tmp_path):
    desk = {}
    script = [
        desk, desk, {"_blind": True}, {"_blind": True},       # a blind gap first
        TREAT,                                                  # tick 4: the cue
        {**TREAT, "food_type": "snack"},                        # relabelled
        {"_blind": True},
        {**TREAT, "in_hand": "rice krispie treat", "food_type": "dessert"},
        TREAT, TREAT, desk, desk,
    ]
    pipeline, source, seen = await run_script(tmp_path, script, "cue")

    conversations = seen["conversations"]
    assert len(conversations) == 1, [c["topic"] for c in conversations]
    (conv,) = conversations
    assert conv["topic"].startswith("rice krispies treat in hand (junk food)")
    cue_tick_t = source.emitted[4].t
    # Opened on the tick the treat first appeared: within the same tick
    # interval, not a clerk call (or a second tick) later.
    assert 0 <= conv["opened_t"] - cue_tick_t < pipeline.settings.tick_interval_s
    assert conv["close_reason"] == "done"
    assert [t["text"] for t in conv["turns"]] == ["Noted."]

    cue_decisions = [d for d in seen["decisions"] if d.trigger == "cue"]
    assert len(cue_decisions) == 1, "at most one hand-off per moment"
    (decision,) = cue_decisions
    assert decision.trigger_tick_id == source.emitted[4].tick_id
    assert decision.spoke is True and conv["decision_id"] == decision.id
    speak = next(a for a in decision.actions if a["type"] == "speak")
    assert speak["outcome"] == "fast_pathed"

    # The clerk still wrote the moment down, under its own decision, and named
    # the food episode the moment belongs to.
    clerk_lines = [l for l in seen["lines"] if l.decision_id == decision.id]
    assert any(l.line == "cue" for l in clerk_lines), [l.line for l in seen["lines"]]
    assert any(l.line == 'said: "Noted."' for l in seen["lines"])
    assert seen["episodes"][decision.episode_id] == ("food_sighting", "cue")

    # Nothing else woke up about the treat, and nothing else spoke.
    assert all(not d.spoke for d in seen["decisions"] if d.id != decision.id)
    assert seen["gate"]["fast_pathed"] == 1
    assert pipeline.conversation.stats()["opened"] == 1


async def test_the_real_pipeline_ignores_indoor_scene_flicker(tmp_path):
    flicker = [
        {"scene": "office", "activity": "seated"},
        {"scene": "office", "activity": "seated"},
        {"scene": "indoor_other", "activity": "computer_use"},
        {"scene": "indoor_other", "activity": "computer_use"},
        {"scene": "classroom", "activity": "standing"},
        {"scene": "classroom", "activity": "standing"},
        {"scene": "office", "activity": "reading"},
        {"scene": "office", "activity": "reading"},
        {"scene": "home", "activity": "seated"},
        {"scene": "home", "activity": "seated"},
    ]
    pipeline, _, seen = await run_script(tmp_path, flicker, "flicker")
    assert seen["decisions"] == [], [d.trigger for d in seen["decisions"]]
    assert seen["conversations"] == []
    assert pipeline.gate.stats()["fired"] == {}
