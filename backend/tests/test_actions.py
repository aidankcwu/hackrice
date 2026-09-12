"""Action handling (SPEC §4.4): each action type lands in exactly one table."""

from __future__ import annotations

import pytest

from pipeline.actions.handlers import ActionHandler
from pipeline.actions.speech import SpeechLimiter, clear_spoken, spoken
from pipeline.config import Timings
from pipeline.db import Database, day_key
from pipeline.reasoner.schema import (
    AnnotateAction,
    LogInsightAction,
    NothingAction,
    SpeakAction,
    T1Response,
    WatchAction,
)

T0 = 1_757_700_000.0
DECISION = "d_0001"


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def timings():
    return Timings.demo()


@pytest.fixture
def handler(db, timings):
    return ActionHandler(db, SpeechLimiter(min_gap_s=0, max_per_hour=10), timings)


@pytest.fixture(autouse=True)
def _clean_speech():
    clear_spoken()
    yield
    clear_spoken()


def response(*actions) -> T1Response:
    return T1Response(interpretation="…", confidence=0.5, actions=list(actions))


# -- one table per action -------------------------------------------------


def test_annotate_appends_to_todays_summary(handler, db):
    result = handler.apply(
        DECISION, T0, response(AnnotateAction(line="12:31 lunch, mixed plate"))
    )

    lines = db.today_summary_lines(day=day_key(T0))
    assert [l.line for l in lines] == ["12:31 lunch, mixed plate"]
    assert lines[0].t == T0
    assert lines[0].decision_id == DECISION
    assert result["annotated"] is True
    assert db.list_insights() == []


def test_log_insight_writes_an_insight_row(handler, db):
    result = handler.apply(
        DECISION,
        T0,
        response(LogInsightAction(category="diet", text="Mediterranean-ish plate.")),
    )

    (insight,) = db.list_insights()
    assert insight.category == "diet"
    assert insight.text == "Mediterranean-ish plate."
    assert insight.t == T0
    assert insight.decision_id == DECISION
    assert insight.id.startswith("i_")
    assert result["insights"] == 1


def test_watch_writes_a_pending_check_due_at_simulated_time(handler, db):
    result = handler.apply(
        DECISION,
        T0,
        response(WatchAction(after_s=900, reason="check if still seated")),
    )

    (check,) = db.due_pending_checks(now=T0 + 900)
    assert check.due_t == T0 + 900
    assert check.created_t == T0
    assert check.reason == "check if still seated"
    assert check.decision_id == DECISION
    assert check.id.startswith("w_")
    assert result["watches"] == 1
    assert db.due_pending_checks(now=T0 + 899) == [], "not due yet"


def test_watch_with_only_a_condition_has_no_due_time(handler, db):
    handler.apply(
        DECISION,
        T0,
        response(WatchAction(condition="food still in frame", reason="meal end?")),
    )

    (check,) = db.due_pending_checks(now=T0)
    assert check.due_t is None
    assert check.condition == "food still in frame"


def test_an_empty_watch_falls_back_to_the_default_delay(handler, db, timings):
    handler.apply(DECISION, T0, response(WatchAction(reason="vague")))

    (check,) = db.due_pending_checks(now=T0 + timings.watch_default_after_s)
    assert check.due_t == T0 + timings.watch_default_after_s


def test_speak_passes_through_the_limiter(handler, db):
    result = handler.apply(
        DECISION, T0, response(SpeakAction(text="Time to stand up.", urgency="normal"))
    )

    assert result["spoke"] is True
    assert spoken == [(T0, "Time to stand up.", "normal")]


def test_a_suppressed_speak_writes_nothing_and_reports_false(db, timings):
    limiter = SpeechLimiter(min_gap_s=600, max_per_hour=10)
    limiter.allow(T0)  # something already spoke a moment ago
    handler = ActionHandler(db, limiter, timings)

    result = handler.apply(
        DECISION, T0 + 5, response(SpeakAction(text="nope", urgency="low"))
    )

    assert result["spoke"] is False
    assert spoken == []


def test_nothing_is_a_no_op(handler, db):
    result = handler.apply(DECISION, T0, response(NothingAction()))

    assert result == {"spoke": False, "insights": 0, "watches": 0, "annotated": False}
    assert db.list_insights() == []
    assert db.today_summary_lines(day=day_key(T0)) == []
    assert db.due_pending_checks(now=T0 + 10_000) == []


# -- actions are not mutually exclusive -----------------------------------


def test_all_four_effects_apply_in_one_response(handler, db, timings):
    result = handler.apply(
        DECISION,
        T0,
        response(
            AnnotateAction(line="16:40 coffee, late"),
            LogInsightAction(category="caffeine", text="Past the cutoff."),
            WatchAction(after_s=60, reason="check for more caffeine"),
            SpeakAction(text="Coffee this late may cost you sleep.", urgency="low"),
        ),
    )

    assert result == {"spoke": True, "insights": 1, "watches": 1, "annotated": True}
    assert len(db.today_summary_lines(day=day_key(T0))) == 1
    assert len(db.list_insights()) == 1
    assert len(db.due_pending_checks(now=T0 + 60)) == 1
    assert len(spoken) == 1


def test_two_insights_in_one_response_get_distinct_ids(handler, db):
    handler.apply(
        DECISION,
        T0,
        response(
            LogInsightAction(category="diet", text="one"),
            LogInsightAction(category="social", text="two"),
        ),
    )

    insights = db.list_insights()
    assert len(insights) == 2
    assert len({i.id for i in insights}) == 2


def test_one_failing_action_does_not_lose_the_others(handler, db, monkeypatch):
    def boom(_insight):
        raise RuntimeError("insights table is on fire")

    monkeypatch.setattr(handler.db, "insert_insight", boom)

    result = handler.apply(
        DECISION,
        T0,
        response(
            LogInsightAction(category="diet", text="doomed"),
            AnnotateAction(line="survives"),
        ),
    )

    assert result["insights"] == 0
    assert result["annotated"] is True
    assert [l.line for l in db.today_summary_lines(day=day_key(T0))] == ["survives"]
