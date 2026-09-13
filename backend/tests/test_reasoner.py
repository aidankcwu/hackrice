"""T1 admission, the single slot, and the decision row for every escalation.

SPEC §5.4 (drop on contention), §6 (silent and dropped decisions are logged),
§2.5 (frames are copied out at escalation and nowhere else), plus the strict
response schema and its normalisation.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from pipeline.actions.speech import SpeechLimiter, clear_spoken, spoken
from pipeline.config import Settings
from pipeline.db import Database
from pipeline.frames import InMemoryFrameStore
from pipeline.models import AiBlock, Escalation, SensorBlock, Tick
from pipeline.reasoner.client import FakeReasonerClient, OpenAIReasonerClient, make_client
from pipeline.reasoner.reasoner import Reasoner
from pipeline.reasoner.schema import (
    T1_JSON_SCHEMA,
    AnnotateAction,
    LogInsightAction,
    NothingAction,
    SpeakAction,
    T1Response,
    normalize,
)

T0 = 1_757_700_000.0
WINDOW_N = 12


# -- fixtures -------------------------------------------------------------


def make_window(n: int = WINDOW_N, **flags: Any) -> list[Tick]:
    ticks: list[Tick] = []
    for i in range(n):
        # Two plateaus so select_frames has real change points to find.
        phash = "0000000000000000" if i < n // 2 else "ffffffffffff0000"
        ticks.append(
            Tick(
                tick_id=f"t_{i:08d}",
                t=T0 + i,
                seq=i,
                sensor=SensorBlock(lux_proxy=340.0, frame_delta=0.1, phash=phash),
                ai=AiBlock(
                    as_of=T0 + i,
                    age_ms=0,
                    scene="office",
                    activity="seated",
                    **flags,
                ),
                frame_ref=f"f_{i:08d}",
            )
        )
    return ticks


def make_escalation(
    trigger: str = "food_in_frame", window: list[Tick] | None = None
) -> Escalation:
    window = window if window is not None else make_window()
    return Escalation(
        trigger=trigger,
        t=window[-1].t,
        tick=window[-1],
        window=window,
        reason="synthetic",
    )


@pytest.fixture
def db():
    database = Database(":memory:").connect().init_schema()
    yield database
    database.close()


@pytest.fixture
def frame_store():
    store = InMemoryFrameStore(ttl_s=90.0)
    for i in range(WINDOW_N):
        store.put(f"f_{i:08d}", f"jpeg-{i}".encode(), T0 + i)
    return store


@pytest.fixture
def settings():
    return Settings(demo_mode=True, openai_api_key=None)


@pytest.fixture(autouse=True)
def _clean_speech():
    clear_spoken()
    yield
    clear_spoken()


def build_reasoner(db, frame_store, settings, client, **kwargs) -> Reasoner:
    return Reasoner(
        db,
        frame_store,
        client,
        SpeechLimiter(
            settings.timings.speech_min_gap, settings.timings.speech_max_per_hour
        ),
        settings,
        **kwargs,
    )


async def drain(reasoner: Reasoner, timeout: float = 2.0) -> None:
    """Wait for the in-flight T1 task to finish."""

    deadline = asyncio.get_running_loop().time() + timeout
    while reasoner.busy:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("reasoner never released the T1 slot")
        await asyncio.sleep(0.005)
    await asyncio.sleep(0)


# -- schema ---------------------------------------------------------------


def _walk_objects(node: Any):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


def test_strict_schema_closes_every_object_and_requires_every_property():
    objects = list(_walk_objects(T1_JSON_SCHEMA))
    assert len(objects) >= 6, "root plus one per action variant"
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_strict_schema_expresses_optional_fields_as_nullable_unions():
    watch = next(
        obj
        for obj in _walk_objects(T1_JSON_SCHEMA)
        if obj["properties"].get("type", {}).get("enum") == ["watch"]
    )
    assert watch["properties"]["after_s"]["type"] == ["integer", "null"]
    assert watch["properties"]["condition"]["type"] == ["string", "null"]


def test_schema_is_json_serialisable():
    json.dumps(T1_JSON_SCHEMA)


def test_normalize_synthesises_the_missing_annotate():
    resp = T1Response(
        interpretation="Mixed lunch with two colleagues, restaurant.",
        confidence=0.8,
        actions=[LogInsightAction(category="diet", text="…")],
    )

    out = normalize(resp, t=T0)

    lines = [a.line for a in out.actions if a.type == "annotate"]
    assert len(lines) == 1, "SPEC §4.5: every escalation writes a memory line"
    assert lines[0].endswith("Mixed lunch with two colleagues, restaurant.")


def test_normalize_truncates_a_long_interpretation_to_80_chars():
    resp = T1Response(interpretation="x" * 200, confidence=0.5, actions=[])
    line = normalize(resp, t=T0).actions[0].line
    assert len(line) == 80


def test_normalize_keeps_an_existing_annotate():
    resp = T1Response(
        interpretation="…",
        confidence=0.5,
        actions=[AnnotateAction(line="12:31 lunch, mixed plate")],
    )
    out = normalize(resp, t=T0)
    assert [a.line for a in out.actions if a.type == "annotate"] == [
        "12:31 lunch, mixed plate"
    ]


def test_normalize_drops_nothing_when_other_actions_exist():
    resp = T1Response(
        interpretation="…",
        confidence=0.5,
        actions=[NothingAction(), AnnotateAction(line="a")],
    )
    assert [a.type for a in normalize(resp, t=T0).actions] == ["annotate"]


def test_normalize_keeps_a_lone_nothing_but_still_annotates():
    resp = T1Response(interpretation="idle", confidence=0.2, actions=[NothingAction()])
    kinds = [a.type for a in normalize(resp, t=T0).actions]
    assert kinds == ["nothing", "annotate"]


def test_normalize_clamps_confidence():
    assert normalize(T1Response(confidence=1.7), t=T0).confidence == 1.0
    assert normalize(T1Response(confidence=-3.0), t=T0).confidence == 0.0


# -- the fake client ------------------------------------------------------


async def envelope_for(trigger: str, window: list[Tick] | None = None):
    from pipeline.reasoner.envelope import build_envelope

    esc = make_escalation(trigger, window)
    frames = {tk.frame_ref: b"jpeg" for tk in esc.window}
    return build_envelope(esc, frames, [], "7d", "persona")


async def test_fake_client_reads_the_trigger_and_the_food_type():
    window = make_window(food_present=True, food_type="fish")
    resp, meta = await FakeReasonerClient().complete(
        await envelope_for("food_in_frame", window)
    )

    assert meta["model"] == "fake"
    assert "fish" in resp.interpretation
    kinds = [a.type for a in resp.actions]
    assert kinds == ["annotate", "log_insight"]
    assert resp.of_type("log_insight")[0].category == "diet"


async def test_fake_client_screen_trigger_schedules_a_watch():
    resp, _ = await FakeReasonerClient().complete(await envelope_for("screen_sustained"))
    watch = resp.of_type("watch")[0]
    assert watch.after_s == 120
    assert watch.reason == "still at screen?"


async def test_fake_client_biometric_anomaly_logs_a_stress_insight():
    """SPEC §14.3: the wearable gives the number, the frames give the cause."""

    from pipeline.reasoner.envelope import build_envelope

    window = make_window(people_present=True)
    esc = make_escalation("biometric_anomaly", window)
    esc.reason = "Heart rate 118 vs resting 58 while not exercising"
    esc.extra_text = [
        "Heart rate (wearable, bpm) over the last 20s, resting 58: "
        "t-20s 96, t-15s 101, t-10s 104, t-5s 112, t-0s 118"
    ]
    messages = build_envelope(
        esc, {tk.frame_ref: b"jpeg" for tk in esc.window}, [], "7d", "persona"
    )

    resp, _ = await FakeReasonerClient().complete(messages)

    assert [a.type for a in resp.actions] == ["annotate", "log_insight"]
    assert "heart rate" in resp.interpretation
    assert "seated" in resp.interpretation
    assert "office" in resp.interpretation
    insight = resp.of_type("log_insight")[0]
    assert insight.category == "stress"
    assert "HR 118 (resting 58)" in insight.text
    assert "seated" in insight.text and "office" in insight.text
    assert not resp.of_type("speak")


async def test_fake_client_biometric_anomaly_without_numbers_still_annotates():
    resp, _ = await FakeReasonerClient().complete(
        await envelope_for("biometric_anomaly")
    )
    assert [a.type for a in resp.actions] == ["annotate", "log_insight"]
    insight = resp.of_type("log_insight")[0]
    assert insight.category == "stress"
    assert "HR elevated" in insight.text
    assert "resting" not in insight.text


async def test_fake_client_unknown_trigger_annotates_only():
    resp, _ = await FakeReasonerClient().complete(await envelope_for("stillness"))
    assert [a.type for a in resp.actions] == ["annotate"]


async def test_fake_client_watch_trigger_annotates_only():
    resp, _ = await FakeReasonerClient().complete(await envelope_for("watch:w_abc123"))
    assert [a.type for a in resp.actions] == ["annotate"]


async def test_fake_client_keyword_trigger_speaks():
    from pipeline.reasoner.envelope import build_envelope

    esc = make_escalation("rice_krispy")
    esc.extra_text = [
        'Keyword trigger rice_krispy: the VLM caption was "person eating a rice '
        'krispy treat" with objects [rice krispy treat, laptop].'
    ]
    messages = build_envelope(
        esc, {tk.frame_ref: b"jpeg" for tk in esc.window}, [], "7d", "persona"
    )
    resp, _ = await FakeReasonerClient().complete(messages)
    assert [a.type for a in resp.actions] == ["annotate", "speak"]
    assert resp.of_type("speak")[0].text == "rice krispy treats again?"


def test_make_client_refuses_openai_without_a_key():
    with pytest.raises(RuntimeError):
        make_client(Settings(openai_api_key=None), "openai")


def test_make_client_fake_needs_no_key():
    assert isinstance(make_client(Settings(openai_api_key=None), "fake"), FakeReasonerClient)


def test_make_client_rejects_an_unknown_mode():
    with pytest.raises(ValueError):
        make_client(Settings(openai_api_key=None), "gemini")  # type: ignore[arg-type]


def test_openai_client_reparses_a_fenced_response():
    payload = (
        '```json\n{"interpretation": "x", "confidence": 0.5, '
        '"actions": [{"type": "nothing"}]}\n```'
    )
    resp = OpenAIReasonerClient._parse(payload)
    assert resp.interpretation == "x"


def test_openai_client_raises_when_the_retry_also_fails():
    with pytest.raises(Exception):
        OpenAIReasonerClient._parse("not json at all")


# -- admission and the single slot ----------------------------------------


class HoldingClient:
    """Occupies the T1 slot until released. Not a mock -- a controllable one."""

    model = "holding"

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def complete(self, input_messages):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return (
            T1Response(
                interpretation="held",
                confidence=0.5,
                actions=[AnnotateAction(line="12:00 held")],
            ),
            {"model": self.model, "latency_ms": 7},
        )


class SleepingClient:
    model = "sleeping"

    async def complete(self, input_messages):
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled by the deadline")


class ExplodingClient:
    model = "exploding"

    async def complete(self, input_messages):
        raise ValueError("the model is on fire")


async def test_try_escalate_claims_the_slot_then_drops_on_contention(
    db, frame_store, settings
):
    client = HoldingClient()
    reasoner = build_reasoner(db, frame_store, settings, client)

    assert reasoner.try_escalate(make_escalation()) is True
    await client.entered.wait()
    assert reasoner.busy is True

    assert reasoner.try_escalate(make_escalation("screen_sustained")) is False
    assert reasoner.try_escalate(make_escalation("people_sustained")) is False

    dropped = [d for d in db.list_decisions() if d.dropped]
    assert len(dropped) == 2
    assert {d.drop_reason for d in dropped} == {"t1_busy"}
    assert {d.trigger for d in dropped} == {"screen_sustained", "people_sustained"}
    assert all(d.actions == [] for d in dropped)

    client.release.set()
    await drain(reasoner)

    assert reasoner.busy is False
    assert client.calls == 1, "a dropped escalation never reaches the model"
    assert reasoner.stats()["dropped_busy"] == 2


async def test_a_busy_drop_does_not_collide_with_the_running_decision_id(
    db, frame_store, settings
):
    """SPEC §5.4: a drop that lands while the original is still in flight must
    keep its own row.

    IDs used to be allocated as ``COUNT(*)+1`` at call time. The running
    escalation claims an id before the model returns but only INSERTs its row
    afterwards, so a busy-drop arriving in between recomputed the identical
    COUNT(*)-based id; when the original finally inserted, ``INSERT OR
    REPLACE`` overwrote the drop and it vanished from the decisions table.
    """

    client = HoldingClient()
    reasoner = build_reasoner(db, frame_store, settings, client)

    assert reasoner.try_escalate(make_escalation()) is True
    await client.entered.wait()
    assert reasoner.busy is True

    assert reasoner.try_escalate(make_escalation("screen_sustained")) is False

    client.release.set()
    await drain(reasoner)

    decisions = {d.id: d for d in db.list_decisions()}
    assert len(decisions) == 2, "both the drop and the completed run must survive"

    dropped = [d for d in decisions.values() if d.dropped]
    completed = [d for d in decisions.values() if not d.dropped]
    assert len(dropped) == 1
    assert len(completed) == 1
    assert dropped[0].drop_reason == "t1_busy"
    assert dropped[0].actions == []
    assert completed[0].actions, "the completed decision kept its actions"
    assert dropped[0].id != completed[0].id


async def test_the_slot_is_reusable_after_a_completed_escalation(
    db, frame_store, settings
):
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)
    assert reasoner.try_escalate(make_escalation("screen_sustained")) is True
    await drain(reasoner)

    assert reasoner.stats()["completed"] == 2
    assert reasoner.stats()["dropped_busy"] == 0
    assert [d.id for d in db.list_decisions()] == ["d_0002", "d_0001"]


async def test_a_completed_escalation_writes_a_decision_with_actions(
    db, frame_store, settings
):
    window = make_window(food_present=True, food_type="mixed")
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())

    assert reasoner.try_escalate(make_escalation("food_in_frame", window)) is True
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert decision.dropped is False
    assert decision.drop_reason is None
    assert decision.trigger == "food_in_frame"
    assert decision.trigger_tick_id == window[-1].tick_id
    assert decision.t == window[-1].t
    assert decision.model == "fake"
    assert decision.latency_ms is not None
    assert "mixed" in decision.interpretation
    assert {a["type"] for a in decision.actions} == {"annotate", "log_insight"}


async def test_a_completed_escalation_applies_its_actions(db, frame_store, settings):
    from pipeline.db import day_key

    window = make_window(food_present=True, food_type="mixed")
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())

    reasoner.try_escalate(make_escalation("food_in_frame", window))
    await drain(reasoner)

    lines = db.today_summary_lines(day=day_key(window[-1].t))
    assert len(lines) == 1
    assert lines[0].decision_id == "d_0001"
    assert len(db.list_insights()) == 1


async def test_escalation_copies_at_most_four_frames_before_inference(
    db, frame_store, settings
):
    client = HoldingClient()
    reasoner = build_reasoner(db, frame_store, settings, client)

    reasoner.try_escalate(make_escalation())
    await client.entered.wait()

    # The copy is durable *before* the model answers (SPEC §2.5).
    rows = reasoner.evidence.list("d_0001")
    assert 1 <= len(rows) <= 4
    assert len(rows) == 4
    assert rows[-1]["frame_ref"] == f"f_{WINDOW_N - 1:08d}", "trigger frame included"
    assert reasoner.evidence.get("d_0001", rows[0]["frame_ref"]) is not None
    assert all("jpeg" not in row for row in rows), "list() returns no bytes"

    client.release.set()
    await drain(reasoner)

    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM escalated_frames WHERE decision_id = 'd_0001'"
        ).fetchone()[0]
        == 4
    )


async def test_missing_frames_are_skipped_not_fatal(db, settings):
    empty = InMemoryFrameStore(ttl_s=90.0)
    reasoner = build_reasoner(db, empty, settings, FakeReasonerClient())

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert decision.dropped is False
    assert reasoner.evidence.list("d_0001") == []
    assert reasoner.stats()["frames_missing"] == 4


async def test_a_timeout_writes_a_dropped_decision(db, frame_store, settings):
    reasoner = build_reasoner(
        db, frame_store, settings, SleepingClient(), t1_deadline_s=0.05
    )

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert decision.dropped is True
    assert decision.drop_reason == "t1_timeout"
    assert decision.trigger == "food_in_frame"
    assert reasoner.stats()["dropped_timeout"] == 1
    assert reasoner.busy is False, "the slot is released on the timeout path"


async def test_a_client_exception_writes_a_dropped_decision(db, frame_store, settings):
    reasoner = build_reasoner(db, frame_store, settings, ExplodingClient())

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert decision.dropped is True
    assert decision.drop_reason == "t1_error:ValueError"
    assert reasoner.stats()["dropped_error"] == 1
    assert reasoner.busy is False


async def test_the_slot_survives_a_handler_that_throws(db, frame_store, settings):
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())

    def boom(*_args, **_kwargs):
        raise RuntimeError("db is on fire")

    reasoner.handler.apply = boom  # type: ignore[method-assign]

    assert reasoner.try_escalate(make_escalation()) is True
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert decision.dropped is False, "the decision row exists even so (SPEC §6)"
    assert decision.actions
    assert reasoner.busy is False


# -- speech and the feed --------------------------------------------------


async def test_spoke_is_recorded_on_the_decision_row(db, frame_store, settings):
    """A late-afternoon caffeine sighting is the one thing the fake speaks about."""

    import time as _time

    afternoon = _time.mktime(
        _time.strptime(
            _time.strftime("%Y-%m-%d") + " 16:30:00", "%Y-%m-%d %H:%M:%S"
        )
    )
    window = make_window(caffeine_visible=True)
    window = [tk.model_copy(update={"t": afternoon + tk.seq}) for tk in window]
    esc = Escalation(
        trigger="caffeine_seen",
        t=window[-1].t,
        tick=window[-1],
        window=window,
        reason="caffeine_visible",
    )
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())

    reasoner.try_escalate(esc)
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert any(a["type"] == "speak" for a in decision.actions)
    assert decision.spoke is True
    assert len(spoken) == 1
    assert spoken[0][1].startswith("Coffee this late")
    assert reasoner.stats()["spoke"] == 1


async def test_a_speak_action_can_be_present_with_spoke_false(
    db, frame_store, settings
):
    """docs/API.md: `spoke` is what reached speak() after the rate limiter."""

    class SpeakingClient:
        model = "speaking"

        async def complete(self, input_messages):
            return (
                T1Response(
                    interpretation="talk",
                    confidence=0.9,
                    actions=[SpeakAction(text="hello", urgency="low")],
                ),
                {"model": "speaking", "latency_ms": 1},
            )

    reasoner = build_reasoner(db, frame_store, settings, SpeakingClient())
    reasoner.speech.min_gap_s = 20.0  # demo timings have no gap; this test is about the gap
    reasoner.speech._granted.append(T0 + WINDOW_N - 1)  # just spoke

    reasoner.try_escalate(make_escalation())
    await drain(reasoner)

    (decision,) = db.list_decisions()
    assert any(a["type"] == "speak" for a in decision.actions)
    assert decision.spoke is False
    assert spoken == []


async def test_feed_line_matches_the_documented_format(db, frame_store, settings):
    window = make_window(food_present=True, food_type="mixed")
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())
    reasoner.try_escalate(make_escalation("food_in_frame", window))
    await drain(reasoner)

    (decision,) = db.list_decisions()
    line = Reasoner.feed_line(decision)

    parts = [p.strip() for p in line.split("·")]
    assert len(parts) == 5
    assert parts[1] == "food_in_frame"
    assert parts[3] == "annotate, log_insight"
    assert parts[4] == "silent"


async def test_the_envelope_carries_today_lines_from_earlier_escalations(
    db, frame_store, settings
):
    client = FakeReasonerClient()
    reasoner = build_reasoner(db, frame_store, settings, client)

    reasoner.try_escalate(make_escalation("food_in_frame"))
    await drain(reasoner)
    reasoner.try_escalate(make_escalation("screen_sustained"))
    await drain(reasoner)

    user_text = FakeReasonerClient._user_text(client.last_envelope)
    assert "Today so far:" in user_text
    assert "nothing yet" not in user_text, "the first decision's line must carry over"


async def test_the_seven_day_callable_is_used_and_its_failure_is_survivable(
    db, frame_store, settings
):
    client = FakeReasonerClient()
    reasoner = build_reasoner(
        db, frame_store, settings, client, seven_day_summary=lambda: "SEVEN-DAY"
    )
    reasoner.try_escalate(make_escalation())
    await drain(reasoner)
    assert "SEVEN-DAY" in client.last_envelope[0]["content"][0]["text"]

    def angry() -> str:
        raise RuntimeError("no history service")

    reasoner.seven_day_summary = angry
    reasoner.try_escalate(make_escalation())
    await drain(reasoner)
    assert "No 7-day history available." in client.last_envelope[0]["content"][0]["text"]
    assert reasoner.stats()["completed"] == 2


def test_try_escalate_without_a_running_loop_drops_rather_than_raising(
    db, frame_store, settings
):
    reasoner = build_reasoner(db, frame_store, settings, FakeReasonerClient())

    assert reasoner.try_escalate(make_escalation()) is False

    (decision,) = db.list_decisions()
    assert decision.dropped is True
    assert decision.drop_reason == "t1_no_loop"
    assert reasoner.busy is False


def test_normalize_drops_non_speech_speak_text() -> None:
    """A speak carrying 'nothing' or JSON is silence done wrong; drop it."""
    from pipeline.reasoner.schema import T1Response, normalize

    for bad in ('{"type":"nothing"}', "nothing", "", "  ", '[{"x":1}]'):
        resp = T1Response.model_validate({
            "interpretation": "food on the desk, nobody eating", "confidence": 0.8,
            "actions": [{"type": "speak", "text": bad, "urgency": "low"}],
        })
        norm = normalize(resp, t=1.0)
        assert not any(a.type == "speak" for a in norm.actions), bad
        assert any(a.type == "annotate" for a in norm.actions)
    good = T1Response.model_validate({
        "interpretation": "x", "confidence": 0.8,
        "actions": [{"type": "speak", "text": "Cereal at 5am. Bold.", "urgency": "low"}],
    })
    assert any(a.type == "speak" for a in normalize(good, t=1.0).actions)


def test_a_keyword_trigger_with_a_fixed_line_speaks_it_without_a_model_call(db, frame_store, settings):
    """Demo: rice krispy in frame -> "Put down the rice krispy." verbatim, no GPT."""

    class NeverCalled:
        model = "never"
        async def complete(self, input_messages):
            raise AssertionError("the clerk must not be called for a fixed line")

    class FakeAgent:
        def __init__(self): self.calls = []
        def request_fixed(self, text, topic, *, decision_id=None):
            self.calls.append((text, topic)); return "handed_off:c_fixed01"
        def request(self, *a, **k): raise AssertionError("not a hand-off")

    reasoner = build_reasoner(db, frame_store, settings, NeverCalled())
    agent = FakeAgent()
    reasoner._conversation = agent
    assert reasoner.try_escalate(make_escalation(trigger="rice_krispy"))
    assert agent.calls == [("PUT THE RICE KRISPY DOWN!", make_escalation(trigger="rice_krispy").reason or "rice_krispy")]
    rows = db.list_decisions()
    assert rows and rows[-1].model == "fixed" and rows[-1].spoke
    assert rows[-1].actions[0]["outcome"] == "handed_off:c_fixed01"
    assert not reasoner.busy
