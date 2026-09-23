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
from pipeline.models import AiBlock, Escalation, PendingCheck, SensorBlock, Tick, WatchBlock
from pipeline.sim import DEFAULT_SCENARIO, SimSource

#: Every gate test that must not change with GATE_READS_WATCH runs both ways.
READS_WATCH = pytest.mark.parametrize("reads_watch", [False, True])


def tick(seq: int, **ai: object) -> Tick:
    return Tick(
        tick_id=f"t_{seq}", t=float(seq), seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=AiBlock(age_ms=0, **ai) if ai else None, frame_ref=f"f_{seq}",
    )


def watch_tick(seq: int, scores: dict[str, float] | None = None, *,
               novelty: float = 0.0, **ai: object) -> Tick:
    """A tick carrying a ``watch`` block; ``hot`` follows the 0.60 default enter."""

    scores = scores or {}
    current = tick(seq, **ai)
    current.watch = WatchBlock(
        frames=10, usable=10, scores=scores, novelty=novelty,
        hot=[name for name, score in scores.items() if score >= 0.60],
    )
    return current


def trigger_named(name: str, **kwargs: object) -> Trigger:
    return next(t for t in default_triggers(Timings.demo(), True, **kwargs) if t.name == name)


@READS_WATCH
def test_gate_episode_suppression_drop_and_watch(tmp_path, reads_watch: bool) -> None:
    db = Database(tmp_path / "gate.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
    seen = []
    gate = TriggerGate(default_triggers(Timings.demo(), True, reads_watch=reads_watch),
                       Timings.demo(), db, episodes, lambda e: seen.append(e) is None, True)
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


@READS_WATCH
@pytest.mark.parametrize("interval_s", [1.0, 1.5])
def test_default_scenario(tmp_path, interval_s: float, reads_watch: bool) -> None:
    """The scripted day escalates identically at 1 Hz and at the glasses' 1.5 s.

    Scenario segments are scripted in *seconds*, so a 1.5 s cadence puts ~13
    ticks in a 20 s window where 1 Hz put 20. Every ``*_min_hits`` is divided
    down by :meth:`Timings.scaled_hits`, and the day must still produce the
    same six triggers and the same episode kinds -- otherwise the gate is
    silently tuned for a stream that does not exist (SPEC §3, §12.2).

    The sim carries no ``watch`` block, so GATE_READS_WATCH must not change
    a thing here: every sustained trigger falls back to today's rule.
    """

    timings = Timings.demo(tick_interval_s=interval_s)
    db = Database(tmp_path / "scenario.db").connect().init_schema()
    episodes = EpisodeBuilder(db, timings, reads_watch=reads_watch)
    escalations = []
    gate = TriggerGate(default_triggers(timings, True, reads_watch=reads_watch), timings, db,
                       episodes, lambda e: escalations.append(e) is None, True)
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


@READS_WATCH
def test_sighting_triggers_stay_reachable_at_the_slow_cadence(tmp_path, reads_watch: bool) -> None:
    """A cup seen once inside the 10 s window is a cup (S9).

    ``caffeine_seen`` is a point observation, two hits at 1 Hz; scaled to 1.5 s
    that floors to one. The window does not scale -- only the count does.
    A point sighting is labeler-only under ``reads_watch`` too: a cold
    ``watch`` block on the tick must not veto it.
    """

    timings = Timings.demo(tick_interval_s=1.5)
    db = Database(tmp_path / "sight.db").connect().init_schema()
    episodes = EpisodeBuilder(db, timings, reads_watch=reads_watch)
    escalations = []
    gate = TriggerGate(default_triggers(timings, True, reads_watch=reads_watch), timings, db,
                       episodes, lambda e: escalations.append(e) is None, True)
    # One positive tick, on a 1.5 s clock, with nothing else in the window.
    current = Tick(
        tick_id="t_0", t=0.0, seq=0,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{0:016x}"),
        ai=AiBlock(age_ms=0, caffeine_visible=True), frame_ref="f_0",
        watch=WatchBlock(scores={"caffeine_visible": 0.1}, hot=[]),
    )
    episodes.on_tick(current)
    gate.on_tick(current)
    assert [e.trigger for e in escalations] == ["caffeine_seen"]
    db.close()


@READS_WATCH
def test_suppressed_trigger_does_not_block_others(tmp_path, reads_watch: bool) -> None:
    """A screen_block already escalated must not stop caffeine_seen firing."""
    db = Database(tmp_path / "s.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo(), reads_watch=reads_watch)
    escalations = []
    gate = TriggerGate(default_triggers(Timings.demo(), True, reads_watch=reads_watch),
                       Timings.demo(), db, episodes, lambda e: escalations.append(e) is None, True)
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


@READS_WATCH
def test_default_triggers_appends_the_biometric_trigger_last(reads_watch: bool) -> None:
    plain = default_triggers(Timings.demo(), True, reads_watch=reads_watch)
    withfeed = default_triggers(Timings.demo(), True, feed=StubFeed(100.0), reads_watch=reads_watch)
    assert [t.name for t in plain] == [t.name for t in withfeed[:-1]]
    assert withfeed[-1].name == "biometric_anomaly"
    assert withfeed[-1].cooldown_s == Timings.demo().biometric_cooldown


# -- reads_watch: the watcher proves how long, the labeler what (PERCEPTION.md phase 3)


#: (trigger, the watcher concept it reads, the ai reading that confirms it today).
SUSTAINED = pytest.mark.parametrize("name, concept, confirm", [
    ("screen_sustained", "screen_present", dict(screen_present=True)),
    ("people_sustained", "people_interacting", dict(people_present=True, people_interacting=True)),
    ("outdoor_sustained", "vegetation_visible", dict(vegetation_visible=True)),
    ("food_in_frame", "food_present", dict(activity="eating")),
])


def hot_window(concept: str, hot: int, total: int = 10, confirm_at: dict[int, dict] | None = None,
               score: float = 0.7) -> list[Tick]:
    """``total`` watch-bearing ticks at 1 Hz, the first ``hot`` of them hot on
    ``concept``; ``confirm_at`` puts a fresh ai reading on the ticks it names."""

    confirm_at = confirm_at or {}
    return [
        watch_tick(i, {concept: score if i < hot else 0.1}, **confirm_at.get(i, {}))
        for i in range(total)
    ]


@SUSTAINED
def test_hot_majority_with_one_confirming_ai_fires(name: str, concept: str, confirm: dict) -> None:
    """70 % of the watch-bearing ticks hot, one confirming ai reading: fires.
    The same window is one fresh hit, far short of today's count, so with the
    switch off it must not."""

    window = hot_window(concept, hot=7, confirm_at={4: confirm})
    assert trigger_named(name, reads_watch=True).predicate(window)
    # (demo food_min_hits is 1, so one eating tick was already enough there)
    assert trigger_named(name, reads_watch=False).predicate(window) == (name == "food_in_frame")


@SUSTAINED
def test_hot_majority_without_a_confirming_ai_does_not_fire(name: str, concept: str, confirm: dict) -> None:
    """The watcher alone is never a §9 boolean: no fresh ai in the window, no fire."""

    assert not trigger_named(name, reads_watch=True).predicate(hot_window(concept, hot=7))
    stale = hot_window(concept, hot=7, confirm_at={4: confirm})
    stale[4].ai.age_ms = 9999  # type: ignore[union-attr]
    assert not trigger_named(name, reads_watch=True).predicate(stale)


@SUSTAINED
def test_confirming_ai_with_a_cold_watcher_does_not_fire(name: str, concept: str, confirm: dict) -> None:
    """Enough labeler hits to fire today, but the watcher saw the concept on
    only 30 % of its ticks: the watcher owns persistence, so no fire."""

    window = hot_window(concept, hot=3, confirm_at={7: confirm, 8: confirm, 9: confirm})
    assert trigger_named(name, reads_watch=False).predicate(window)
    assert not trigger_named(name, reads_watch=True).predicate(window)


@SUSTAINED
def test_a_window_without_watch_falls_back_to_todays_rule(name: str, concept: str, confirm: dict) -> None:
    """Replay and the webcam carry no ``watch``: the switch changes nothing there."""

    enough = [tick(i, **confirm) for i in range(3)]
    short = [tick(i, **confirm) for i in range(3) if i < 1] + [tick(2)]
    for reads_watch in (False, True):
        assert trigger_named(name, reads_watch=reads_watch).predicate(enough)
        assert (trigger_named(name, reads_watch=reads_watch).predicate(short)
                == (name == "food_in_frame"))  # demo food_min_hits is 1


def test_a_tick_without_watch_counts_neither_way() -> None:
    """7 hot of 10 watch-bearing ticks stays 70 % however many blind ticks sit between."""

    window = hot_window("screen_present", hot=7, confirm_at={0: dict(screen_present=True)})
    window[3:3] = [tick(100 + i) for i in range(5)]  # blind ticks, in the same window
    for i, t in enumerate(window):  # keep time monotonic inside the window
        t.t = float(i)
    assert trigger_named("screen_sustained", reads_watch=True).predicate(window)


def test_watch_thresholds_set_the_enter_bar() -> None:
    window = hot_window("screen_present", hot=7, confirm_at={4: dict(screen_present=True)}, score=0.7)
    assert trigger_named("screen_sustained", reads_watch=True,
                         watch_thresholds={"screen_present": (0.65, 0.4)}).predicate(window)
    assert not trigger_named("screen_sustained", reads_watch=True,
                             watch_thresholds={"screen_present": (0.8, 0.5)}).predicate(window)


def test_point_sightings_ignore_the_watcher() -> None:
    """``caffeine_seen`` stays labeler-only: a cold watch block does not veto it."""

    window = [watch_tick(i, {"caffeine_visible": 0.05}, caffeine_visible=True) for i in range(3)]
    for reads_watch in (False, True):
        assert trigger_named("caffeine_seen", reads_watch=reads_watch).predicate(window)


def test_novelty_on_two_consecutive_ticks_fires_change() -> None:
    change = trigger_named("change", reads_watch=True)
    one = [watch_tick(0, novelty=0.1), watch_tick(1, novelty=0.5)]
    assert not change.predicate(one)
    two = one + [watch_tick(2, novelty=0.5)]
    assert change.predicate(two)
    reason, extra = change.enrich(two)  # type: ignore[misc]
    assert "novelty" in reason
    assert extra and "novelty" in extra[0]
    # The existing budget applies: once accepted, the cooldown holds it.
    change.on_fired(2.0)  # type: ignore[misc]
    assert not change.predicate(two + [watch_tick(3, novelty=0.5)])
    # Below enter, or with the switch off, novelty is not an input.
    assert not trigger_named("change", reads_watch=True, novelty_enter=0.6).predicate(two)
    assert not trigger_named("change", reads_watch=False).predicate(two)


def test_novelty_change_reaches_the_gate_with_its_reason(tmp_path) -> None:
    db = Database(tmp_path / "novelty.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo(), reads_watch=True)
    escalations: list[Escalation] = []
    gate = TriggerGate(default_triggers(Timings.demo(), True, reads_watch=True), Timings.demo(),
                       db, episodes, lambda e: escalations.append(e) is None, True)
    for i, novelty in enumerate((0.1, 0.1, 0.5, 0.5)):
        gate.on_tick(watch_tick(i, novelty=novelty))
    assert [e.trigger for e in escalations] == ["change"]
    assert "novelty" in escalations[0].reason
    db.close()


def test_watch_persistence_reaches_the_gate_and_binds_its_episode(tmp_path) -> None:
    """End to end: a hot watcher plus one Gemini confirmation escalates
    ``screen_sustained`` once, and the ``screen_block`` that opens on the
    labeler's own (unchanged) entry rule is bound to it, so the trigger does
    not fire a second time for the same block."""

    db = Database(tmp_path / "persist.db").connect().init_schema()
    episodes = EpisodeBuilder(db, Timings.demo(), reads_watch=True)
    escalations: list[Escalation] = []
    gate = TriggerGate(default_triggers(Timings.demo(), True, reads_watch=True), Timings.demo(),
                       db, episodes, lambda e: escalations.append(e) is None, True)
    # Three Gemini hits open the episode (entry rules unchanged) ...
    for i in range(3):
        current = watch_tick(i, {"screen_present": 0.7}, screen_present=True)
        episodes.on_tick(current)
        gate.on_tick(current)
    block = episodes.open_episodes()["screen_block"]
    # ... then the watcher carries it with no more labeler calls, past the 20 s
    # cooldown but inside the (unchanged) 32 s labeler-silence close.
    for i in range(3, 30):
        current = watch_tick(i, {"screen_present": 0.7})
        episodes.on_tick(current)
        gate.on_tick(current)
    names = [e.trigger for e in escalations]
    assert names.count("screen_sustained") == 1, names
    assert block.id in gate._escalated_episode_ids
    assert "screen_block" in episodes.open_episodes()  # the watcher kept it open
    db.close()


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


def test_a_sustained_trigger_needs_its_hit_count_of_watch_ticks() -> None:
    """One hot watch tick plus one confirmation is not "sustained": the watch rule
    only applies once the window holds the trigger's own hit count of watch-bearing
    ticks; before that today's rule judges the window (and it needs its hits too)."""

    demo = Timings.demo()
    hits = max(1, round(demo.screen_sustained_min_hits / demo.tick_interval_s))
    trigger = next(t for t in default_triggers(demo, True, reads_watch=True)
                   if t.name == "screen_sustained")
    first = [watch_tick(0, {"screen_present": 0.9}, screen_present=True)]
    assert not trigger.predicate(first)
    window = [watch_tick(i, {"screen_present": 0.9}, screen_present=(i == 0)) for i in range(hits)]
    assert trigger.predicate(window)
