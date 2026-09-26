"""The two session blocks (session_context): what was said, what is furniture."""

from __future__ import annotations

import json
import time

import pytest

from pipeline.db import Database
from pipeline.models import Session
from pipeline.reasoner.session_context import (
    CONSTANT_MIN_TICKS, constant_block, said_block, said_this_session, session_start,
)
from tests.conftest import make_tick

T0 = 1_790_000_000.0


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "p.db").connect().init_schema()
    yield database
    database.close()


def _conversation(db: Database, opened_t: float, text: str, kind: str = "statement") -> None:
    db._write_conversation({
        "id": f"c_{int(opened_t)}", "opened_t": opened_t, "closed_t": opened_t + 2,
        "reason": "", "topic": text, "decision_id": None, "episode_id": None,
        "state": "closed", "close_reason": "done", "settled": {},
        "turns": [{"t": opened_t + 1, "role": "agent", "text": text, "kind": kind}],
    })


def test_no_session_means_no_memory_and_no_furniture(db):
    _conversation(db, T0 + 10, "Creatine, huh?")
    assert session_start(db, T0 + 100) is None
    assert said_this_session(db, T0 + 100) == []
    assert "none so far" in said_block(db, T0 + 100)
    assert constant_block(db, T0 + 100) is None


def test_said_block_lists_only_lines_since_the_session_began(db):
    _conversation(db, T0 - 300, "Before the session.")
    db.insert_session(Session(id="s1", started_t=T0))
    _conversation(db, T0 + 30, "Creatine, huh?", kind="question")
    _conversation(db, T0 + 90, "Second coffee already.")

    said = said_this_session(db, T0 + 120)
    assert [s[2] for s in said] == ["Creatine, huh?", "Second coffee already."]
    block = said_block(db, T0 + 120)
    assert "Before the session" not in block
    assert 'asked: "Creatine, huh?"' in block
    assert 'said: "Second coffee already."' in block
    assert "not said again" in block


def _tagged_ticks(db: Database, n: int, objects: list[str], screen: bool = True) -> None:
    for i in range(n):
        tick = make_tick(seq=i, t=T0 + 1.5 * i, with_ai=True)
        tick.ai.objects = list(objects)
        tick.ai.screen_present = screen
        db.insert_tick(tick)


def test_constant_block_needs_enough_tagged_ticks(db):
    db.insert_session(Session(id="s1", started_t=T0))
    _tagged_ticks(db, CONSTANT_MIN_TICKS - 1, ["laptop", "water bottle"])
    assert constant_block(db, T0 + 60) is None


def test_constant_block_names_the_furniture_and_not_the_visitor(db):
    db.insert_session(Session(id="s1", started_t=T0))
    _tagged_ticks(db, 40, ["laptop", "water bottle"])
    # A tub that appears on two ticks is an event, not furniture.
    for i in (40, 41):
        tick = make_tick(seq=i, t=T0 + 1.5 * i, with_ai=True)
        tick.ai.objects = ["creatine tub", "laptop"]
        db.insert_tick(tick)

    block = constant_block(db, T0 + 70)
    assert block is not None
    assert "laptop" in block and "water bottle" in block
    assert "creatine" not in block
    assert "a screen in view" in block
    assert "furniture" in block and "not a reason to speak" in block
