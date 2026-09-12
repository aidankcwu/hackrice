from __future__ import annotations

import time

import pytest
from conftest import make_tick

from pipeline.db import Database, day_key
from pipeline.models import (
    Decision,
    Episode,
    Insight,
    PendingCheck,
    Score,
    SeededRow,
    TodaySummaryLine,
)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db").connect().init_schema()
    yield database
    database.close()


def test_wal_mode_enabled(db: Database) -> None:
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_insert_and_read_ticks(db: Database) -> None:
    base = 1_757_700_000.0
    for i in range(5):
        db.insert_tick(make_tick(i, t=base + i, with_ai=(i % 2 == 0)))

    recent = db.recent_ticks(3)
    assert [t.seq for t in recent] == [2, 3, 4]
    assert recent[0].ai is not None
    assert recent[1].ai is None
    assert recent[0].sensor.phash == f"{2:016x}"
    assert recent[0].device is not None

    window = db.ticks_between(base + 1, base + 3)
    assert [t.seq for t in window] == [1, 2, 3]


def test_ticks_table_stores_no_pixels(db: Database) -> None:
    """SPEC §2.5 rule 3."""

    db.insert_tick(make_tick(1))
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(ticks)")}
    assert cols == {
        "tick_id",
        "t",
        "seq",
        "frame_ref",
        "has_ai",
        "v",
        "sensor",
        "device",
        "ai",
    }
    for row in db.conn.execute("SELECT * FROM ticks"):
        for value in row:
            assert not isinstance(value, (bytes, bytearray))


def test_tick_reinsert_is_idempotent(db: Database) -> None:
    tick = make_tick(3)
    db.insert_tick(tick)
    db.insert_tick(tick)
    assert db.stats()["tick_count"] == 1


def test_episodes(db: Database) -> None:
    now = time.time()
    ep = Episode(
        id="ep_1",
        kind="meal",
        start_t=now,
        end_t=None,
        duration_s=0.0,
        dominant={"food_type": "mixed", "scene": "restaurant"},
        tick_count=1,
        open=True,
    )
    db.upsert_episode(ep)

    stored = db.list_episodes()
    assert len(stored) == 1
    assert stored[0].dominant["food_type"] == "mixed"
    assert stored[0].open is True

    closed = ep.model_copy(
        update={"end_t": now + 600, "duration_s": 600.0, "open": False, "tick_count": 600}
    )
    db.upsert_episode(closed)
    stored = db.list_episodes(day=day_key(now))
    assert len(stored) == 1
    assert stored[0].open is False
    assert stored[0].duration_s == 600.0
    assert db.list_episodes(day="1999-01-01") == []


def test_decisions_including_silent_and_dropped(db: Database) -> None:
    now = time.time()
    db.insert_decision(
        Decision(
            id="d_1",
            t=now,
            trigger="food_in_frame",
            trigger_tick_id="t_00000001",
            interpretation="salad, healthy",
            confidence=0.8,
            actions=[{"type": "annotate", "text": "salad at 12:31"}],
            spoke=False,
            latency_ms=920,
            model="gpt-5.4-mini",
        )
    )
    db.insert_decision(
        Decision(
            id="d_2",
            t=now + 1,
            trigger="screen_sustained",
            trigger_tick_id="t_00000002",
            dropped=True,
            drop_reason="t1_busy",
        )
    )

    rows = db.list_decisions()
    assert [d.id for d in rows] == ["d_2", "d_1"]
    assert rows[1].actions[0]["type"] == "annotate"
    assert rows[0].dropped is True and rows[0].drop_reason == "t1_busy"

    stats = db.stats()
    assert stats["decision_count"] == 2
    assert stats["dropped_count"] == 1


def test_insights(db: Database) -> None:
    db.insert_insight(
        Insight(id="i_1", t=1.0, category="diet", text="third sweet today", decision_id="d_1")
    )
    rows = db.list_insights()
    assert len(rows) == 1 and rows[0].category == "diet"


def test_pending_checks(db: Database) -> None:
    db.insert_pending_check(
        PendingCheck(id="p_1", created_t=100.0, due_t=160.0, reason="recheck screen")
    )
    db.insert_pending_check(
        PendingCheck(id="p_2", created_t=100.0, due_t=None, condition="outdoor", reason="c")
    )

    assert [c.id for c in db.due_pending_checks(120.0)] == ["p_2"]
    assert {c.id for c in db.due_pending_checks(200.0)} == {"p_1", "p_2"}

    db.mark_pending_fired("p_1")
    assert [c.id for c in db.due_pending_checks(200.0)] == ["p_2"]


def test_scores_upsert(db: Database) -> None:
    s = Score(
        metric="nature_minutes",
        layer="Nature",
        period="weekly",
        period_key="2026-W37",
        value=95.0,
        target=">=120 min/wk",
        score=0.79,
        source="live",
        grade="B",
    )
    db.upsert_score(s)
    db.upsert_score(s.model_copy(update={"value": 130.0, "score": 1.0}))

    rows = db.list_scores("weekly")
    assert len(rows) == 1
    assert rows[0].value == 130.0 and rows[0].score == 1.0
    assert db.list_scores("daily") == []


def test_seeded_rows(db: Database) -> None:
    n = db.insert_seeded_rows(
        [
            SeededRow(day="2026-09-10", metric="sleep_hours", value=7.2, unit="h"),
            SeededRow(day="2026-09-11", metric="sleep_hours", value=6.1, unit="h"),
            SeededRow(day="2026-09-12", metric="sleep_hours", value=5.4, unit="h"),
        ]
    )
    assert n == 3
    rows = db.list_seeded("2026-09-11", "2026-09-12")
    assert [r.value for r in rows] == [6.1, 5.4]
    assert db.insert_seeded_rows([]) == 0


def test_today_summary(db: Database) -> None:
    now = time.time()
    db.append_summary_line(TodaySummaryLine(t=now, line="coffee at desk", decision_id="d_1"))
    db.append_summary_line(TodaySummaryLine(t=now + 1, line="salad for lunch"))

    lines = db.today_summary_lines()
    assert [line.line for line in lines] == ["coffee at desk", "salad for lunch"]
    assert db.today_summary_lines("1999-01-01") == []


def test_biometric_series_range_query(db: Database) -> None:
    assert db.insert_biometric_series([
        (102.0, "heart_rate", 62.0, "apple_watch"),
        (100.0, "heart_rate", 58.0, "apple_watch"),
        (101.0, "skin_temp", 0.1, "oura"),
    ]) == 3
    assert db.biometric_series("heart_rate", 99.0, 101.0) == [(100.0, 58.0)]
    assert db.stats()["biometric_count"] == 3


def test_stats_counts_ai_ticks(db: Database) -> None:
    for i in range(6):
        db.insert_tick(make_tick(i, with_ai=(i < 4)))
    stats = db.stats()
    assert stats == {
        "tick_count": 6,
        "ai_tick_count": 4,
        "decision_count": 0,
        "dropped_count": 0,
        "biometric_count": 0,
    }


def test_connect_creates_parent_directory(tmp_path) -> None:
    path = tmp_path / "nested" / "deeper" / "pipeline.db"
    with Database(path) as database:
        assert path.exists()
        assert database.stats()["tick_count"] == 0


def test_use_before_connect_raises() -> None:
    with pytest.raises(RuntimeError):
        Database().stats()
