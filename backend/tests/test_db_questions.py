"""The ``pending_questions`` table (docs/ASK_DESIGN.md §5, §8.4).

An answer and an expiry race each other by construction: the phone may deliver
a transcript in the same second the tick loop decides nobody replied. Both
transitions are single UPDATEs guarded by ``status = 'open'``, so exactly one
of them can win and the loser is a no-op. That is what most of this file is
about; the rest is plain CRUD and ordering.
"""

from __future__ import annotations

import pytest

from pipeline.db import Database
from pipeline.models import PendingQuestion

T0 = 1_757_700_000.0


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db").connect().init_schema()
    yield database
    database.close()


def make_question(
    qid: str = "q_00000001",
    *,
    created_t: float = T0,
    expires_t: float | None = T0 + 25.0,
    episode_id: str | None = "ep_1",
    status: str = "open",
    **extra,
) -> PendingQuestion:
    return PendingQuestion(
        id=qid,
        created_t=created_t,
        expires_t=expires_t,
        decision_id="d_1",
        episode_id=episode_id,
        question="That yours?",
        answer_kind="yes_no",
        fills="confirmed",
        status=status,
        **extra,
    )


# -- CRUD ----------------------------------------------------------------


def test_insert_and_read_back_every_field(db: Database) -> None:
    question = make_question(
        sent_t=T0 + 1.5,
        followup_of="q_00000000",
        suppressed_reason=None,
    )
    db.insert_question(question)

    stored = db.get_question(question.id)
    assert stored == question


def test_get_question_returns_none_for_an_unknown_id(db: Database) -> None:
    assert db.get_question("q_nope") is None


def test_update_question_replaces_the_whole_row(db: Database) -> None:
    """``parsed`` stays ``{}`` until the parser finishes and writes it back."""

    db.insert_question(make_question())
    assert db.get_question("q_00000001").parsed == {}

    db.claim_answer("q_00000001", "yeah two", True, T0 + 10.0)
    row = db.get_question("q_00000001")
    row.parsed = {"understood": True, "confirmed": True, "count": 2.0}
    db.update_question(row)

    stored = db.get_question("q_00000001")
    assert stored.parsed == {"understood": True, "confirmed": True, "count": 2.0}
    assert stored.status == "answered"
    assert stored.answer_text == "yeah two"


def test_heard_stays_tri_state(db: Database) -> None:
    """``None`` is "the window has not closed yet", not "heard nothing"."""

    db.insert_question(make_question())
    assert db.get_question("q_00000001").heard is None

    db.claim_answer("q_00000001", "", False, T0 + 10.0)
    assert db.get_question("q_00000001").heard is False


def test_the_table_is_indexed_for_the_expiry_sweep(db: Database) -> None:
    indexes = {
        row["name"]
        for row in db.conn.execute("PRAGMA index_list(pending_questions)")
    }
    assert "ix_questions_status" in indexes
    columns = [
        row["name"]
        for row in db.conn.execute("PRAGMA index_info(ix_questions_status)")
    ]
    assert columns == ["status", "expires_t"]


# -- the one open question -----------------------------------------------


def test_open_question_is_none_until_one_is_open(db: Database) -> None:
    assert db.open_question() is None
    db.insert_question(make_question())
    assert db.open_question().id == "q_00000001"

    db.claim_answer("q_00000001", "yes", True, T0 + 5.0)
    assert db.open_question() is None


def test_open_question_returns_the_newest(db: Database) -> None:
    db.insert_question(make_question("q_old", created_t=T0))
    db.insert_question(make_question("q_new", created_t=T0 + 60))
    assert db.open_question().id == "q_new"


# -- claiming an answer (§8.1) -------------------------------------------


def test_claim_answer_succeeds_exactly_once(db: Database) -> None:
    """A duplicate or late answer is ignored, not applied twice."""

    db.insert_question(make_question())

    assert db.claim_answer("q_00000001", "yeah two", True, T0 + 10.0) is True
    assert db.claim_answer("q_00000001", "no wait", True, T0 + 11.0) is False

    stored = db.get_question("q_00000001")
    assert stored.status == "answered"
    assert stored.answer_text == "yeah two"  # the first claim, not the second
    assert stored.answer_t == T0 + 10.0


def test_claim_answer_cannot_revive_an_expired_question(db: Database) -> None:
    db.insert_question(make_question())
    assert db.expire_questions(T0 + 30.0) == 1

    assert db.claim_answer("q_00000001", "sorry, yes", True, T0 + 31.0) is False
    stored = db.get_question("q_00000001")
    assert stored.status == "expired"
    assert stored.answer_text is None


def test_claim_answer_on_an_unknown_id_is_false(db: Database) -> None:
    assert db.claim_answer("q_nope", "yes", True, T0) is False


# -- expiry (§8.4) -------------------------------------------------------


def test_expire_questions_touches_only_overdue_open_rows(db: Database) -> None:
    db.insert_question(make_question("q_overdue", expires_t=T0 + 10.0))
    db.insert_question(make_question("q_later", expires_t=T0 + 100.0))
    db.insert_question(make_question("q_unsent", expires_t=None))
    db.insert_question(
        make_question("q_answered", expires_t=T0 + 10.0, status="answered")
    )
    db.insert_question(
        make_question("q_suppressed", expires_t=T0 + 10.0, status="suppressed")
    )

    assert db.expire_questions(T0 + 50.0) == 1

    statuses = {q.id: q.status for q in db.list_questions(limit=10)}
    assert statuses == {
        "q_overdue": "expired",
        "q_later": "open",
        "q_unsent": "open",  # never sent is not the same as ignored
        "q_answered": "answered",
        "q_suppressed": "suppressed",
    }


def test_expire_questions_is_idempotent(db: Database) -> None:
    db.insert_question(make_question(expires_t=T0 + 10.0))
    assert db.expire_questions(T0 + 50.0) == 1
    assert db.expire_questions(T0 + 50.0) == 0


def test_expire_questions_fires_exactly_on_the_deadline(db: Database) -> None:
    db.insert_question(make_question(expires_t=T0 + 25.0))
    assert db.expire_questions(T0 + 24.9) == 0
    assert db.expire_questions(T0 + 25.0) == 1


# -- listing -------------------------------------------------------------


def test_questions_for_episode_is_newest_first(db: Database) -> None:
    db.insert_question(make_question("q_root", created_t=T0, episode_id="ep_1"))
    db.insert_question(
        make_question("q_child", created_t=T0 + 30, episode_id="ep_1",
                      followup_of="q_root")
    )
    db.insert_question(make_question("q_other", created_t=T0 + 10, episode_id="ep_2"))

    assert [q.id for q in db.questions_for_episode("ep_1")] == ["q_child", "q_root"]
    assert [q.id for q in db.questions_for_episode("ep_2")] == ["q_other"]
    assert db.questions_for_episode("ep_missing") == []


def test_questions_for_episode_breaks_a_timestamp_tie_by_insertion(db: Database) -> None:
    """A root and its child can share a clock reading; order must still hold."""

    db.insert_question(make_question("q_root", created_t=T0))
    db.insert_question(make_question("q_child", created_t=T0, followup_of="q_root"))
    assert [q.id for q in db.questions_for_episode("ep_1")] == ["q_child", "q_root"]


def test_list_questions_is_newest_first_and_honours_the_limit(db: Database) -> None:
    for i in range(5):
        db.insert_question(make_question(f"q_{i}", created_t=T0 + i))

    assert [q.id for q in db.list_questions()] == ["q_4", "q_3", "q_2", "q_1", "q_0"]
    assert [q.id for q in db.list_questions(limit=2)] == ["q_4", "q_3"]


def test_stats_still_reports_the_same_counters(db: Database) -> None:
    """The questions table is its own thing; ``stats()`` did not change."""

    db.insert_question(make_question())
    assert set(db.stats()) == {
        "tick_count",
        "ai_tick_count",
        "decision_count",
        "dropped_count",
        "biometric_count",
    }
