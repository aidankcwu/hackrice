"""The session recap (moments, session-window scores, narrative, speech).

The end-to-end half follows ``test_api.py``: a sim-source pipeline at
``speed=200`` reaches 210 ticks in a couple of seconds, and the default
scenario escalates on food, caffeine, screen, outdoors and people inside that
span. Everything else is unit-level over hand-built rows.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx
import pytest

from pipeline.actions import speech
from pipeline.api.app import create_app
from pipeline.api.wiring import build_pipeline
from pipeline.capture.speak import make_speak_fn
from pipeline.config import Settings
from pipeline.db import Database, day_key
from pipeline.models import Decision, Episode, Insight, Score
from pipeline.recap import narrative as narrative_module
from pipeline.recap.builder import MAX_WINDOW_S, clamp_window, label_for, unit_for
from pipeline.recap.moments import (
    MAX_MOMENTS,
    MIN_SHARPNESS,
    Moment,
    blur_threshold,
    category_for,
    select_moments,
    severity_for,
)
from pipeline.recap.narrative import (
    FakeNarrativeClient,
    HEADLINE_MAX_WORDS,
    OpenAINarrativeClient,
    PARAGRAPH_MAX_WORDS,
    RECAP_TIMEOUT_S,
    RecapContext,
    RecapNarrative,
    SPOKEN_MAX_WORDS,
    SUGGESTION_MAX_WORDS,
    clamp_narrative,
    make_narrative_client,
)
from pipeline.scoring.scorer import WAKING_HOURS, Scorer

RECAP_KEYS = {"id", "session", "score", "moments", "narrative", "spoken",
              "generated_at", "model"}
SESSION_KEYS = {"id", "name", "from_t", "to_t", "duration_s", "tick_count",
                "ai_coverage", "decision_count"}
SUBSCORE_KEYS = {"metric", "layer", "label", "value", "unit", "score", "target",
                 "source", "grade", "note"}


# -- fixtures -------------------------------------------------------------


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


async def running_pipeline(tmp_path, name: str, ticks: int = 210):
    """A started sim pipeline that has produced at least ``ticks`` ticks."""

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / f"{name}.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await pipeline.start()
    for _ in range(2_000):
        if pipeline.db.stats()["tick_count"] >= ticks:
            break
        await asyncio.sleep(0.01)
    else:
        produced = pipeline.db.stats()["tick_count"]
        await pipeline.stop()
        raise AssertionError(
            f"sim pipeline {name!r} produced {produced} ticks in 20 s, wanted "
            f"{ticks}: the recap tests below would have run against a window "
            "with nothing in it."
        )
    return pipeline


def client_for(pipeline) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(pipeline)),
        base_url="http://test",
    )


# -- category and severity mapping ---------------------------------------


def test_category_maps_every_trigger_the_gate_can_fire():
    assert category_for("food_in_frame") == "diet"
    assert category_for("caffeine_seen") == "caffeine"
    assert category_for("alcohol_seen") == "alcohol"
    assert category_for("screen_sustained") == "screen"
    assert category_for("people_sustained") == "social"
    assert category_for("outdoor_sustained") == "nature"
    assert category_for("biometric_anomaly") == "stress"
    assert category_for("watch:food_in_frame") == "followup"
    assert category_for("watch:anything_at_all") == "followup"
    assert category_for("rice_krispy") == "other"
    assert category_for("") == "other"


def test_severity_reads_alcohol_and_stress_as_flags_whatever_the_hour():
    noon = _at_hour(12)
    assert severity_for("alcohol", noon) == "flag"
    assert severity_for("stress", noon) == "flag"


def test_severity_flags_caffeine_only_after_the_cutoff():
    assert severity_for("caffeine", _at_hour(9)) == "neutral"
    assert severity_for("caffeine", _at_hour(13, minute=59)) == "neutral"
    assert severity_for("caffeine", _at_hour(14)) == "flag"
    assert severity_for("caffeine", _at_hour(21)) == "flag"


def test_severity_splits_food_on_the_models_families():
    noon = _at_hour(12)
    assert severity_for("diet", noon, "salad") == "good"
    assert severity_for("diet", noon, "rice_bowl") == "good"
    assert severity_for("diet", noon, "chips") == "flag"
    assert severity_for("diet", noon, "fast_food") == "flag"
    # Neither family: the VLM saw a sandwich, which is not a verdict.
    assert severity_for("diet", noon, "sandwich") == "neutral"
    assert severity_for("diet", noon, None) == "neutral"


def test_severity_credits_nature_and_social_and_stays_neutral_elsewhere():
    noon = _at_hour(12)
    assert severity_for("nature", noon) == "good"
    assert severity_for("social", noon) == "good"
    assert severity_for("screen", noon) == "neutral"
    assert severity_for("followup", noon) == "neutral"
    assert severity_for("other", noon) == "neutral"


def _at_hour(hour: int, minute: int = 0) -> float:
    """A unix timestamp at ``hour`` local time today."""

    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0,
                        now.tm_wday, now.tm_yday, -1))


# -- blur -----------------------------------------------------------------


def test_blur_threshold_is_relative_but_never_below_the_floor():
    assert blur_threshold([]) == MIN_SHARPNESS
    # A crisp window: the relative rule binds.
    assert blur_threshold([400.0, 500.0, 600.0]) == pytest.approx(200.0)
    # A dim one: the absolute floor binds instead.
    assert blur_threshold([10.0, 12.0, 14.0]) == MIN_SHARPNESS


# -- moments over hand-built rows ----------------------------------------


class _Evidence:
    """Stand-in for EvidenceStore.list -- refs per decision, no bytes."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping

    def list(self, decision_id: str) -> list[dict]:
        return [{"decision_id": decision_id, "frame_ref": ref, "t": float(i),
                 "bytes": 100}
                for i, ref in enumerate(self.mapping.get(decision_id, []))]


def _tick(db, seq: int, t: float, sharpness: float) -> None:
    from tests.conftest import make_tick

    tick = make_tick(seq=seq, t=t)
    tick.sensor.sharpness = sharpness
    db.insert_tick(tick)


def test_select_moments_picks_the_sharpest_frame_and_skips_the_evidence_less(db):
    t0 = _at_hour(12)
    for i, sharpness in enumerate((90.0, 20.0, 95.0)):
        _tick(db, i, t0 + i, sharpness)
    db.insert_decision(Decision(
        id="d_1", t=t0 + 1, trigger="food_in_frame", trigger_tick_id="t_00000000",
        interpretation="meal, salad"))
    db.insert_decision(Decision(
        id="d_2", t=t0 + 2, trigger="screen_sustained", trigger_tick_id="t_00000001"))
    db.insert_insight(Insight(id="i_1", t=t0 + 1, category="diet",
                              text="Meal logged; tag against the pattern.",
                              decision_id="d_1"))
    evidence = _Evidence({"d_1": ["f_00000000", "f_00000001", "f_00000002"]})

    moments = select_moments(db, evidence, t0 - 1, t0 + 10)

    assert [m.decision_id for m in moments] == ["d_1"]  # d_2 saved no frames
    moment = moments[0]
    assert moment.frame_ref == "f_00000002" and moment.sharpness == 95.0
    assert moment.blurry is False
    assert moment.frame_url == "/api/evidence/d_1/f_00000002"
    assert moment.category == "diet" and moment.severity == "good"
    assert moment.caption == "meal, salad"
    assert moment.insight == "Meal logged; tag against the pattern."


def test_select_moments_flags_a_blurry_best_frame_and_drops_dropped_decisions(db):
    t0 = _at_hour(12)
    _tick(db, 0, t0, 400.0)      # a crisp tick sets the window median high
    _tick(db, 1, t0 + 1, 400.0)
    _tick(db, 2, t0 + 2, 30.0)   # the only frame this decision has
    db.insert_decision(Decision(
        id="d_1", t=t0 + 2, trigger="people_sustained", trigger_tick_id="t_2"))
    db.insert_decision(Decision(
        id="d_2", t=t0 + 2, trigger="food_in_frame", trigger_tick_id="t_2",
        dropped=True, drop_reason="t1_busy"))
    evidence = _Evidence({"d_1": ["f_00000002"], "d_2": ["f_00000000"]})

    moments = select_moments(db, evidence, t0 - 1, t0 + 10)

    assert [m.decision_id for m in moments] == ["d_1"]
    assert moments[0].blurry is True and moments[0].severity == "good"


def test_select_moments_reads_the_food_type_off_the_episode(db):
    t0 = _at_hour(12)
    _tick(db, 0, t0, 90.0)
    db.upsert_episode(Episode(id="e_1", kind="meal", start_t=t0, end_t=t0 + 60,
                              duration_s=60.0, dominant={"food_type": "chips"}))
    db.insert_decision(Decision(
        id="d_1", t=t0, trigger="food_in_frame", trigger_tick_id="t_0",
        episode_id="e_1", interpretation="meal"))

    moments = select_moments(db, _Evidence({"d_1": ["f_00000000"]}), t0 - 1, t0 + 10)
    assert moments[0].severity == "flag"


def test_select_moments_falls_back_to_a_humanised_trigger_for_the_caption(db):
    t0 = _at_hour(12)
    _tick(db, 0, t0, 90.0)
    db.insert_decision(Decision(id="d_1", t=t0, trigger="outdoor_sustained",
                                trigger_tick_id="t_0", interpretation="  "))
    moments = select_moments(db, _Evidence({"d_1": ["f_00000000"]}), t0 - 1, t0 + 10)
    assert moments[0].caption == "Outdoor sustained"
    assert moments[0].insight is None


# -- score_window ---------------------------------------------------------


def by_metric(rows: list[Score]) -> dict[str, Score]:
    return {row.metric: row for row in rows}


def test_score_window_extrapolates_a_rate_to_a_waking_day(db):
    t0 = _at_hour(12)
    t1 = t0 + 120.0  # a two-minute session
    # One minute of screen inside the window: half the session.
    db.upsert_episode(Episode(id="e_1", kind="screen_block", start_t=t0 + 30,
                              end_t=t0 + 90, duration_s=60.0, open=False))

    rows = Scorer(db).score_window(t0, t1)
    screen = by_metric(rows)["screen_hours_daily"]

    assert screen.period == "session"
    assert screen.period_key == f"{int(t0)}-{int(t1)}"
    assert screen.value == pytest.approx(0.5 * WAKING_HOURS)  # 8 h/day
    assert "at this rate" in screen.note and "2-minute session" in screen.note


def test_score_window_clips_an_episode_that_straddles_the_window(db):
    t0 = _at_hour(12)
    t1 = t0 + 120.0
    # Starts an hour early and is still open: only the 120 s inside count.
    db.upsert_episode(Episode(id="e_1", kind="screen_block", start_t=t0 - 3600,
                              end_t=None, duration_s=3600.0, open=True))

    screen = by_metric(Scorer(db).score_window(t0, t1))["screen_hours_daily"]
    assert screen.value == pytest.approx(WAKING_HOURS)  # 100% of the window

    # ...and an episode entirely before the window contributes nothing.
    db.upsert_episode(Episode(id="e_2", kind="screen_block", start_t=t0 - 7200,
                              end_t=t0 - 3600, duration_s=3600.0, open=False))
    again = by_metric(Scorer(db).score_window(t0, t1))["screen_hours_daily"]
    assert again.value == pytest.approx(WAKING_HOURS)


def test_score_window_counts_are_raw_not_extrapolated(db):
    t0 = _at_hour(12)
    t1 = t0 + 120.0
    for i, kind in enumerate(("conversation", "conversation", "meal",
                              "alcohol_sighting")):
        db.upsert_episode(Episode(id=f"e_{i}", kind=kind, start_t=t0 + i,
                                  end_t=t0 + i + 5, duration_s=5.0, open=False,
                                  dominant={"food_type": "salad"} if kind == "meal"
                                  else {}))

    rows = by_metric(Scorer(db).score_window(t0, t1))
    assert rows["social_episodes_daily"].value == 2.0
    assert rows["meals_logged_daily"].value == 1.0
    assert rows["alcohol_daily"].value == 1.0
    assert rows["diet_pattern_daily"].value == pytest.approx(1.0)
    assert "2-minute session" in rows["social_episodes_daily"].note


def test_score_window_returns_seeded_rows_and_never_upserts(db):
    from pipeline.seed.generate import seed_database

    t0 = _at_hour(12)
    t1 = t0 + 120.0
    seed_database(db, end_day=day_key(t1))

    rows = Scorer(db).score_window(t0, t1)
    sources = {row.source for row in rows}
    assert sources == {"live", "seeded"}
    assert all(row.period == "session" for row in rows)
    # Weekly specs are skipped entirely.
    assert not any(row.metric.endswith("_weekly") for row in rows)
    assert by_metric(rows)["sleep_hours"].value is not None
    # Nothing was written: the scores table is still empty.
    assert db.list_scores("daily") == [] and db.list_scores("weekly") == []


def test_score_window_tolerates_a_reversed_window(db):
    t0 = _at_hour(12)
    rows = Scorer(db).score_window(t0 + 120.0, t0)
    assert by_metric(rows)["screen_hours_daily"].period_key == f"{int(t0)}-{int(t0 + 120)}"


# -- narrative ------------------------------------------------------------


def _context(moment_count: int = 2) -> RecapContext:
    moments = [
        {"t": _at_hour(12) + i, "category": "diet" if i % 2 else "alcohol",
         "severity": "good" if i % 2 else "flag",
         "caption": f"moment number {i} with a fairly long caption attached",
         "insight": "an insight that runs on and on and on for a while yet"}
        for i in range(moment_count)
    ]
    subscores = [
        {"metric": "screen_hours_daily", "label": "Screen time", "value": 8.0,
         "unit": "h", "score": 0.66, "source": "live", "grade": "A",
         "target": "<=6 h/day", "note": "at this rate: 8.0 h/day over a 2-minute session"},
        {"metric": "sleep_hours", "label": "Sleep", "value": 7.4, "unit": "h",
         "score": 1.0, "source": "seeded", "grade": "A", "target": "7-9 h",
         "note": None},
    ]
    return RecapContext(duration_s=124.0, subscores=subscores, moments=moments,
                        summary_lines=["12:31 lunch, salad"], overall=0.71)


async def test_fake_narrative_respects_every_word_limit():
    narrative, meta = await FakeNarrativeClient().write(_context())

    assert meta["model"] == "fake"
    assert len(narrative.headline.split()) <= HEADLINE_MAX_WORDS
    assert 2 <= len(narrative.paragraphs) <= 3
    assert all(len(p.split()) <= PARAGRAPH_MAX_WORDS for p in narrative.paragraphs)
    assert 1 <= len(narrative.suggestions) <= 3
    assert all(s.strip() for s in narrative.suggestions)
    assert len(narrative.spoken.split()) <= SPOKEN_MAX_WORDS


async def test_fake_narrative_spoken_is_speakable():
    narrative, _ = await FakeNarrativeClient().write(_context())
    spoken = narrative.spoken
    assert "." in spoken  # it is sentences
    # No decimals and no list punctuation: ElevenLabs reads it verbatim, and
    # it once read '{"type":"nothing"}' aloud.
    assert re.search(r"\d+\.\d", spoken) is None
    assert not any(ch in spoken for ch in ("•", "\n", "{", "["))


async def test_fake_narrative_handles_an_empty_window():
    empty = RecapContext(duration_s=60.0, subscores=[], moments=[], overall=0.0)
    narrative, _ = await FakeNarrativeClient().write(empty)
    assert narrative.headline and len(narrative.paragraphs) == 2
    assert narrative.suggestions and narrative.spoken


async def test_fake_narrative_is_deterministic():
    first, _ = await FakeNarrativeClient().write(_context())
    second, _ = await FakeNarrativeClient().write(_context())
    assert first.model_dump() == second.model_dump()


def test_clamp_trims_an_over_long_model_answer():
    clamped = clamp_narrative(RecapNarrative(
        headline=" ".join(["word"] * 40),
        paragraphs=[" ".join(["word"] * 200), "  ", " ".join(["word"] * 3),
                    "four", "five"],
        suggestions=["a", "b", "c", "d"],
        spoken="screen time was 8.47 hours and diet was 0.67 " + " ".join(["w"] * 90),
    ))
    assert len(clamped.headline.split()) == HEADLINE_MAX_WORDS
    assert len(clamped.paragraphs) == 3
    assert len(clamped.paragraphs[0].split()) == PARAGRAPH_MAX_WORDS
    assert len(clamped.suggestions) == 3
    assert len(clamped.spoken.split()) == SPOKEN_MAX_WORDS
    assert "8.47" not in clamped.spoken and "0.67" not in clamped.spoken
    assert "8" in clamped.spoken


def test_make_narrative_client_follows_the_reasoner_mode():
    settings = Settings(openai_api_key=None)
    assert isinstance(make_narrative_client(settings, "fake"), FakeNarrativeClient)
    # Key-less openai still recaps rather than refusing (unlike T1).
    assert isinstance(make_narrative_client(settings, "openai"), FakeNarrativeClient)
    with pytest.raises(ValueError):
        make_narrative_client(settings, "nonsense")  # type: ignore[arg-type]


# -- labels and units -----------------------------------------------------


def test_labels_and_units_are_short_and_humanised():
    assert label_for("screen_hours_daily") == "Screen time"
    assert label_for("sleep_hours") == "Sleep"
    assert label_for("not_a_metric_daily") == "Not a metric"
    assert unit_for("screen_hours_daily") == "h"
    assert unit_for("social_episodes_daily") == "count"
    assert unit_for("diet_pattern_daily") == "ratio"
    assert unit_for("not_a_metric") == ""


# -- end to end over the simulator ---------------------------------------


async def test_moments_route_returns_moments_with_servable_photos(tmp_path):
    pipeline = await running_pipeline(tmp_path, "moments")
    try:
        async with client_for(pipeline) as client:
            start = pipeline.clock.sim_start_t
            end = pipeline.last_tick.t
            rows = (await client.get("/api/moments",
                                     params={"from": start, "to": end})).json()
            assert rows, "the default scenario should escalate inside 210 ticks"
            assert set(rows[0]) == set(Moment.model_fields)
            assert rows == sorted(rows, key=lambda r: r["t"])
            assert all(start <= r["t"] <= end for r in rows)
            assert {r["category"] for r in rows} <= {
                "diet", "caffeine", "alcohol", "screen", "social", "nature",
                "stress", "followup", "other"}

            photo = await client.get(rows[0]["frame_url"])
            assert photo.status_code == 200
            assert photo.headers["content-type"] == "image/jpeg"
            assert photo.content.startswith(b"\xff\xd8")

            # No window argument defaults to the last 15 minutes of tick clock.
            assert isinstance((await client.get("/api/moments")).json(), list)
    finally:
        await pipeline.stop()


async def test_recap_scores_speaks_and_persists(tmp_path):
    pipeline = await running_pipeline(tmp_path, "recap")
    speech.clear_spoken()
    try:
        async with client_for(pipeline) as client:
            body = (await client.post("/api/recap", json={})).json()

            assert set(body) == RECAP_KEYS
            assert body["id"].startswith("r_") and len(body["id"]) == 10

            session = body["session"]
            assert set(session) == SESSION_KEYS
            assert session["id"] is None and session["name"] is None
            assert session["to_t"] > session["from_t"]
            assert session["duration_s"] == pytest.approx(
                session["to_t"] - session["from_t"])
            assert session["tick_count"] > 0
            assert 0.0 <= session["ai_coverage"] <= 1.0
            assert session["decision_count"] > 0

            score = body["score"]
            assert 0.0 <= score["overall"] <= 1.0
            assert len(score["subscores"]) >= 3
            assert all(set(row) == SUBSCORE_KEYS for row in score["subscores"])
            sources = {row["source"] for row in score["subscores"]}
            assert "live" in sources and "seeded" in sources
            assert all(row["label"] and isinstance(row["unit"], str)
                       for row in score["subscores"])

            assert len(body["moments"]) >= 1
            assert all(set(m) == set(Moment.model_fields) for m in body["moments"])

            narrative = body["narrative"]
            assert set(narrative) == {"headline", "paragraphs", "suggestions", "spoken"}
            assert narrative["headline"] and narrative["spoken"]
            assert len(narrative["paragraphs"]) >= 2 and narrative["suggestions"]

            assert body["spoken"] is True
            assert body["model"] == "fake"
            assert speech.spoken[-1][1] == narrative["spoken"]
            assert speech.spoken[-1][2] == "normal"

            latest = (await client.get("/api/recap/latest")).json()
            assert latest["id"] == body["id"]
            assert latest == body
    finally:
        speech.clear_spoken()
        await pipeline.stop()


async def test_recap_honours_an_explicit_window_and_the_speak_switch(tmp_path):
    pipeline = await running_pipeline(tmp_path, "window")
    speech.clear_spoken()
    try:
        async with client_for(pipeline) as client:
            start = pipeline.clock.sim_start_t
            end = pipeline.last_tick.t
            body = (await client.post("/api/recap", json={
                "from": start, "to": end, "speak": False})).json()
            assert body["session"]["from_t"] == pytest.approx(start)
            assert body["session"]["to_t"] == pytest.approx(end)
            assert body["spoken"] is False
            assert speech.spoken == []

            bad = await client.post("/api/recap", json={"from": "soon"})
            assert bad.status_code == 400
    finally:
        speech.clear_spoken()
        await pipeline.stop()


async def test_recap_resolves_a_session_id_and_404s_on_an_unknown_one(tmp_path):
    pipeline = await running_pipeline(tmp_path, "session")
    try:
        start = pipeline.clock.sim_start_t
        end = pipeline.last_tick.t
        with pipeline.db._lock:
            pipeline.db.conn.execute(
                "INSERT OR REPLACE INTO sessions (id, name, started_t, ended_t)"
                " VALUES (?,?,?,?)", ("s_judge", "Judge 3", start + 10, end - 10))
            pipeline.db.conn.execute(
                "INSERT OR REPLACE INTO sessions (id, name, started_t, ended_t)"
                " VALUES (?,?,?,?)", ("s_open", "Open one", start + 5, None))
            pipeline.db.conn.commit()

        async with client_for(pipeline) as client:
            closed = (await client.post(
                "/api/recap", json={"session_id": "s_judge"})).json()
            assert closed["session"]["id"] == "s_judge"
            assert closed["session"]["name"] == "Judge 3"
            assert closed["session"]["from_t"] == pytest.approx(start + 10)
            assert closed["session"]["to_t"] == pytest.approx(end - 10)

            # An open session runs to the tick clock.
            open_body = (await client.post(
                "/api/recap", json={"session_id": "s_open"})).json()
            assert open_body["session"]["to_t"] >= end

            missing = await client.post("/api/recap", json={"session_id": "nope"})
            assert missing.status_code == 404
            assert missing.json()["detail"] == "unknown session"
    finally:
        await pipeline.stop()


async def test_recap_latest_is_404_until_one_is_generated(tmp_path):
    pipeline = await running_pipeline(tmp_path, "latest", ticks=30)
    try:
        async with client_for(pipeline) as client:
            assert (await client.get("/api/recap/latest")).status_code == 404
            first = (await client.post("/api/recap", json={"speak": False})).json()
            second = (await client.post("/api/recap", json={"speak": False})).json()
            assert first["id"] != second["id"]
            assert (await client.get("/api/recap/latest")).json()["id"] == second["id"]
    finally:
        await pipeline.stop()


# -- window normalisation and the four-hour cap ---------------------------


def test_clamp_window_orders_the_endpoints_and_bounds_the_length():
    t0 = _at_hour(12)
    assert clamp_window(t0, t0 + 120.0) == (t0, t0 + 120.0)
    # Reversed in, ordered out.
    assert clamp_window(t0 + 120.0, t0) == (t0, t0 + 120.0)
    # Ten days in, the most recent four hours out -- the right edge is kept.
    start, end = clamp_window(t0 - 10 * 86400.0, t0)
    assert end == t0 and start == t0 - MAX_WINDOW_S
    # Reversed *and* over-long: both rules apply, in that order.
    start, end = clamp_window(t0, t0 - 10 * 86400.0)
    assert end == t0 and start == t0 - MAX_WINDOW_S


async def test_recap_clamps_an_over_long_window_to_the_most_recent_four_hours(tmp_path):
    pipeline = await running_pipeline(tmp_path, "clamp", ticks=30)
    try:
        async with client_for(pipeline) as client:
            end = pipeline.last_tick.t
            body = (await client.post("/api/recap", json={
                "from": end - 10 * 86400.0, "to": end, "speak": False})).json()

            session = body["session"]
            assert session["to_t"] == pytest.approx(end)
            assert session["from_t"] == pytest.approx(end - MAX_WINDOW_S)
            assert session["duration_s"] == pytest.approx(MAX_WINDOW_S)
    finally:
        await pipeline.stop()


async def test_recap_swaps_a_reversed_window_before_anything_reads_it(tmp_path):
    pipeline = await running_pipeline(tmp_path, "reversed")
    try:
        async with client_for(pipeline) as client:
            start = pipeline.clock.sim_start_t
            end = pipeline.last_tick.t
            # The endpoints arrive the wrong way round.
            body = (await client.post("/api/recap", json={
                "from": end, "to": start, "speak": False})).json()

            session = body["session"]
            assert session["from_t"] == pytest.approx(start)
            assert session["to_t"] == pytest.approx(end)
            assert session["duration_s"] > 0.0
            # ...and every component saw the same window, not an empty one.
            assert body["moments"], "a reversed window must still find its moments"
            assert session["tick_count"] > 0 and session["decision_count"] > 0
            assert all(start <= m["t"] <= end for m in body["moments"])
    finally:
        await pipeline.stop()


# -- timestamp validation -------------------------------------------------


async def test_both_routes_reject_non_finite_and_absurd_timestamps(tmp_path):
    pipeline = await running_pipeline(tmp_path, "bounds", ticks=30)
    try:
        async with client_for(pipeline) as client:
            for bad in ("nan", "inf", "-inf", "-1", "1e18"):
                moments = await client.get("/api/moments", params={"from": bad})
                assert moments.status_code == 400, bad
                moments_to = await client.get("/api/moments", params={"to": bad})
                assert moments_to.status_code == 400, bad

                recap = await client.post("/api/recap", json={"from": bad})
                assert recap.status_code == 400, bad
                recap_to = await client.post("/api/recap", json={"to": bad})
                assert recap_to.status_code == 400, bad

            # A sane window still works on both.
            end = pipeline.last_tick.t
            params = {"from": end - 60.0, "to": end}
            assert (await client.get("/api/moments", params=params)).status_code == 200
            ok = await client.post("/api/recap", json={**params, "speak": False})
            assert ok.status_code == 200
    finally:
        await pipeline.stop()


# -- time-filtered database reads ----------------------------------------


def test_between_queries_read_by_overlap_and_by_time(db):
    t0 = _at_hour(12)
    db.upsert_episode(Episode(id="e_before", kind="screen_block", start_t=t0 - 600,
                              end_t=t0 - 300, duration_s=300.0, open=False))
    db.upsert_episode(Episode(id="e_open", kind="screen_block", start_t=t0 - 600,
                              end_t=None, duration_s=600.0, open=True))
    db.upsert_episode(Episode(id="e_inside", kind="meal", start_t=t0 + 10,
                              end_t=t0 + 20, duration_s=10.0, open=False))
    assert [e.id for e in db.episodes_between(t0, t0 + 60)] == ["e_open", "e_inside"]

    for i, t in enumerate((t0 - 100, t0 + 10, t0 + 20, t0 + 500)):
        db.insert_decision(Decision(id=f"d_{i}", t=t, trigger="food_in_frame",
                                    trigger_tick_id="t_0"))
        db.insert_insight(Insight(id=f"i_{i}", t=t, category="diet", text=f"line {i}",
                                  decision_id=f"d_{i}"))
    assert [d.id for d in db.decisions_between(t0, t0 + 60)] == ["d_1", "d_2"]
    assert [i.id for i in db.insights_between(t0, t0 + 60)] == ["i_1", "i_2"]


def test_score_window_sees_an_episode_that_started_the_previous_day(db):
    # 00:30 today, and a screen block that began at 23:00 yesterday and is still
    # open. Its ``day`` column says yesterday; the window is entirely today.
    start = _at_hour(0, minute=30)
    end = start + 120.0
    began = _at_hour(23) - 86400.0
    db.upsert_episode(Episode(id="e_overnight", kind="screen_block", start_t=began,
                              end_t=None, duration_s=5400.0, open=True))

    assert day_key(began) != day_key(start), "the fixture must straddle midnight"
    assert db.list_episodes(day_key(start)) == []  # the day lookup misses it
    assert [e.id for e in db.episodes_between(start, end)] == ["e_overnight"]

    screen = by_metric(Scorer(db).score_window(start, end))["screen_hours_daily"]
    assert screen.value == pytest.approx(WAKING_HOURS)  # the whole window


# -- the moment cap -------------------------------------------------------


def test_select_moments_caps_the_strip_and_drops_neutrals_first(db):
    t0 = _at_hour(12)
    # 30 decisions, alternating flag (alcohol) and neutral (screen).
    for i in range(30):
        trigger = "alcohol_seen" if i % 2 == 0 else "screen_sustained"
        db.insert_decision(Decision(id=f"d_{i:02d}", t=t0 + i, trigger=trigger,
                                    trigger_tick_id="t_0"))
    evidence = _Evidence({f"d_{i:02d}": [f"f_{i:02d}"] for i in range(30)})

    moments = select_moments(db, evidence, t0 - 1, t0 + 100)

    assert len(moments) == MAX_MOMENTS
    assert [m.t for m in moments] == sorted(m.t for m in moments)
    severities = [m.severity for m in moments]
    assert severities.count("flag") == 15      # every flag survives
    assert severities.count("neutral") == 9    # six oldest neutrals dropped
    kept = {m.decision_id for m in moments}
    assert {f"d_{i:02d}" for i in (1, 3, 5, 7, 9, 11)}.isdisjoint(kept)
    assert "d_13" in kept and "d_00" in kept


def test_select_moments_leaves_a_short_strip_alone(db):
    t0 = _at_hour(12)
    for i in range(MAX_MOMENTS):
        db.insert_decision(Decision(id=f"d_{i:02d}", t=t0 + i,
                                    trigger="screen_sustained", trigger_tick_id="t_0"))
    evidence = _Evidence({f"d_{i:02d}": [f"f_{i:02d}"] for i in range(MAX_MOMENTS)})
    assert len(select_moments(db, evidence, t0 - 1, t0 + 100)) == MAX_MOMENTS


async def test_the_narrative_is_shown_the_capped_moment_list(tmp_path, db):
    """What the model reads is what the dashboard shows: the capped list."""

    t0 = _at_hour(12)
    for i in range(30):
        db.insert_decision(Decision(id=f"d_{i:02d}", t=t0 + i,
                                    trigger="screen_sustained", trigger_tick_id="t_0"))
    evidence = _Evidence({f"d_{i:02d}": [f"f_{i:02d}"] for i in range(30)})
    moments = select_moments(db, evidence, t0 - 1, t0 + 100)

    client = FakeNarrativeClient()
    await client.write(RecapContext(duration_s=120.0,
                                    moments=[m.model_dump() for m in moments]))
    assert len(client.last_context.moments) == MAX_MOMENTS


# -- the narrative deadline ----------------------------------------------


class _SleepingClient:
    """A stub OpenAI client whose ``responses.create`` never comes back."""

    def __init__(self, delay: float = 5.0) -> None:
        self.delay = delay
        self.calls = 0
        self.responses = self

    async def create(self, **kwargs):
        self.calls += 1
        await asyncio.sleep(self.delay)
        raise AssertionError("the recap deadline should have fired first")


async def test_openai_narrative_gives_up_at_the_deadline(monkeypatch):
    monkeypatch.setattr(narrative_module, "RECAP_TIMEOUT_S", 0.05)
    stub = _SleepingClient()
    client = OpenAINarrativeClient("key", "gpt-test", client=stub)
    assert client.timeout == 0.05

    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await client.write(_context())

    assert time.perf_counter() - started < 2.0  # not the stub's five seconds
    assert stub.calls == 1  # one attempt, no silent retry


def test_openai_narrative_client_disables_the_sdk_retries():
    import openai

    captured: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    real = openai.AsyncOpenAI
    openai.AsyncOpenAI = _Recorder
    try:
        client = OpenAINarrativeClient("key", "gpt-test")
    finally:
        openai.AsyncOpenAI = real

    assert captured["max_retries"] == 0
    assert captured["timeout"] == RECAP_TIMEOUT_S == client.timeout


async def test_recap_falls_back_to_the_fake_when_the_model_times_out(tmp_path):
    pipeline = await running_pipeline(tmp_path, "timeout", ticks=30)
    try:
        pipeline._recap_narrative_client = OpenAINarrativeClient(
            "key", "gpt-test", timeout=0.05, client=_SleepingClient())
        async with client_for(pipeline) as client:
            body = (await client.post("/api/recap", json={"speak": False})).json()

        assert body["model"] == "fake"
        assert body["narrative"]["headline"] and body["narrative"]["spoken"]
    finally:
        await pipeline.stop()


def test_clamp_trims_an_over_long_suggestion():
    clamped = clamp_narrative(RecapNarrative(
        suggestions=[" ".join(["word"] * 80), "short one"]))
    assert len(clamped.suggestions[0].split()) == SUGGESTION_MAX_WORDS
    assert clamped.suggestions[1] == "short one"


# -- spoken means accepted for delivery ----------------------------------


async def test_glasses_speak_hook_reports_whether_it_accepted_the_utterance():
    class Link:
        def __init__(self, connected: bool) -> None:
            self.clients = {object()} if connected else set()
            self.messages: list[str] = []

        async def send_text(self, message: str) -> int:
            self.messages.append(message)
            return 1

    assert make_speak_fn(Link(connected=True))("Take a walk", "high") is True
    await asyncio.sleep(0)

    quiet = Link(connected=False)
    assert make_speak_fn(quiet)("Take a walk", "high") is False
    await asyncio.sleep(0)
    assert quiet.messages == []


async def test_recap_is_not_spoken_when_the_hook_declines_it(tmp_path):
    pipeline = await running_pipeline(tmp_path, "declined", ticks=30)
    previous = speech.get_speak_fn()
    offered: list[tuple[str, str]] = []

    def declining(text: str, urgency: str) -> bool:
        offered.append((text, urgency))
        return False

    speech.set_speak_fn(declining)
    speech.clear_spoken()
    try:
        async with client_for(pipeline) as client:
            body = (await client.post("/api/recap", json={})).json()

        assert offered == [(body["narrative"]["spoken"], "normal")]
        assert body["spoken"] is False
        # Nothing reached the phone, so nothing goes in the utterance log.
        assert speech.spoken == []
    finally:
        speech.set_speak_fn(previous)
        speech.clear_spoken()
        await pipeline.stop()


async def test_ending_a_session_generates_its_recap(tmp_path):
    """Ending a session by any route produces its recap without a second call."""
    pipeline = await running_pipeline(tmp_path, "session_end")
    speech.clear_spoken()
    try:
        async with client_for(pipeline) as client:
            started = (await client.post("/api/session/start", json={"name": "judge"})).json()
            await asyncio.sleep(0.2)
            ended = (await client.post("/api/session/end", json={})).json()
            assert ended["id"] == started["id"] and ended["recap"] == "generating"
            for _ in range(200):
                latest = (await client.get("/api/recap/latest")).json()
                if latest and latest.get("session", {}).get("id") == started["id"]:
                    break
                await asyncio.sleep(0.05)
            else:
                raise AssertionError("no recap landed for the ended session")
            assert set(latest) == RECAP_KEYS
    finally:
        await pipeline.stop()
