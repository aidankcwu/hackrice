"""US-W04: the watcher core -- scoring, novelty, quality gate, hysteresis, wake-ups.

The fake model's cross-concept scores are noisy (temperature 100 over random hash
vectors), so every test restricts `thresholds` to the concepts it is about and uses
prompts whose vectors are clean against every other concept in the bank.
"""

import threading
import time

import numpy as np
import pytest

from longevity import ai_fields
from longevity.watcher import MAX_COOLDOWN_MULTIPLIER, Wakeup, Watcher, WatcherConfig
from longevity.watcher_model import FakeWatcherModel

VEG = "vegetation_visible"
PEOPLE = "people_present"
CAFFEINE = "caffeine_visible"
ENTER, EXIT = 0.6, 0.4
POINT = frozenset({CAFFEINE})


def _vec(prompt: str) -> np.ndarray:
    return FakeWatcherModel().text_bank([prompt])[0]


def _basis(i: int) -> np.ndarray:
    v = np.zeros(64, np.float32)
    v[i] = 1.0
    return v


#: Frame bytes -> forced embedding. "veg" sits on a vegetation prompt that scores
#: nothing else above 0.2; "people" likewise for people_present; "null" is a null
#: prompt (everything under 0.1); e1/e2 are orthogonal, for novelty.
SCRIPTED = {
    b"veg": _vec(ai_fields.WATCH_PROMPTS[VEG][1]),
    b"people": _vec(ai_fields.WATCH_PROMPTS[PEOPLE][0]),
    b"caffeine": _vec(ai_fields.WATCH_PROMPTS[CAFFEINE][0]),
    b"null": _vec(ai_fields.WATCH_NULL_PROMPTS[1]),
    b"e1": _basis(0),
    b"e2": _basis(1),
    b"e3": _basis(2),
}


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(
    thresholds=None, *, k=2, n=3, model=None, clock=None, run_inline=True, **cfg
) -> tuple[Watcher, Clock, FakeWatcherModel]:
    clock = clock or Clock()
    model = model or FakeWatcherModel(scripted=SCRIPTED)
    if thresholds is None:
        thresholds = {VEG: (ENTER, EXIT)}
    # Unrelated hash vectors are ~orthogonal, so any frame change is "novel" to the
    # fake; concept tests switch novelty off (a real novelty never exceeds 2.0).
    cfg.setdefault("novelty_enter", 9.0)
    config = WatcherConfig(k=k, n=n, **cfg)
    w = Watcher(model, config, thresholds, POINT, clock=clock, run_inline=run_inline)
    return w, clock, model


class Feeder:
    """Offers frames with strictly increasing frame_t, independent of the clock."""

    def __init__(self, w: Watcher, step: float = 0.5) -> None:
        self.w = w
        self.t = 0.0
        self.step = step

    def __call__(self, *frames: bytes, sensor=None, dt: float | None = None) -> None:
        for f in frames:
            self.t += self.step if dt is None else dt
            self.w.offer(self.t, f, sensor)


# --- scores and hysteresis ----------------------------------------------------------


def test_scripted_frames_score_as_intended():
    w, _, _ = make({VEG: (ENTER, EXIT), PEOPLE: (ENTER, EXIT)})
    feed = Feeder(w)
    feed(b"veg")
    s = w.take_tick(feed.t)["scores"]
    assert s[VEG] > 0.9 and s[PEOPLE] < 0.2
    feed(b"people")
    s = w.take_tick(feed.t)["scores"]
    assert s[PEOPLE] > 0.9 and s[VEG] < 0.2
    feed(b"null")
    s = w.take_tick(feed.t)["scores"]
    assert all(v < 0.1 for v in s.values())


def test_one_high_frame_does_not_wake():
    w, _, _ = make(k=2, n=3)
    feed = Feeder(w)
    feed(b"veg", b"null", b"null", b"null")
    assert w.take_wakeup() is None
    assert w.hot_concepts() == frozenset()
    assert w.stats()["wakeups"] == 0


def test_k_of_n_wakes_exactly_once_and_take_is_consume_once():
    w, _, _ = make(k=2, n=3)
    feed = Feeder(w)
    feed(b"veg")
    assert w.take_wakeup() is None
    feed(b"veg")
    wake = w.take_wakeup()
    assert wake == Wakeup(frame_t=1.0, concepts=(VEG,), novelty=pytest.approx(wake.novelty), reason="concept")
    assert wake.concepts == (VEG,) and wake.reason == "concept"
    assert w.take_wakeup() is None
    assert w.hot_concepts() == frozenset({VEG})


def test_static_hot_concept_issues_no_second_wakeup():
    w, _, _ = make(k=2, n=3)
    feed = Feeder(w)
    feed(*([b"veg"] * 40))
    assert w.stats()["wakeups"] == 1
    assert w.take_wakeup() is not None
    assert w.take_wakeup() is None
    assert w.take_tick(feed.t)["hot"] == [VEG]


def test_hot_cooling_hot_issues_no_wakeup():
    w, clock, _ = make(k=2, n=3)
    feed = Feeder(w)
    feed(b"veg", b"veg")                      # cold -> hot (wake)
    assert w.take_wakeup() is not None
    feed(b"null", b"null")                    # hot -> cooling
    assert w.hot_concepts() == frozenset({VEG})
    clock.t += 5
    feed(b"veg", b"veg")                      # cooling -> hot, no wake
    assert w.take_wakeup() is None
    assert w.hot_concepts() == frozenset({VEG})
    assert w.stats()["wakeups"] == 1


def test_cooling_to_cold_after_cooldown_then_fresh_hot_wakes_again():
    w, clock, _ = make(k=2, n=3, cooldown_s=30.0)
    feed = Feeder(w)
    feed(b"veg", b"veg", b"null", b"null")    # hot, then cooling
    assert w.take_wakeup().reason == "concept"
    clock.t += 29.0
    assert w.hot_concepts() == frozenset({VEG})
    clock.t += 1.0
    assert w.hot_concepts() == frozenset()   # expired on the clock, no frame needed
    feed(b"veg", b"veg")
    wake = w.take_wakeup()
    assert wake is not None and wake.concepts == (VEG,)
    assert w.stats()["wakeups"] == 2


def test_cooling_concepts_is_the_cooling_subset_of_hot_concepts():
    w, clock, _ = make({VEG: (ENTER, EXIT), PEOPLE: (ENTER, EXIT)}, k=1, n=1, cooldown_s=30.0)
    feed = Feeder(w)
    assert w.cooling_concepts() == frozenset()
    feed(b"veg")                              # VEG hot
    assert w.hot_concepts() == frozenset({VEG}) and w.cooling_concepts() == frozenset()
    feed(b"people")                           # VEG cooling, PEOPLE hot
    assert w.hot_concepts() == frozenset({VEG, PEOPLE})
    assert w.cooling_concepts() == frozenset({VEG})
    clock.t += 30.0                           # VEG cold on the clock, no frame needed
    assert w.cooling_concepts() == frozenset()
    assert w.hot_concepts() == frozenset({PEOPLE})


def test_windows_restart_at_a_transition_so_one_of_n_cannot_flap():
    w, _, _ = make(k=1, n=3)
    feed = Feeder(w)
    feed(b"veg")                              # hot on one frame
    assert w.take_wakeup() is not None
    feed(b"null")                             # the single above frame must not also count as re-entry
    assert w.hot_concepts() == frozenset({VEG})
    assert w.stats()["wakeups"] == 1


def test_point_concepts_follow_the_same_machine_and_are_flagged():
    w, _, _ = make({CAFFEINE: (ENTER, EXIT)}, k=2, n=3)
    assert w.is_point(CAFFEINE) and not w.is_point(VEG)
    feed = Feeder(w)
    feed(b"caffeine", b"caffeine", b"caffeine")
    assert w.take_wakeup().concepts == (CAFFEINE,)
    assert w.hot_concepts() == frozenset({CAFFEINE})


def test_concepts_without_thresholds_never_go_hot_but_are_still_scored():
    w, _, _ = make({}, k=1, n=1)
    feed = Feeder(w)
    feed(b"veg", b"veg")
    assert w.take_wakeup() is None
    block = w.take_tick(feed.t)
    assert block["scores"][VEG] > 0.9 and block["hot"] == []


# --- novelty ------------------------------------------------------------------------


def test_first_usable_frame_has_zero_novelty():
    w, _, _ = make({}, k=1, n=1, novelty_enter=0.35)
    feed = Feeder(w)
    feed(b"e1")
    assert w.take_tick(feed.t)["novelty"] == 0.0
    assert w.take_wakeup() is None


def test_novelty_wake_and_rearm_rule():
    w, _, _ = make({}, k=1, n=1, novelty_enter=0.35, novelty_ema_s=30.0)
    feed = Feeder(w)
    feed(b"e1")                               # seeds the mean
    feed(b"e2")                               # orthogonal: novelty 1.0
    wake = w.take_wakeup()
    assert wake is not None and wake.reason == "novelty" and wake.concepts == ()
    assert wake.novelty == pytest.approx(1.0, abs=1e-5)
    assert w.take_tick(feed.t)["woke"] == "novelty"
    feed(b"e2")                               # mean still ~e1 (dt << tau): novel again, but not re-armed
    assert w.take_wakeup() is None
    feed(b"e2", dt=1000.0)                    # dt >> tau: measured against ~e1, then the mean snaps to e2
    feed(b"e2")                               # novelty ~0 -> re-arms
    assert w.take_wakeup() is None
    feed(b"e3")                               # novel again -> a second novelty wake
    wake = w.take_wakeup()
    assert wake is not None and wake.reason == "novelty"
    assert w.stats()["wakeups_by_reason"] == {"novelty": 2}


def test_single_novel_frame_does_not_wake_under_k_of_n():
    w, _, _ = make({}, k=2, n=3, novelty_enter=0.35)
    feed = Feeder(w)
    feed(b"e1", b"e2")
    assert w.take_wakeup() is None
    feed(b"e2")
    assert w.take_wakeup().reason == "novelty"


# --- quality gate -------------------------------------------------------------------


def test_quality_gate_excludes_frames_from_usable_scores_and_wakeups():
    w, _, model = make(k=1, n=1, min_sharpness=20.0, min_lux=10.0)
    feed = Feeder(w)
    feed(b"veg", sensor={"sharpness": 5.0, "lux_proxy": 200.0})
    feed(b"veg", sensor={"sharpness": 100.0, "lux_proxy": 2.0})
    assert model.calls == 0
    assert w.take_wakeup() is None
    block = w.take_tick(feed.t)
    assert block["frames"] == 2 and block["usable"] == 0
    assert block["scores"] == {} and block["novelty"] == 0.0 and block["woke"] is None
    feed(b"veg", sensor={"sharpness": 100.0, "lux_proxy": 200.0})
    assert model.calls == 1
    assert w.take_wakeup().concepts == (VEG,)
    block = w.take_tick(feed.t)
    assert block["frames"] == 1 and block["usable"] == 1


def test_gated_frames_do_not_seed_or_move_the_novelty_mean():
    w, _, _ = make({}, k=1, n=1, novelty_enter=0.35)
    feed = Feeder(w)
    feed(b"e2", sensor={"sharpness": 0.0})    # gated: must not seed the mean
    feed(b"e1")                               # seeds it
    feed(b"e1", sensor={"sharpness": 0.0})    # gated
    feed(b"e2")
    assert w.take_wakeup().reason == "novelty"


# --- coalescing and the tick --------------------------------------------------------


def test_two_concepts_in_one_tick_coalesce_into_one_wakeup():
    w, _, _ = make({VEG: (ENTER, EXIT), PEOPLE: (ENTER, EXIT)}, k=1, n=1)
    feed = Feeder(w)
    feed(b"veg", b"people")
    block = w.take_tick(feed.t)
    assert block["woke"] == VEG
    assert block["hot"] == sorted([VEG, PEOPLE])
    wake = w.take_wakeup()
    assert wake.concepts == (VEG, PEOPLE) and wake.reason == "concept"
    assert wake.frame_t == feed.t
    assert w.stats()["wakeups"] == 2


def test_take_tick_names_but_does_not_consume_the_wakeup():
    w, _, _ = make(k=1, n=1)
    feed = Feeder(w)
    feed(b"veg")
    assert w.take_tick(feed.t)["woke"] == VEG
    assert w.take_wakeup().concepts == (VEG,)
    assert w.take_tick(feed.t)["woke"] is None


def test_a_wakeup_belongs_to_one_tick_only():
    w, _, _ = make({VEG: (ENTER, EXIT), PEOPLE: (ENTER, EXIT)}, k=1, n=1)
    feed = Feeder(w)
    feed(b"veg")
    assert w.take_tick(feed.t)["woke"] == VEG   # tick 1 named it; the loop has not consumed it yet
    feed(b"people")
    assert w.take_tick(feed.t)["woke"] == PEOPLE
    wake = w.take_wakeup()
    assert wake.concepts == (PEOPLE,)            # a new wake-up, not tick 1's with people appended


def test_take_tick_shape_empty_and_nonempty():
    w, _, _ = make(k=1, n=1)
    empty = w.take_tick(0.0)
    assert empty == {
        "v": 1, "model": "fake", "frames": 0, "usable": 0,
        "scores": {}, "novelty": 0.0, "hot": [], "woke": None,
    }
    feed = Feeder(w)
    feed(b"null", b"veg", b"e1")
    block = w.take_tick(feed.t)
    assert set(block) == {"v", "model", "frames", "usable", "scores", "novelty", "hot", "woke"}
    assert block["frames"] == 3 and block["usable"] == 3
    assert set(block["scores"]) == set(ai_fields.WATCH_PROMPTS)
    assert block["scores"][VEG] > 0.9                 # max over the tick's frames
    assert 0.0 < block["novelty"] <= 2.0
    assert block["hot"] == [VEG] and block["woke"] == VEG
    assert isinstance(block["novelty"], float)
    again = w.take_tick(feed.t)
    assert again["frames"] == 0 and again["scores"] == {} and again["novelty"] == 0.0
    assert again["hot"] == [VEG] and again["woke"] is None


# --- armed conditions ---------------------------------------------------------------


def test_armed_match_wakes_with_watch_id_and_removes_the_arming():
    w, clock, _ = make({PEOPLE: (ENTER, EXIT)}, k=3, n=3)
    w.arm(PEOPLE, within_s=60.0, watch_id="w1")
    assert w.stats()["armed"] == 1
    feed = Feeder(w)
    feed(b"null")
    assert w.take_wakeup() is None
    feed(b"people")                           # one frame is enough for an armed watch
    wake = w.take_wakeup()
    assert wake.reason == "watch_armed:w1" and wake.concepts == (PEOPLE,)
    assert w.stats()["armed"] == 0
    assert w.stats()["wakeups_by_reason"] == {"watch_armed": 1}
    feed(b"people")
    assert w.take_wakeup() is None            # gone: no second armed wake


def test_armed_expiry_is_silent_and_counted():
    w, clock, _ = make({PEOPLE: (ENTER, EXIT)}, k=3, n=3)
    w.arm(PEOPLE, within_s=10.0, watch_id="w1")
    clock.t += 11.0
    feed = Feeder(w)
    feed(b"people")
    assert w.take_wakeup() is None
    s = w.stats()
    assert s["armed"] == 0 and s["armed_expired"] == 1


def test_arm_evicts_the_oldest_past_max_armed_and_disarm_removes():
    w, _, _ = make({PEOPLE: (ENTER, EXIT)}, k=3, n=3, max_armed=2)
    w.arm(PEOPLE, 60.0, "a")
    w.arm(PEOPLE, 60.0, "b")
    w.arm(PEOPLE, 60.0, "c")                  # evicts "a"
    assert w.stats()["armed"] == 2
    w.disarm("b")
    assert w.stats()["armed"] == 1
    feed = Feeder(w)
    feed(b"people")
    assert w.take_wakeup().reason == "watch_armed:c"
    w.disarm("nope")                          # unknown id is a no-op


def test_rearming_the_same_id_replaces_it():
    w, _, _ = make({PEOPLE: (ENTER, EXIT)}, k=3, n=3, max_armed=8)
    w.arm(PEOPLE, 60.0, "a")
    w.arm(PEOPLE, 60.0, "a")
    assert w.stats()["armed"] == 1


# --- unconfirmed-wake backoff -------------------------------------------------------


def test_mark_unconfirmed_doubles_the_cooldown_and_confirmed_resets():
    w, clock, _ = make(k=1, n=1, cooldown_s=30.0)
    assert w.cooldown_of(VEG) == 30.0
    w.mark_unconfirmed(VEG)
    assert w.cooldown_of(VEG) == 60.0
    w.mark_unconfirmed(VEG)
    assert w.cooldown_of(VEG) == 120.0
    w.mark_unconfirmed(VEG)
    assert w.cooldown_of(VEG) == MAX_COOLDOWN_MULTIPLIER * 30.0   # capped
    w.mark_confirmed(VEG)
    assert w.cooldown_of(VEG) == 30.0


def test_cooling_to_cold_uses_the_backed_off_cooldown():
    w, clock, _ = make(k=1, n=1, cooldown_s=30.0)
    feed = Feeder(w)
    feed(b"veg")
    assert w.take_wakeup() is not None
    w.mark_unconfirmed(VEG)                   # the labeler saw nothing: 60 s now
    feed(b"null")                             # hot -> cooling
    clock.t += 45.0
    assert w.hot_concepts() == frozenset({VEG})
    clock.t += 15.0
    assert w.hot_concepts() == frozenset()
    feed(b"veg")
    assert w.take_wakeup() is not None
    assert w.stats()["wakeups"] == 2


# --- on_wakeup, stats, threading ----------------------------------------------------


def test_on_wakeup_is_invoked_with_the_coalesced_wakeup():
    w, _, _ = make({VEG: (ENTER, EXIT), PEOPLE: (ENTER, EXIT)}, k=1, n=1)
    seen: list[Wakeup] = []
    w.on_wakeup = lambda wake: seen.append(wake)
    feed = Feeder(w)
    feed(b"null", b"veg", b"veg", b"people")
    assert [s.concepts for s in seen] == [(VEG,), (VEG, PEOPLE)]
    assert seen[-1] == w.take_wakeup()


def test_on_wakeup_listener_may_call_back_in_and_may_fail():
    w, _, _ = make(k=1, n=1)
    w.on_wakeup = lambda wake: (w.take_wakeup(), w.hot_concepts())   # would deadlock if called under the lock
    feed = Feeder(w)
    feed(b"veg")
    assert w.take_wakeup() is None            # the listener already consumed it
    w.on_wakeup = lambda wake: 1 / 0
    feed(b"null", b"null")
    w.mark_confirmed(VEG)
    feed(b"veg", b"veg")                      # cooling -> hot: no wake, no call
    w_clock = w  # noqa: F841


def test_stats_shape():
    w, _, _ = make(k=1, n=1)
    feed = Feeder(w)
    feed(b"veg", b"null", sensor=None)
    feed(b"veg", sensor={"sharpness": 0.0})
    s = w.stats()
    for key in ("frames", "usable", "dropped", "wakeups", "wakeups_by_reason", "armed",
                "armed_expired", "inference_ms_p50", "inference_ms_p99", "hot"):
        assert key in s, key
    assert s["frames"] == 3 and s["usable"] == 2 and s["dropped"] == 0
    assert s["wakeups"] == 1 and s["wakeups_by_reason"] == {"concept": 1}
    assert s["hot"] == [VEG]
    assert s["inference_ms_p50"] >= 0.0 and s["inference_ms_p99"] >= s["inference_ms_p50"]


class SlowModel(FakeWatcherModel):
    def __init__(self, delay: float) -> None:
        super().__init__(scripted=SCRIPTED)
        self.delay = delay
        self.started = threading.Event()

    def embed(self, jpeg: bytes) -> np.ndarray:
        self.started.set()
        time.sleep(self.delay)
        return super().embed(jpeg)


def _wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


async def test_threaded_path_drops_frames_rather_than_queueing():
    model = SlowModel(delay=0.05)
    w, _, _ = make(k=1, n=1, model=model, run_inline=False)
    assert model.calls == 0
    await w.start()
    try:
        # offer never blocks and never touches the model
        started = time.perf_counter()
        for i in range(20):
            w.offer(float(i), b"veg")
        assert time.perf_counter() - started < 0.04
        assert model.calls <= 1
        _wait_until(lambda: w.stats()["frames"] + w.stats()["dropped"] == 20)
        s = w.stats()
        assert s["dropped"] >= 15 and s["frames"] == 20 - s["dropped"]
        assert model.calls == s["frames"]
        assert w.take_wakeup().concepts == (VEG,)
        block = w.take_tick(20.0)
        assert block["frames"] == s["frames"] and block["woke"] == VEG
    finally:
        await w.aclose()
    assert w._thread is None


async def test_threaded_worker_survives_a_failing_embed():
    class Broken(FakeWatcherModel):
        def embed(self, jpeg: bytes) -> np.ndarray:
            self.calls += 1
            if jpeg == b"boom":
                raise RuntimeError("bad jpeg")
            return super().embed(jpeg)

    model = Broken(scripted=SCRIPTED)
    w, _, _ = make(k=1, n=1, model=model, run_inline=False)
    await w.start()
    try:
        w.offer(0.0, b"boom")
        _wait_until(lambda: w.stats()["errors"] == 1)
        w.offer(1.0, b"veg")
        _wait_until(lambda: w.stats()["wakeups"] == 1)
    finally:
        await w.aclose()


def test_offer_before_start_is_dropped_silently_in_threaded_mode():
    w, _, model = make(run_inline=False)
    w.offer(0.0, b"veg")
    assert model.calls == 0 and w.stats()["frames"] == 0
    w.stop()                                  # never started: still safe


def test_bank_is_built_once_on_construction():
    class Counting(FakeWatcherModel):
        banks = 0

        def text_bank(self, prompts):
            type(self).banks += 1
            return super().text_bank(prompts)

    make(model=Counting(scripted=SCRIPTED))
    assert Counting.banks == 1
