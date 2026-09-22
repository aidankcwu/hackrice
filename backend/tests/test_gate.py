from __future__ import annotations

import re

import pytest

from pipeline.config import Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder
from pipeline.gate import (
    BiometricFeed,
    CallableBiometricFeed,
    Trigger,
    TriggerGate,
    biometric_anomaly_trigger,
    default_triggers,
    keyword_trigger,
)
from pipeline.gate.triggers import change_trigger, wearable_now_line
from pipeline.models import AiBlock, Escalation, PendingCheck, SensorBlock, Tick
from pipeline.sim import DEFAULT_SCENARIO, SimSource


def tick(seq: int, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=float(seq), seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{seq}",
    )


def test_gate_episode_suppression_drop_and_watch(tmp_path) -> None:
    db = Database(tmp_path / "gate.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    seen = []
    gate = TriggerGate(default_triggers(Timings.demo(), True), Timings.demo(), db, episodes, lambda e: seen.append(e) is None, True)
    for i in range(8):
        current = tick(i, scene="restaurant", activity="eating", food_present=True)
        episodes.on_tick(current)
        gate.on_tick(current)
    assert [e.trigger for e in seen].count("food_in_frame") == 1
    assert gate.suppressed["food_in_frame"] > 0

    dropped_gate = TriggerGate(
        [Trigger("always", lambda _: True, 0, None, "test")], Timings.demo(), db,
        episodes, lambda _: False, True,
    )
    assert dropped_gate.on_tick(tick(100)) is not None
    assert dropped_gate.dropped == 1

    db.insert_pending_check(PendingCheck(id="p1", created_t=100, due_t=101, reason="check posture"))
    watch = []
    watch_gate = TriggerGate([], Timings.demo(), db, episodes, lambda e: watch.append(e) is None, True)
    watch_gate.on_tick(tick(101))
    assert watch[0].trigger == "watch:check posture"
    assert db.due_pending_checks(102) == []
    db.close()


def test_cooldown_and_global_gap(tmp_path) -> None:
    db = Database(tmp_path / "limits.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    accepted = []
    triggers = [
        Trigger("a", lambda w: w[-1].seq in {0, 20}, 30, None, "a"),
        Trigger("b", lambda w: w[-1].seq in {1, 40}, 0, None, "b"),  # 1 s: inside the 2 s global gap
    ]
    assert Timings.demo().global_escalation_min_gap == 2.0
    gate = TriggerGate(triggers, Timings.demo(), db, episodes, lambda e: accepted.append(e) is None, True)
    for i in (0, 1, 20, 40):
        gate.on_tick(tick(i))
    assert [e.trigger for e in accepted] == ["a", "b"]
    assert gate.suppressed == {"b": 1, "a": 1}
    db.close()


@pytest.mark.parametrize("interval_s", [1.0, 1.5])
def test_default_scenario(tmp_path, interval_s: float) -> None:
    """The scripted day escalates identically at 1 Hz and at the glasses' 1.5 s.

    Scenario segments are scripted in *seconds*, so a 1.5 s cadence puts ~13
    ticks in a 20 s window where 1 Hz put 20. Every ``*_min_hits`` is divided
    down by :meth:`Timings.scaled_hits`, and the day must still produce the
    same six triggers and the same episode kinds -- otherwise the gate is
    silently tuned for a stream that does not exist (SPEC §3, §12.2).
    """

    timings = Timings.demo(tick_interval_s=interval_s)
    db = Database(tmp_path / "scenario.db").connect().init_schema()
    episodes = EpisodeBuilder(db, timings)
    escalations = []
    gate = TriggerGate(default_triggers(timings, True), timings, db, episodes, lambda e: escalations.append(e) is None, True)
    source = SimSource(DEFAULT_SCENARIO, frame_store=None, speed=1, seed=0,
                       interval_s=interval_s)
    for _ in range(400):
        current = source.next_tick()
        episodes.on_tick(current)
        gate.on_tick(current)
    names = [e.trigger for e in escalations]
    print(f"scenario escalations @{interval_s}s:", names)
    assert set(names) == {
        "food_in_frame", "screen_sustained", "people_sustained",
        "outdoor_sustained", "caffeine_seen", "alcohol_seen", "change",
    } - {"people_sustained"}, names
    # The scripted evening is phone_use at home with `screen_present` and no
    # laptop anywhere: the phone's own screen. That is not "phone at the
    # laptop" (it used to be, and so was walking on stage checking a phone).
    assert not [e for e in escalations if e.trigger == "cue"]
    bound_triggers = {
        "food_in_frame", "screen_sustained", "outdoor_sustained",
    }
    assert all(
        escalation.episode_id is not None
        for escalation in escalations
        if escalation.trigger in bound_triggers
    )
    kinds = {episode.kind for episode in db.list_episodes()}
    assert {"meal", "screen_block", "outdoor_block"} <= kinds
    assert "conversation" not in kinds
    db.close()


def test_sighting_triggers_stay_reachable_at_the_slow_cadence(tmp_path) -> None:
    """A cup seen once inside the 10 s window is a cup (S9).

    ``caffeine_seen`` is a point observation, two hits at 1 Hz; scaled to 1.5 s
    that floors to one. The window does not scale -- only the count does.
    """

    timings = Timings.demo(tick_interval_s=1.5)
    db = Database(tmp_path / "sight.db").connect().init_schema()
    episodes = EpisodeBuilder(db, timings)
    escalations = []
    gate = TriggerGate(default_triggers(timings, True), timings, db, episodes,
                       lambda e: escalations.append(e) is None, True)
    # One positive tick, on a 1.5 s clock, with nothing else in the window.
    current = Tick(
        tick_id="t_0", t=0.0, seq=0,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{0:016x}"),
        ai=AiBlock(age_ms=0, caffeine_visible=True), frame_ref="f_0",
    )
    episodes.on_tick(current)
    gate.on_tick(current)
    assert [e.trigger for e in escalations] == ["caffeine_seen"]
    db.close()


def test_suppressed_trigger_does_not_block_others(tmp_path) -> None:
    """A screen_block already escalated must not stop caffeine_seen firing."""
    db = Database(tmp_path / "s.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    escalations = []
    gate = TriggerGate(default_triggers(Timings.demo(), True), Timings.demo(), db, episodes, lambda e: escalations.append(e) is None, True)
    seq = 0
    for _ in range(30):  # screen only -> screen_sustained fires, screen_block opens
        t = tick(seq, screen_present=True); seq += 1
        episodes.on_tick(t); gate.on_tick(t)
    for _ in range(30):  # coffee appears while still at the screen
        t = tick(seq, screen_present=True, caffeine_visible=True); seq += 1
        episodes.on_tick(t); gate.on_tick(t)
    names = [e.trigger for e in escalations]
    assert names[0] == "screen_sustained"
    assert "caffeine_seen" in names, names
    db.close()


def test_keyword_trigger_fires_on_two_fresh_hits_then_cools_down(tmp_path) -> None:
    timings = Timings.demo()
    trigger = keyword_trigger(
        "rice_krispy", ["rice krispy"], timings, cooldown_s=60,
        reason="Keyword '{kw}' seen in caption/objects",
        extra_line='caption="{caption}" objects=[{objects}]',
    )
    db = Database(tmp_path / "keyword.db").connect().init_schema()
    episodes = EpisodeBuilder(db, timings)
    escalations: list[Escalation] = []
    gate = TriggerGate([trigger], timings, db, episodes,
                       lambda e: escalations.append(e) is None, True)

    gate.on_tick(tick(0, caption="person eating a rice krispy treat"))
    gate.on_tick(tick(1, objects=["rice krispy treat", "laptop"]))
    gate.on_tick(tick(2, caption="person eating a rice krispy treat"))
    assert len(escalations) == 1
    assert escalations[0].reason == "Keyword 'rice krispy' seen in caption/objects"
    assert escalations[0].extra_text == [
        'caption="" objects=[rice krispy treat, laptop]'
    ]
    assert gate.suppressed["rice_krispy"] == 1
    db.close()


def test_keyword_trigger_ignores_unrelated_and_stale_ai() -> None:
    trigger = keyword_trigger(
        "rice_krispy", ["rice krispy"], Timings.demo(), cooldown_s=60,
        reason="Keyword '{kw}' seen", extra_line="{caption} [{objects}]",
    )
    assert not trigger.predicate([tick(0, caption="person using a laptop"),
                                  tick(1, objects=["plate", "laptop"])])
    stale = tick(2, caption="rice krispy treat")
    stale.ai.age_ms = 9999  # type: ignore[union-attr]
    assert not trigger.predicate([tick(1, caption="rice krispy treat"), stale])


def test_change_trigger_scene_activity_object_and_in_hand() -> None:
    trigger = change_trigger(Timings.demo())
    window = [
        tick(0, scene="home", activity="computer_use", objects=["laptop"]),
        tick(1, scene="outdoor_other", activity="walking", food_present=True,
             caption="holding a snack bar in hand", objects=["snack bar"]),
        tick(2, scene="outdoor_other", activity="walking", food_present=True,
             caption="holding a snack bar in hand", objects=["snack bar"]),
    ]
    assert trigger.predicate(window)
    reason, extra = trigger.enrich(window)  # type: ignore[misc]
    for fragment in ("scene home -> outdoor_other", "activity computer_use -> walking",
                     "new object: snack bar", "food in hand"):
        assert fragment in reason
    assert extra and "Visual transition:" in extra[0]


def test_change_trigger_rejects_flicker_and_stale_ticks() -> None:
    trigger = change_trigger(Timings.demo())
    assert not trigger.predicate([
        tick(0, scene="home"), tick(1, scene="outdoor_other"), tick(2, scene="home"),
    ])
    stale = tick(2, scene="outdoor_other")
    stale.ai.age_ms = 9999  # type: ignore[union-attr]
    assert not trigger.predicate([tick(0, scene="home"), tick(1, scene="outdoor_other"), stale])


def test_change_trigger_cooldown_and_per_minute_cap() -> None:
    trigger = change_trigger(Timings.demo())
    assert trigger.predicate([tick(0, scene="home"), tick(1, scene="street"), tick(2, scene="street")])
    trigger.enrich([tick(0, scene="home"), tick(1, scene="street"), tick(2, scene="street")])  # type: ignore[misc]
    # Rendering the reason does not spend the budget: an escalation the gate
    # then rejected (T1 busy, global gap) must not cost a later real change.
    assert trigger.predicate([tick(2, scene="street"), tick(3, scene="home"), tick(4, scene="home")])
    trigger.on_fired(2.0)  # type: ignore[misc]  # the gate accepted it
    assert not trigger.predicate([tick(2, scene="street"), tick(3, scene="home"), tick(4, scene="home")])

    # Five more accepted changes reach the demo cap of six inside one minute.
    prior, now = "street", "home"
    for base in (10, 19, 28, 37, 46):
        window = [tick(base, scene=prior), tick(base + 1, scene=now), tick(base + 2, scene=now)]
        assert trigger.predicate(window)
        trigger.enrich(window)  # type: ignore[misc]
        trigger.on_fired(float(base + 2))  # type: ignore[misc]
        prior, now = now, prior
    blocked = [tick(55, scene=prior), tick(56, scene=now), tick(57, scene=now)]
    assert not trigger.predicate(blocked)


# -- biometric_anomaly (SPEC §14.3) ---------------------------------------


class StubFeed:
    """A flat HR series on the tick clock, one sample per second.

    ``extra`` stands in for the rest of the SPEC §14.1 metric set: a mapping of
    metric -> (t, value) used by the "wearable now" line.
    """

    def __init__(self, bpm: float, resting: float = 58.0, seconds: float = 40.0,
                 extra: dict[str, tuple[float, float]] | None = None,
                 steps: list[tuple[float, float]] | None = None) -> None:
        self.bpm, self.resting, self.seconds = bpm, resting, seconds
        self.extra = extra or {}
        self.steps = steps or []
        self.reads = 0

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]:
        """Samples exist only from t=0 (series start) to ``self.seconds``."""

        self.reads += 1
        start = max(0.0, t0, t1 - self.seconds)
        return [(float(t), self.bpm) for t in range(int(start), int(t1) + 1)]

    def resting_hr(self) -> float:
        return self.resting

    def series(self, metric: str, t0: float, t1: float) -> list[tuple[float, float]]:
        if metric == "heart_rate":
            return self.hr_series(t0, t1)
        if metric == "steps_delta":
            return [(t, v) for t, v in self.steps if t0 <= t <= t1]
        return []

    def latest(self, metric: str) -> tuple[float, float] | None:
        if metric == "heart_rate":
            return (self.seconds, self.bpm)
        return self.extra.get(metric)

    def latest_source(self, metric: str) -> str | None:
        if metric == "heart_rate":
            return "apple_watch"
        return "whoop" if metric in self.extra else None


def bio_gate(tmp_path, name: str, feed: BiometricFeed):
    db = Database(tmp_path / f"{name}.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    escalations: list[Escalation] = []
    gate = TriggerGate(
        [biometric_anomaly_trigger(Timings.demo(), feed)],
        Timings.demo(), db, episodes, lambda e: escalations.append(e) is None, True,
    )
    return db, gate, escalations


def test_biometric_feed_protocol_is_satisfied_by_the_stub() -> None:
    assert isinstance(StubFeed(60.0), BiometricFeed)


def test_resting_heart_rate_never_fires(tmp_path) -> None:
    db, gate, escalations = bio_gate(tmp_path, "flat", StubFeed(60.0))
    for i in range(60):
        gate.on_tick(tick(i, activity="seated"))
    assert escalations == []
    db.close()


def test_sustained_elevated_hr_while_seated_fires_once_then_cools_down(tmp_path) -> None:
    db, gate, escalations = bio_gate(tmp_path, "hot", StubFeed(100.0))
    for i in range(70):  # first fire ~t=16 s, then the 60 s cooldown holds
        gate.on_tick(tick(i, activity="seated"))

    assert [e.trigger for e in escalations] == ["biometric_anomaly"]
    assert gate.suppressed["biometric_anomaly"] > 0

    for i in range(70, 140):  # past the cooldown the spike may escalate again
        gate.on_tick(tick(i, activity="seated"))
    assert len(escalations) > 1
    assert escalations[1].t - escalations[0].t >= Timings.demo().biometric_cooldown

    esc = escalations[0]
    assert "100" in esc.reason and "58" in esc.reason
    assert len(esc.extra_text) == 1
    line = esc.extra_text[0]
    assert "resting 58" in line
    assert len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 5
    assert 5 <= line.count("t-") <= 12, "the series is subsampled, not dumped"
    db.close()


def test_walking_explains_the_heart_rate_and_suppresses_the_trigger(tmp_path) -> None:
    db, gate, escalations = bio_gate(tmp_path, "walk", StubFeed(100.0))
    for i in range(60):
        gate.on_tick(tick(i, activity="walking"))
    assert escalations == []
    db.close()


def test_unknown_activity_still_fires(tmp_path) -> None:
    """No known activity in the window: the wearable still gets to speak."""

    db, gate, escalations = bio_gate(tmp_path, "unknown", StubFeed(100.0))
    for i in range(60):
        gate.on_tick(tick(i))  # no ai block at all
    assert [e.trigger for e in escalations] == ["biometric_anomaly"]
    db.close()


def test_a_short_series_is_not_enough(tmp_path) -> None:
    db, gate, escalations = bio_gate(tmp_path, "short", StubFeed(100.0, seconds=3.0))
    for i in range(60):
        gate.on_tick(tick(i, activity="seated"))
    assert escalations == []
    db.close()


def test_default_triggers_appends_the_biometric_trigger_last() -> None:
    plain = default_triggers(Timings.demo(), True)
    withfeed = default_triggers(Timings.demo(), True, feed=StubFeed(100.0))
    assert [t.name for t in plain] == [t.name for t in withfeed[:-1]]
    assert withfeed[-1].name == "biometric_anomaly"
    assert withfeed[-1].cooldown_s == Timings.demo().biometric_cooldown


def test_callable_feed_adapts_injected_callables_and_swallows_failures() -> None:
    stub = StubFeed(100.0)
    feed = CallableBiometricFeed(
        lambda metric, t0, t1: stub.series(metric, t0, t1),
        lambda: stub.resting,
        lambda metric: (12.0, 41.0, "whoop", "live") if metric == "hrv_rmssd" else None,
    )
    assert isinstance(feed, BiometricFeed)
    assert feed.resting_hr() == 58.0
    assert len(feed.hr_series(0.0, 20.0)) == 21
    assert feed.latest("hrv_rmssd") == (12.0, 41.0)
    assert feed.latest_source("hrv_rmssd") == "whoop"
    assert feed.latest("spo2") is None

    def boom(*_: object) -> list[tuple[float, float]]:
        raise RuntimeError("db is mid-migration")

    broken = CallableBiometricFeed(boom, lambda: 58.0, boom)
    assert broken.hr_series(0.0, 20.0) == []  # a feed read never breaks a tick
    assert broken.latest("heart_rate") is None
    assert broken.latest_source("heart_rate") is None


def test_a_feed_with_no_latest_hook_renders_no_wearable_line() -> None:
    """A bare HR-only feed must not crash the line, it just has nothing to say."""

    feed = CallableBiometricFeed(lambda m, a, b: [], lambda: 58.0)
    assert wearable_now_line(feed, 100.0) is None


def test_wearable_now_line_renders_every_metric_present() -> None:
    feed = StubFeed(96.0, seconds=100.0, extra={
        "hrv_rmssd": (95.0, 41.0),
        "spo2": (90.0, 97.0),
        "respiratory_rate": (90.0, 17.0),
        "wrist_temp_dev": (60.0, 0.1),
        "strain": (30.0, 6.2),
    }, steps=[(t, 0.0) for t in range(40, 101, 20)])
    line = wearable_now_line(feed, 100.0)
    assert line is not None
    assert line.startswith("Wearable now (apple_watch/whoop): ")
    for fragment in ("HR 96 bpm", "HRV 41 ms", "SpO2 97%", "RR 17",
                     "wrist temp +0.1", "strain 6.2", "steps last 10 min 0"):
        assert fragment in line, line


def test_wearable_now_line_skips_stale_and_missing_metrics() -> None:
    """A sample from an hour ago is not "now" and a missing metric is silent."""

    feed = StubFeed(70.0, seconds=100.0, extra={"hrv_rmssd": (-9999.0, 41.0)})
    line = wearable_now_line(feed, 100.0)
    assert line == "Wearable now (apple_watch): HR 70 bpm"


def test_every_escalation_carries_the_wearable_line(tmp_path) -> None:
    """Not just biometric_anomaly -- the body's state is context for anything."""

    feed = StubFeed(62.0, seconds=100.0, extra={"spo2": (95.0, 97.0)})
    db = Database(tmp_path / "wear.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    escalations: list[Escalation] = []
    gate = TriggerGate(
        default_triggers(Timings.demo(), True), Timings.demo(), db, episodes,
        lambda e: escalations.append(e) is None, True, feed=feed,
    )
    for seq in range(40):
        t = tick(seq, screen_present=True)
        episodes.on_tick(t)
        gate.on_tick(t)
    assert escalations, "a camera trigger should have fired"
    first = escalations[0]
    assert first.trigger != "biometric_anomaly"
    assert any(line.startswith("Wearable now") for line in first.extra_text), first.extra_text
    db.close()


def test_the_biometric_escalation_keeps_its_hr_series_line(tmp_path) -> None:
    """The HR series is the detail; the "wearable now" line is the context."""

    feed = StubFeed(100.0, extra={"strain": (20.0, 6.2)})
    db, gate, escalations = bio_gate(tmp_path, "both", feed)
    gate.feed = feed
    for i in range(40):
        gate.on_tick(tick(i, activity="seated"))
    assert escalations
    lines = escalations[0].extra_text
    assert len(lines) == 2
    assert lines[0].startswith("Heart rate (wearable, bpm)")
    assert lines[1].startswith("Wearable now")
    db.close()


def test_a_gate_without_a_feed_attaches_nothing(tmp_path) -> None:
    db = Database(tmp_path / "nofeed.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo())
    escalations: list[Escalation] = []
    gate = TriggerGate(default_triggers(Timings.demo(), True), Timings.demo(), db,
                       episodes, lambda e: escalations.append(e) is None, True)
    for seq in range(40):
        t = tick(seq, screen_present=True)
        episodes.on_tick(t)
        gate.on_tick(t)
    assert escalations and escalations[0].extra_text == []
    db.close()
