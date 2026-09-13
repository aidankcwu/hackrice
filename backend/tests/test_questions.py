import asyncio
from dataclasses import replace

import pytest

from pipeline.actions.handlers import ActionHandler
from pipeline.actions.questions import QuestionManager
from pipeline.actions.speech import SpeechLimiter
from pipeline.config import Timings
from pipeline.db import Database, day_key
from pipeline.models import Decision, Episode
from pipeline.reasoner.client import FakeAnswerParser
from pipeline.reasoner.schema import AnswerParse, AskAction, SpeakAction, T1Response
from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings


@pytest.fixture
def db():
    value = Database(":memory:").connect().init_schema()
    yield value
    value.close()


def manager(db, *, supported=True, transport=True, send_ok=True, timings=None):
    sent = []
    async def send(q):
        sent.append(q)
        return send_ok
    timings = timings or replace(Timings.demo(), speech_min_gap=0)
    speech = SpeechLimiter(timings.speech_min_gap, timings.speech_max_per_hour)
    q = QuestionManager(db, speech, timings, send=send,
                        supports_ask=lambda: supported,
                        has_transport=lambda: transport,
                        parser=FakeAnswerParser(), now_fn=lambda: 100.0)
    return q, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("supported,transport,reason", [
    (False, True, "ask_unsupported"), (True, False, "no_transport"),
])
async def test_transport_guards_are_recorded(db, supported, transport, reason):
    q, _ = manager(db, supported=supported, transport=transport)
    row, got = q.ask(decision_id="d", t=10, episode_id="e",
                     action=AskAction(text="Is that yours?"))
    assert got == reason
    assert row.status == "suppressed" and row.suppressed_reason == reason


@pytest.mark.asyncio
async def test_one_open_and_same_episode(db):
    q, _ = manager(db)
    first, _ = q.ask(decision_id="d1", t=10, episode_id="e",
                     action=AskAction(text="First?"))
    _, reason = q.ask(decision_id="d2", t=11, episode_id="x",
                      action=AskAction(text="Second?"))
    assert reason == "one_open"
    first.status = "answered"
    db.update_question(first)
    _, reason = q.ask(decision_id="d3", t=100, episode_id="e",
                      action=AskAction(text="Again?"))
    assert reason == "same_episode"


@pytest.mark.asyncio
async def test_limiter_guards_and_speech_last(db):
    timings = replace(Timings.demo(), ask_min_gap=30, ask_max_per_hour=1,
                      speech_min_gap=20, ask_speech_gap=5)
    q, _ = manager(db, timings=timings)
    q.limiter.grant(10)
    _, reason = q.ask(decision_id="d", t=20, episode_id="e",
                      action=AskAction(text="Gap?"))
    assert reason == "ask_min_gap"
    q.limiter._granted = [0]
    _, reason = q.ask(decision_id="d", t=100, episode_id="x",
                      action=AskAction(text="Cap?"))
    assert reason == "ask_max_per_hour"
    q.limiter._granted.clear()
    q.speech.allow(100)
    _, reason = q.ask(decision_id="d", t=101, episode_id="y",
                      action=AskAction(text="Speech?"))
    assert reason == "speech_gap"
    # Asks bypass the 20 s speech gap; only the 5 s overlap gap applies.
    q.limiter._granted.clear()
    _, reason = q.ask(decision_id="d", t=106, episode_id="z",
                      action=AskAction(text="Now?"))
    assert reason is None
    assert q.speech.last_spoken_t == 106


@pytest.mark.asyncio
async def test_send_success_and_failure(db):
    q, sent = manager(db)
    row, reason = q.ask(decision_id="d", t=10, episode_id="e",
                        action=AskAction(text="Question?"))
    await asyncio.sleep(0)
    stored = db.get_question(row.id)
    assert reason is None and sent and stored.sent_t == 100
    assert stored.expires_t == 100 + q.timings.ask_expire_s

    stored.status = "answered"
    db.update_question(stored)
    q2, _ = manager(db, send_ok=False)
    failed, _ = q2.ask(decision_id="d2", t=100, episode_id="x",
                       action=AskAction(text="Fail?"))
    db.insert_decision(Decision(
        id="d2", t=100, trigger="test", trigger_tick_id="", actions=[{
            "type": "ask", "text": "Fail?", "question_id": failed.id,
            "outcome": "sent",
        }],
    ))
    await asyncio.sleep(0)
    failed = db.get_question(failed.id)
    assert failed.status == "suppressed" and failed.suppressed_reason == "send_failed"
    assert db.list_decisions()[0].actions[0]["outcome"] == "suppressed:send_failed"


@pytest.mark.asyncio
async def test_listening_includes_question_still_sending(db):
    q, _ = manager(db)
    row, _ = q.ask(decision_id="d", t=10, episode_id="e",
                   action=AskAction(text="Question?"))
    assert row.sent_t is None and q.listening()


@pytest.mark.asyncio
async def test_answer_claim_silence_parse_followup_and_expiry(db):
    db.upsert_episode(Episode(id="e", kind="alcohol_sighting", start_t=1))
    q, _ = manager(db)
    root, _ = q.ask(decision_id="d", t=10, episode_id="e",
                    action=AskAction(text="Is that yours?"))
    await asyncio.sleep(0)
    q.on_answer(root.id, "", False, 20)
    q.on_answer(root.id, "yes", True, 21)
    stored = db.get_question(root.id)
    assert stored.status == "expired"
    assert stored.parsed["note"] == "nothing heard"
    assert len(db.today_summary_lines(day_key(20))) == 1

    child_root, _ = q.ask(decision_id="d2", t=100, episode_id="x",
                          action=AskAction(text="Yours?"))
    q.apply_parse(child_root, AnswerParse(understood=True, confirmed=True,
                  note="yes", followup="How many?"), 101)
    children = [x for x in db.list_questions() if x.followup_of == child_root.id]
    assert len(children) == 1
    children[0].status = "answered"
    db.update_question(children[0])
    q.apply_parse(children[0], AnswerParse(understood=True, confirmed=True,
                  note="two", followup="Another?"), 102)
    assert len([x for x in db.list_questions() if x.followup_of]) == 1
    decision = next(d for d in db.list_decisions()
                    if d.trigger == f"answer:{child_root.id}")
    assert decision.actions[0]["line"].startswith("wearer:")
    assert decision.actions[1]["question_id"] == children[0].id
    assert decision.actions[1]["outcome"] == "sent"

    expiring, _ = q.ask(decision_id="d3", t=200, episode_id="z",
                        action=AskAction(text="Expire?"))
    await asyncio.sleep(0)
    expiring = db.get_question(expiring.id)
    assert q.expire(expiring.expires_t) == 1
    assert db.get_question(expiring.id).status == "expired"
    assert any("no answer" in line.line
               for line in db.today_summary_lines(day_key(expiring.expires_t)))


@pytest.mark.asyncio
async def test_followup_guards_skip_gaps_and_record_speech_grant(db):
    timings = replace(Timings.demo(), ask_min_gap=100, speech_min_gap=100)
    q, _ = manager(db, timings=timings)
    root, _ = q.ask(decision_id="d", t=10, episode_id="e",
                    action=AskAction(text="Yours?"))
    root.status = "answered"
    db.update_question(root)
    child, reason = q.ask(decision_id="a", t=11, episode_id="e",
                          action=AskAction(text="How many?"), followup_of=root)
    assert reason is None and child.status == "open"
    assert q.speech.last_spoken_t == 11 and q.speech.allowed == 2
    child.status = "answered"
    db.update_question(child)
    _, reason = q.ask(decision_id="b", t=12, episode_id="e",
                      action=AskAction(text="Again?"), followup_of=root)
    assert reason == "followup_denied"
    _, reason = q.ask(decision_id="c", t=13, episode_id="e",
                      action=AskAction(text="Grandchild?"), followup_of=child)
    assert reason == "followup_denied"


def test_expire_annotates_only_successful_transitions(db, monkeypatch):
    q, _ = manager(db)
    row, _ = q.ask(decision_id="d", t=10, episode_id="e",
                   action=AskAction(text="Race?"))
    row.expires_t = 20
    db.update_question(row)
    def lose_race(now):
        row.status = "answered"
        db.update_question(row)
        return 0
    monkeypatch.setattr(db, "expire_questions", lose_race)
    assert q.expire(20) == 0
    assert db.today_summary_lines(day_key(20)) == []


@pytest.mark.asyncio
async def test_expiry_timer_survives_clock_error_and_stop_swallows(db, monkeypatch):
    q, _ = manager(db)
    calls = 0
    def clock():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("clock")
        return 100
    q.now_fn = clock
    real_sleep = asyncio.sleep
    async def fast_sleep(_delay):
        await real_sleep(0)
    monkeypatch.setattr("pipeline.actions.questions.asyncio.sleep", fast_sleep)
    q.start()
    while calls < 2:
        await real_sleep(0)
    await q.stop()

    async def broken():
        raise ValueError("task")
    q._expiry_task = asyncio.create_task(broken())
    await real_sleep(0)
    await q.stop()


def test_capture_interface_mismatch_fails_at_construction(tmp_path, monkeypatch):
    class Link:
        pass
    class BadCapture:
        def __init__(self, *args, **kwargs):
            self.link = Link()
    monkeypatch.setattr("pipeline.capture.bridge.LongevityCapture", BadCapture)
    with pytest.raises(RuntimeError, match=(
        "capture bridge lacks the ask/answer interface: .*send_question.*supports.*on_answer"
    )):
        build_pipeline(Settings(db_path=tmp_path / "bad.db"), source="glasses",
                       reasoner_mode="fake", speed=1, seed_db=False, vlm="off")


@pytest.mark.asyncio
async def test_busy_answer_is_finalised_and_dropped(db):
    q, _ = manager(db)
    class Busy:
        def try_answer(self, question, transcript, t): return False
    q.reasoner = Busy()
    row, _ = q.ask(decision_id="d", t=10, episode_id="e",
                   action=AskAction(text="Yours?"))
    await asyncio.sleep(0)
    q.on_answer(row.id, "yes", True, 20)
    stored = db.get_question(row.id)
    assert stored.parsed["note"] == "reasoner busy"
    decision = db.list_decisions()[0]
    assert decision.dropped and decision.drop_reason == "t1_busy"


@pytest.mark.asyncio
async def test_handler_drops_speak_for_ask_and_while_listening(db):
    q, _ = manager(db)
    handler = ActionHandler(db, q.speech, q.timings, q)
    outcome = handler.apply("d", 10, T1Response(actions=[
        SpeakAction(text="Statement"), AskAction(text="Question?")]))
    assert not outcome["spoke"] and outcome["asks"][0]["outcome"] == "sent"
    await asyncio.sleep(0)
    db.get_question(outcome["asks"][0]["question_id"])
    outcome = handler.apply("d2", 11, T1Response(actions=[SpeakAction(text="Talk")]))
    assert not outcome["spoke"]


@pytest.mark.asyncio
async def test_sim_pipeline_ask_answer_end_to_end(tmp_path):
    settings = Settings(db_path=tmp_path / "e2e.db")
    settings.__dict__["timings"] = replace(settings.timings, ask_expire_s=10000)
    pipeline = build_pipeline(settings,
                              source="sim", reasoner_mode="fake", speed=200)
    await pipeline.start()
    try:
        for _ in range(2000):
            decisions = pipeline.db.list_decisions(limit=100)
            asked = next((d for d in decisions if any(
                a.get("type") == "ask" and a.get("outcome") == "sent"
                for a in d.actions)), None)
            if asked is not None:
                break
            await asyncio.sleep(.005)
        assert asked is not None
        action = next(a for a in asked.actions if a.get("outcome") == "sent")
        pipeline.questions.on_answer(action["question_id"], "yeah two", True,
                                     pipeline.clock.wall_to_tick(__import__("time").time()))
        for _ in range(100):
            if any(d.trigger == f"answer:{action['question_id']}"
                   for d in pipeline.db.list_decisions(limit=100)):
                break
            await asyncio.sleep(.01)
        assert any(d.trigger == f"answer:{action['question_id']}"
                   for d in pipeline.db.list_decisions(limit=100))
        assert pipeline.questions.stats()["answered"] >= 1
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_a_send_that_never_returns_is_finalised_send_failed(db):
    """§8.2: delivery gets `ask_expire_s` and no more.

    A transport that neither returns nor raises -- a socket accepting bytes into
    a dead TCP window, a synthesiser past its own deadline -- would otherwise
    leave the row `open` forever: `expires_t` is only written *after* a
    successful send, so `expire` never sees it, `one_open` blocks every later
    question and `listening()` mutes speech for the rest of the run.
    """
    timings = replace(Timings.demo(), speech_min_gap=0, ask_expire_s=0.02)
    started = asyncio.Event()

    async def hangs(_q):
        started.set()
        await asyncio.sleep(30)
        return True

    speech = SpeechLimiter(timings.speech_min_gap, timings.speech_max_per_hour)
    q = QuestionManager(db, speech, timings, send=hangs,
                        supports_ask=lambda: True, has_transport=lambda: True,
                        parser=FakeAnswerParser(), now_fn=lambda: 100.0)
    row, reason = q.ask(decision_id="d", t=10, episode_id="e",
                        action=AskAction(text="Anyone there?"))
    assert reason is None
    await started.wait()
    for _ in range(200):
        stored = db.get_question(row.id)
        if stored.status != "open":
            break
        await asyncio.sleep(0.005)

    stored = db.get_question(row.id)
    assert stored.status == "suppressed"
    assert stored.suppressed_reason == "send_failed"
    assert stored.expires_t is None, "a question that never went out has no deadline"
    assert not q.listening(), "the open row must not mute the rest of the run"
