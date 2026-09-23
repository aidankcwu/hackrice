"""US-M03: the ``look`` action end to end, and a decider-fired sound cue
(docs/PERCEPTION.md "Gate and actions", "Normalisation additions").

Fakes for capture, conversation and reasoner, as the other action tests do;
the re-run tests build a real Reasoner the way ``test_reasoner_decider`` does.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.actions.handlers import (
    ACT_FAILED,
    ACTED,
    LOOK_CHAINED,
    LOOK_UNAVAILABLE,
    LOOKED,
    SOUND_RATE_LIMITED,
    ActionHandler,
)
from pipeline.actions.speech import SpeechLimiter, clear_spoken, spoken
from pipeline.config import Settings, Timings
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.models import AiBlock, Decision, Escalation, SensorBlock, Tick
from pipeline.reasoner.client import FakeReasonerClient
from pipeline.reasoner.decider import FakeDecider
from pipeline.reasoner.decider_settings import DeciderSettings
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.schema import (
    T1_JSON_SCHEMA,
    ActAction,
    AnnotateAction,
    AskAction,
    LogInsightAction,
    LookAction,
    SpeakAction,
    T1Response,
    normalize,
)
from pipeline.reasoner.writers import FakeWriters

T0 = 1_757_700_000.0
DECISION = "d_0001"
QUESTION = "What is the wearer holding?"
ANSWER = "a glass of red wine"
WINDOW_N = 12


# -- fakes ------------------------------------------------------------------


class FakeCapture:
    """``look`` records the question; ``take_look_answer`` hands back what the
    test queued, once, like the loop's one-slot answer."""

    look_wait_s = 0.5

    def __init__(self) -> None:
        self.looks: list[str] = []
        self.answer: tuple[float, str] | None = None

    def look(self, question: str) -> None:
        self.looks.append(question)

    def take_look_answer(self) -> tuple[float, str] | None:
        answer, self.answer = self.answer, None
        return answer


class FakeConversation:
    def __init__(self, open_: bool = False) -> None:
        self.open_ = open_
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def current(self) -> dict[str, Any] | None:
        return {"id": "c_1"} if self.open_ else None

    def request(self, topic: str, mode: str = "statement", **kw: Any) -> str:
        self.requests.append((topic, mode, kw))
        return "handed_off:c_1"


class FakeReasoner:
    def __init__(self) -> None:
        self.reruns: list[tuple[str, str, str]] = []

    def rerun_after_look(self, decision_id: str, question: str, answer: str) -> bool:
        self.reruns.append((decision_id, question, answer))
        return True


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    for name in list(DeciderSettings.model_fields) + ["T1_MODEL"]:
        for key in (name.upper(), name):
            monkeypatch.delenv(key, raising=False)
    clear_spoken()
    yield
    clear_spoken()


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def timings():
    return Timings.demo()


def make_handler(db, timings, **kw) -> ActionHandler:
    return ActionHandler(db, SpeechLimiter(min_gap_s=0, max_per_hour=10), timings, **kw)


def response(*actions) -> T1Response:
    return T1Response(interpretation="…", confidence=0.5, actions=list(actions))


def kinds(resp) -> list[str]:
    return [a.type for a in resp.actions]


def write_decision(db, *actions: dict[str, Any], decision_id: str = DECISION) -> Decision:
    decision = Decision(id=decision_id, t=T0, trigger="food_in_frame",
                        trigger_tick_id="t_1", actions=list(actions))
    db.insert_decision(decision)
    return decision


def read_decision(db, decision_id: str = DECISION) -> Decision:
    return next(d for d in db.list_decisions() if d.id == decision_id)


# -- schema -----------------------------------------------------------------


def test_t1_response_parses_a_look_and_a_sound_act():
    resp = T1Response.model_validate({
        "interpretation": "wine glass in hand",
        "confidence": 0.8,
        "actions": [
            {"type": "look", "question": QUESTION, "reason": "ownership unknown"},
            {"type": "act", "kind": "sound", "args": {"name": "soft"}},
        ],
    })
    look, act = resp.actions
    assert isinstance(look, LookAction) and look.question == QUESTION
    assert isinstance(act, ActAction) and act.kind == "sound" and act.args == {"name": "soft"}
    assert resp.deferred_for_look is False


def test_look_question_is_bounded_and_act_is_sound_only():
    with pytest.raises(ValidationError):
        LookAction(question="x")
    with pytest.raises(ValidationError):
        LookAction(question="q" * 121)
    with pytest.raises(ValidationError):
        ActAction(kind="calendar_block")  # type: ignore[arg-type]
    assert ActAction().args == {}


def test_both_variants_are_in_the_strict_t1_schema():
    variants = {v["properties"]["type"]["enum"][0]: v
                for v in T1_JSON_SCHEMA["properties"]["actions"]["items"]["anyOf"]}
    assert variants["act"]["properties"]["kind"]["enum"] == ["sound"]
    assert variants["act"]["properties"]["args"]["properties"]["name"]["enum"] == [
        "chime", "tick", "soft"]
    assert set(variants["look"]["required"]) == {"type", "question", "reason"}
    json.dumps(T1_JSON_SCHEMA)


# -- normalize --------------------------------------------------------------


def test_normalize_keeps_only_the_first_look():
    norm = normalize(response(LookAction(question=QUESTION),
                              LookAction(question="Is anyone else there?"),
                              LogInsightAction(text="wine")), t=T0)
    assert kinds(norm) == ["look", "log_insight", "annotate"]
    assert norm.actions[0].question == QUESTION


def test_normalize_defers_speak_and_ask_behind_a_look():
    norm = normalize(response(SpeakAction(text="wine on a weeknight"),
                              AskAction(text="is this yours"),
                              LookAction(question=QUESTION),
                              LogInsightAction(text="wine")), t=T0)
    assert kinds(norm) == ["look", "log_insight", "annotate"]
    assert norm.deferred_for_look is True


def test_normalize_without_a_look_does_not_defer():
    norm = normalize(response(SpeakAction(text="wine on a weeknight")), t=T0)
    assert kinds(norm) == ["speak", "annotate"]
    assert norm.deferred_for_look is False


def test_normalize_drops_a_sound_next_to_a_speak():
    sound = ActAction(args={"name": "chime"})
    norm = normalize(response(sound, SpeakAction(text="stand up")), t=T0)
    assert kinds(norm) == ["speak", "annotate"]
    norm = normalize(response(sound, LogInsightAction(text="screen")), t=T0)
    assert kinds(norm) == ["act", "log_insight", "annotate"]
    # Ask beats speak first; with the speak gone the sound stays.
    norm = normalize(response(sound, SpeakAction(text="x y"), AskAction(text="yours?")), t=T0)
    assert kinds(norm) == ["act", "ask", "annotate"]


# -- handler: look ----------------------------------------------------------


async def test_look_goes_to_the_capture_and_is_recorded_looked(db, timings):
    capture = FakeCapture()
    handler = make_handler(db, timings, capture=capture)

    result = handler.apply(DECISION, T0, response(LookAction(question=QUESTION)))

    assert capture.looks == [QUESTION]
    assert result["outcomes"] == {0: {"outcome": LOOKED}}
    assert handler._look is not None and handler._look[0] == DECISION


def test_look_without_a_capture_is_unavailable(db, timings):
    handler = make_handler(db, timings)
    result = handler.apply(DECISION, T0, response(LookAction(question=QUESTION)))
    assert result["outcomes"] == {0: {"outcome": LOOK_UNAVAILABLE}}


async def test_answer_with_no_conversation_reruns_the_decision_once(db, timings):
    capture, reasoner = FakeCapture(), FakeReasoner()
    handler = make_handler(db, timings, capture=capture)
    handler.reasoner = reasoner
    write_decision(db, {"type": "look", "question": QUESTION, "outcome": LOOKED})

    handler.apply(DECISION, T0, response(LookAction(question=QUESTION)))
    await asyncio.sleep(0.15)
    assert reasoner.reruns == [], "nothing landed yet, nothing re-ran"
    capture.answer = (T0, ANSWER)
    await asyncio.sleep(0.3)

    assert reasoner.reruns == [(DECISION, QUESTION, ANSWER)]
    assert handler._look is None
    (row,) = read_decision(db).actions
    assert (row["outcome"], row["answer"]) == ("answered:rerun", ANSWER)


async def test_answer_with_an_open_conversation_goes_to_the_voice_agent(db, timings):
    capture, reasoner = FakeCapture(), FakeReasoner()
    conversation = FakeConversation(open_=True)
    handler = make_handler(db, timings, capture=capture, conversation=conversation)
    handler.reasoner = reasoner
    write_decision(db, {"type": "look", "question": QUESTION, "outcome": LOOKED})

    handler.apply(DECISION, T0, response(LookAction(question=QUESTION)))
    capture.answer = (T0, ANSWER)
    await asyncio.sleep(0.3)

    assert reasoner.reruns == []
    ((topic, mode, kw),) = conversation.requests
    assert QUESTION in topic and ANSWER in topic
    assert mode == "statement" and kw["decision_id"] == DECISION
    (row,) = read_decision(db).actions
    assert row["outcome"] == "answered:conversation:handed_off:c_1"


async def test_a_closed_conversation_still_reruns(db, timings):
    capture, reasoner = FakeCapture(), FakeReasoner()
    conversation = FakeConversation(open_=False)
    handler = make_handler(db, timings, capture=capture, conversation=conversation)
    handler.reasoner = reasoner

    handler.apply(DECISION, T0, response(LookAction(question=QUESTION)))
    capture.answer = (T0, ANSWER)
    await asyncio.sleep(0.3)

    assert conversation.requests == []
    assert reasoner.reruns == [(DECISION, QUESTION, ANSWER)]


async def test_an_answer_that_never_lands_times_out(db, timings):
    capture = FakeCapture()
    capture.look_wait_s = 0.2
    handler = make_handler(db, timings, capture=capture)
    handler.reasoner = FakeReasoner()
    write_decision(db, {"type": "look", "question": QUESTION, "outcome": LOOKED})

    handler.apply(DECISION, T0, response(LookAction(question=QUESTION)))
    await asyncio.sleep(0.45)

    assert handler.reasoner.reruns == []
    assert handler._look is None
    (row,) = read_decision(db).actions
    assert row["outcome"] == "look_timeout"


# -- handler: sound act -----------------------------------------------------


def test_sound_act_takes_the_act_send_path_and_flips_on_act_result(db, timings):
    handler = make_handler(db, timings)
    sent: list[dict[str, Any]] = []
    handler.send_act = lambda message: sent.append(json.loads(message)) or True

    result = handler.apply(DECISION, T0, response(ActAction(args={"name": "soft"})))

    (msg,) = sent
    assert (msg["type"], msg["kind"], msg["args"]) == ("act", "sound", {"name": "soft"})
    patch = result["outcomes"][0]
    assert patch["outcome"] == "sent" and patch["id"] == msg["id"]
    write_decision(db, {"type": "act", "id": msg["id"], "kind": "sound", "outcome": "sent"})

    assert handler.on_act_result(msg["id"], ok=True) == ACTED
    assert read_decision(db).actions[0]["outcome"] == ACTED


def test_a_failed_sound_is_act_failed_and_earns_no_sentence(db, timings):
    handler = make_handler(db, timings)
    sent: list[str] = []
    handler.send_act = lambda message: sent.append(message) or True
    result = handler.apply(DECISION, T0, response(ActAction(args={"name": "tick"})))
    act_id = result["outcomes"][0]["id"]

    assert handler.on_act_result(act_id, ok=False, detail="no speaker") == ACT_FAILED
    assert spoken == [], "a failed chime is not worth a line"


def test_sound_acts_are_rate_limited_by_the_handlers_own_limiter(db, timings):
    handler = make_handler(db, timings, sound_max_per_hour=2)
    sent: list[str] = []
    handler.send_act = lambda message: sent.append(message) or True

    outcomes = [
        handler.apply(DECISION, T0 + i, response(ActAction(args={"name": "soft"})))
        ["outcomes"][0]["outcome"]
        for i in range(3)
    ]

    assert outcomes == ["sent", "sent", SOUND_RATE_LIMITED]
    assert len(sent) == 2
    assert handler.sound.stats() == {"allowed": 2, "suppressed": 1, "in_last_hour": 2}
    # The window slides: an hour later a cue plays again.
    assert handler.act(DECISION, T0 + 3601.0, "sound", {"name": "soft"})["outcome"] == "sent"


# -- decider path: fired act/look become actions, and the re-run -----------


def make_window(n: int = WINDOW_N, **flags: Any) -> list[Tick]:
    return [
        Tick(
            tick_id=f"t_{i:08d}", t=T0 + i, seq=i,
            sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.1,
                               phash="0000000000000000" if i < n // 2 else "ffffffffffff0000"),
            ai=AiBlock(as_of=T0 + i, age_ms=0, scene="office", activity="seated", **flags),
            frame_ref=f"f_{i:08d}",
        )
        for i in range(n)
    ]


def make_escalation(trigger: str = "food_in_frame") -> Escalation:
    window = make_window()
    return Escalation(trigger=trigger, t=window[-1].t, tick=window[-1], window=window,
                      reason="synthetic")


@pytest.fixture
def frame_store():
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(WINDOW_N):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    return store


@pytest.fixture
def settings():
    return Settings(demo_mode=True, openai_api_key=None)


def build_reasoner(db, frame_store, settings, **kwargs) -> Reasoner:
    return Reasoner(
        db, frame_store, FakeReasonerClient(),
        SpeechLimiter(settings.timings.speech_min_gap, settings.timings.speech_max_per_hour),
        settings, **kwargs,
    )


async def drain(reasoner: Reasoner, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while reasoner.busy:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("reasoner never released the T1 slot")
        await asyncio.sleep(0.005)
    await asyncio.sleep(0)


async def test_decider_path_builds_a_sound_act_and_a_look(db, frame_store, settings):
    decider = FakeDecider({"act": 0.9, "look": 0.9, "speak": 0.9}, topic="alcohol")
    writers = FakeWriters()
    reasoner = build_reasoner(db, frame_store, settings, decider=decider, writers=writers)
    capture = FakeCapture()
    reasoner.handler.capture = capture

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)
    (decision,) = db.list_decisions()

    # The speak was written but deferred behind the look; the sound survives
    # because the speak is gone.
    assert [a["type"] for a in decision.actions] == ["annotate", "act", "look"]
    assert decision.writers == ["summary_line", "handoff_topic", "act:sound", "look_question"]
    act, look = decision.actions[1], decision.actions[2]
    assert (act["kind"], act["args"], act["outcome"]) == ("sound", {"name": "soft"}, "sent")
    assert (look["question"], look["reason"], look["outcome"]) == (
        FakeWriters.DEFAULTS["look_question"], "alcohol", LOOKED)
    assert capture.looks == [FakeWriters.DEFAULTS["look_question"]]
    assert decision.spoke is False


async def test_a_failed_look_writer_drops_the_look(db, frame_store, settings):
    decider = FakeDecider({"look": 0.9, "speak": 0.9})
    reasoner = build_reasoner(db, frame_store, settings, decider=decider,
                              writers=FakeWriters(look_question=None))

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)
    (decision,) = db.list_decisions()

    assert [a["type"] for a in decision.actions] == ["annotate", "speak"]
    assert decision.writers == ["summary_line", "handoff_topic", "look:failed"]


async def test_the_rerun_sees_the_answer_and_never_chains(db, frame_store, settings):
    decider = FakeDecider({"look": 0.9, "speak": 0.9}, topic="alcohol")
    writers = FakeWriters()
    reasoner = build_reasoner(db, frame_store, settings, decider=decider, writers=writers)
    capture = FakeCapture()
    reasoner.handler.capture = capture

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)
    capture.answer = (T0, ANSWER)
    await asyncio.sleep(0.3)
    await drain(reasoner)

    first, second = sorted(db.list_decisions(), key=lambda d: d.id)
    assert first.path == "decider" and second.path == "decider:look"
    assert first.trigger == second.trigger == "food_in_frame"
    assert first.actions[1]["outcome"] == "answered:rerun"
    # The decider saw the question and the answer the second time.
    assert "look" not in decider.calls[0]
    assert decider.calls[1]["look"] == {
        "question": FakeWriters.DEFAULTS["look_question"], "answer": ANSWER}
    # The re-run decided the deferred speak afresh and dropped its own look.
    assert [a["type"] for a in second.actions] == ["annotate", "speak", "look"]
    assert second.actions[2]["outcome"] == LOOK_CHAINED
    assert second.spoke is True
    assert capture.looks == [FakeWriters.DEFAULTS["look_question"]], "one look, never chained"
    assert reasoner.stats()["look_reruns"] == 1
    assert reasoner._look_context == {}, "the context is spent by the re-run"


async def test_rerun_after_look_without_context_or_slot_is_dropped(db, frame_store, settings):
    reasoner = build_reasoner(db, frame_store, settings, decider=FakeDecider({}),
                              writers=FakeWriters())
    assert reasoner.rerun_after_look("d_9999", QUESTION, ANSWER) is False

    assert reasoner.try_escalate(make_escalation()) is True
    reasoner._look_context["d_0001"] = reasoner._look_context.get(
        "d_0001", (make_escalation(), {}, None))
    assert reasoner.busy
    assert reasoner.rerun_after_look("d_0001", QUESTION, ANSWER) is False
    await drain(reasoner)
    assert reasoner.stats()["look_reruns_dropped"] == 2


async def test_the_clerk_path_reruns_with_the_answer_as_a_note(db, frame_store, settings):
    reasoner = build_reasoner(db, frame_store, settings)
    capture = FakeCapture()
    reasoner.handler.capture = capture
    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)
    (first,) = db.list_decisions()

    assert reasoner.rerun_after_look(first.id, QUESTION, ANSWER) is True
    await drain(reasoner)

    first, second = sorted(db.list_decisions(), key=lambda d: d.id)
    assert second.path == "clerk:look"
    assert second.dropped is False
    assert reasoner.stats()["look_reruns"] == 1


async def test_sound_cap_comes_from_the_decider_settings(db, frame_store, settings):
    reasoner = build_reasoner(db, frame_store, settings, decider=FakeDecider({}),
                              writers=FakeWriters(),
                              decider_settings=DeciderSettings(act_sound_max_per_hour=1))
    assert reasoner.handler.sound.max_per_hour == 1
    assert build_reasoner(db, frame_store, settings).handler.sound.max_per_hour == 6
