import asyncio
from dataclasses import replace

import pytest

from pipeline.actions.questions import QuestionManager
from pipeline.actions.speech import SpeechLimiter
from pipeline.config import Settings
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.models import PendingQuestion
from pipeline.reasoner.client import FakeReasonerClient
from pipeline.reasoner.reasoner import Reasoner


class SlowParser:
    model = "slow"
    async def parse(self, question, transcript, episode):
        await asyncio.sleep(1)


class ExplodingDatabase(Database):
    def list_episodes(self, limit=1000):
        raise ValueError("database")


@pytest.mark.asyncio
async def test_answer_timeout_finalises_and_releases_slot(tmp_path):
    settings = Settings(db_path=tmp_path / "q.db", demo_mode=True)
    db = Database(settings.db_path).connect().init_schema()
    speech = SpeechLimiter(0, 100)
    async def send(q): return True
    questions = QuestionManager(db, speech, settings.timings, send=send,
        supports_ask=lambda: True, has_transport=lambda: True, parser=SlowParser())
    reasoner = Reasoner(db, InMemoryFrameStore(), FakeReasonerClient(), speech,
                        settings, t1_deadline_s=.01, parser=questions.parser,
                        questions=questions)
    questions.reasoner = reasoner
    row = PendingQuestion(id="q_deadbeef", created_t=1, question="Question?",
                          status="answered", answer_text="yes", answer_t=2)
    db.insert_question(row)
    assert reasoner.try_answer(row, "yes", 2)
    await asyncio.sleep(.03)
    assert not reasoner.busy
    stored = db.get_question(row.id)
    assert stored.parsed["note"] == "parse failed: TimeoutError"
    assert db.list_decisions()[0].drop_reason == "t1_timeout"
    assert reasoner.stats()["answers_dropped"] == 1
    db.close()


@pytest.mark.asyncio
async def test_answer_setup_failure_finalises_and_releases_slot(tmp_path):
    settings = Settings(db_path=tmp_path / "setup.db", demo_mode=True)
    db = ExplodingDatabase(settings.db_path).connect().init_schema()
    speech = SpeechLimiter(0, 100)
    async def send(q): return True
    questions = QuestionManager(db, speech, settings.timings, send=send,
        supports_ask=lambda: True, has_transport=lambda: True, parser=SlowParser())
    reasoner = Reasoner(db, InMemoryFrameStore(), FakeReasonerClient(), speech,
                        settings, parser=questions.parser, questions=questions)
    questions.reasoner = reasoner
    row = PendingQuestion(id="q_setupbad", created_t=1, question="Question?",
                          status="answered", answer_text="yes", answer_t=2)
    db.insert_question(row)
    assert reasoner.try_answer(row, "yes", 2)
    await asyncio.sleep(0)
    assert not reasoner.busy
    assert db.get_question(row.id).parsed["note"] == "parse failed: ValueError"
    assert db.list_decisions()[0].drop_reason == "t1_error:ValueError"
    db.close()


@pytest.mark.asyncio
async def test_answer_cancelled_before_start_releases_slot(tmp_path):
    settings = Settings(db_path=tmp_path / "cancel.db", demo_mode=True)
    db = Database(settings.db_path).connect().init_schema()
    speech = SpeechLimiter(0, 100)
    reasoner = Reasoner(db, InMemoryFrameStore(), FakeReasonerClient(), speech,
                        settings)
    loop = asyncio.get_running_loop()
    original = loop.create_task
    created = []
    def cancel_immediately(coro, *args, **kwargs):
        task = original(coro, *args, **kwargs)
        task.cancel()
        created.append(task)
        return task
    loop.create_task = cancel_immediately
    try:
        row = PendingQuestion(id="q_cancel00", created_t=1,
                              question="Question?", status="answered")
        assert reasoner.try_answer(row, "yes", 2)
    finally:
        loop.create_task = original
    await asyncio.gather(*created, return_exceptions=True)
    await asyncio.sleep(0)
    assert not reasoner.busy
    assert reasoner._slot.acquire(blocking=False)
    reasoner._slot.release()
    db.close()


@pytest.mark.asyncio
async def test_an_answer_waits_for_a_busy_slot_instead_of_dropping(tmp_path):
    """Seen live: "just the water" finalised as "reasoner busy" because a wake-up
    was in flight. The parse must wait for the slot, not be dropped."""
    settings = Settings(db_path=tmp_path / "wait.db", demo_mode=True)
    db = Database(settings.db_path).connect().init_schema()
    speech = SpeechLimiter(0, 100)
    async def send(q): return True
    questions = QuestionManager(db, speech, settings.timings, send=send,
        supports_ask=lambda: True, has_transport=lambda: True, parser=SlowParser())
    reasoner = Reasoner(db, InMemoryFrameStore(), FakeReasonerClient(), speech,
                        settings, t1_deadline_s=.01, parser=questions.parser,
                        questions=questions)
    questions.reasoner = reasoner
    row = PendingQuestion(id="q_waiting1", created_t=1, question="Question?",
                          status="answered", answer_text="yes", answer_t=2)
    db.insert_question(row)
    assert reasoner._slot.acquire(blocking=False)  # a wake-up holds T1
    assert reasoner.try_answer(row, "yes", 2)       # accepted, not dropped
    await asyncio.sleep(.4)
    assert db.get_question(row.id).parsed == {}      # still waiting, nothing finalised
    reasoner._slot.release()                         # the wake-up finishes
    await asyncio.sleep(.6)
    stored = db.get_question(row.id)
    assert stored.parsed["note"] == "parse failed: TimeoutError"  # the parse ran
    assert not reasoner.busy
    db.close()
