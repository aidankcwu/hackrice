"""The labeler runs on wake-ups, in hot mode, on a heartbeat and on a targeted look
(docs/PERCEPTION.md "Labeler", "Gate and actions"), not on every tick.

Each test pins one piece of US-W07: the priority mailbox, the per-kind counters, the
hourly cap, the error back-off, the scheduler's cadence in every concept state, the
point-sighting rule, and the look plumbing whose `answer` never reaches `coerce()`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from longevity import vlm
from longevity.ai_fields import FIELD_ORDER, coerce, look_suffix, response_schema


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class GatedClient:
    """Calls finish only when the test says so; records the question of a look."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Future[dict[str, Any]]] = []
        self.jpegs: list[bytes] = []
        self.questions: list[str | None] = []

    async def tag(self, jpeg: bytes, *, question: str | None = None) -> dict[str, Any]:
        self.jpegs.append(jpeg)
        self.questions.append(question)
        gate: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.gates.append(gate)
        return await gate

    def finish(self, i: int, **fields: Any) -> None:
        self.gates[i].set_result({"scene": "office", "conf": 0.9, **fields})

    async def aclose(self) -> None:
        return None


class FlakyClient:
    """Fails the first `failures` calls, then answers."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def tag(self, jpeg: bytes, *, question: str | None = None) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("boom")
        return {"scene": "office"}

    async def aclose(self) -> None:
        return None


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def make_tagger(client: Any, clock: FakeClock | None = None, **kw: Any) -> vlm.T0Tagger:
    return vlm.T0Tagger(client, budget_s=0.01, log_every=0, clock=clock or FakeClock(), **kw)


# -- the priority mailbox -----------------------------------------------------


async def test_a_wake_replaces_a_pending_hot_and_nothing_lower_replaces_the_wake() -> None:
    client = GatedClient()
    tagger = make_tagger(client)
    await tagger.start()
    tagger.request("hot", 1.0, b"a")
    await settle()
    tagger.request("hot", 2.0, b"b")
    await settle()
    # Both slots busy: the mailbox arbitrates.
    tagger.request("hot", 3.0, b"c")        # pending
    tagger.request("wake", 4.0, b"d")       # displaces c
    tagger.request("hot", 5.0, b"e")        # dropped under the wake
    tagger.request("heartbeat", 6.0, b"f")  # dropped under the wake
    await settle()
    assert tagger._slot.pending_kind == "wake"

    client.finish(0)
    await settle()
    assert client.jpegs == [b"a", b"b", b"d"]
    assert tagger._slot.dropped == 3
    assert tagger.by_kind["hot"] == {
        "requested": 4, "started": 2, "returned": 1, "landed": 1, "dropped": 2,
    }
    assert tagger.by_kind["wake"] == {
        "requested": 1, "started": 1, "returned": 0, "landed": 0, "dropped": 0,
    }
    assert tagger.by_kind["heartbeat"]["dropped"] == 1
    assert tagger.stats()["by_kind"]["hot"]["dropped"] == 2
    await tagger.aclose()


async def test_same_kind_newest_wins_and_a_look_sits_between_wake_and_hot() -> None:
    client = GatedClient()
    tagger = make_tagger(client)
    await tagger.start()
    tagger.request("hot", 1.0, b"a")
    await settle()
    tagger.request("hot", 2.0, b"b")
    await settle()
    tagger.request("wake", 3.0, b"w1")
    tagger.request("wake", 4.0, b"w2")   # same kind: newest wins
    await settle()
    assert tagger._slot.pending_kind == "wake" and tagger.by_kind["wake"]["dropped"] == 1
    client.finish(0)
    await settle()
    assert client.jpegs[-1] == b"w2"

    tagger.request("hot", 5.0, b"h")
    tagger.request("look", 6.0, b"l", question="what is on the plate?")  # beats hot
    tagger.request("hot", 7.0, b"h2")                                    # dropped
    await settle()
    assert tagger._slot.pending_kind == "look"
    client.finish(1)
    await settle()
    assert client.jpegs[-1] == b"l" and client.questions[-1] == "what is on the plate?"
    assert client.questions[:-1] == [None, None, None]  # no keyword for the others
    await tagger.aclose()


async def test_offer_is_the_hot_alias_and_bad_requests_are_refused() -> None:
    tagger = make_tagger(vlm.FakeClient(latency=0.0))
    tagger.offer(1.0, b"a")
    assert tagger.by_kind["hot"]["requested"] == 1
    with pytest.raises(ValueError):
        tagger.request("bogus", 1.0, b"a")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        tagger.request("hot", 1.0, b"a", question="not a look")
    # No client: nothing is counted, nothing is pending, nothing raises.
    off = vlm.T0Tagger(None, log_every=0)
    off.request("wake", 1.0, b"a")
    assert off.by_kind["wake"]["requested"] == 0 and off.take() is None


# -- the hourly cap ------------------------------------------------------------


async def test_at_the_cap_hot_is_dropped_but_wake_and_heartbeat_are_served() -> None:
    client = vlm.FakeClient(latency=0.0)
    clock = FakeClock()
    tagger = make_tagger(client, clock, max_per_hour=2)
    await tagger.start()
    for t in (1.0, 2.0):
        tagger.request("hot", t, b"x")
        await settle()
    assert client.calls == 2
    assert tagger.scheduler.capped(clock()) and tagger.stats()["scheduler"]["capped"]

    tagger.request("hot", 3.0, b"x")
    await settle()
    assert client.calls == 2
    assert tagger.dropped_capped == 1 and tagger.stats()["dropped_capped"] == 1
    assert tagger.by_kind["hot"]["dropped"] == 1

    tagger.request("wake", 4.0, b"x")
    await settle()
    assert client.calls == 3
    tagger.request("heartbeat", 5.0, b"x")
    await settle()
    assert client.calls == 4
    assert tagger.stats()["scheduler"]["calls_last_hour"] == 4

    # The window rolls: an hour on, hot mode is back.
    clock.advance(3601)
    assert not tagger.scheduler.capped(clock())
    tagger.request("hot", 6.0, b"x")
    await settle()
    assert client.calls == 5 and tagger.dropped_capped == 1
    await tagger.aclose()


# -- error back-off ------------------------------------------------------------


async def test_consecutive_errors_back_off_the_heartbeat_until_a_success() -> None:
    tagger = make_tagger(
        FlakyClient(2), error_backoff_n=2, heartbeat_s=60.0, dormant_heartbeat_s=300.0,
    )
    await tagger.start()
    tagger.request("hot", 1.0, b"x")
    await settle()
    assert tagger.scheduler.errors_in_a_row == 1
    assert tagger.scheduler.heartbeat_interval() == 60.0

    tagger.request("hot", 2.0, b"x")
    await settle()
    assert tagger.scheduler.heartbeat_interval() == 300.0
    assert tagger.stats()["scheduler"]["errors_in_a_row"] == 2
    assert tagger.stats()["scheduler"]["heartbeat_s"] == 300.0
    assert tagger._outcomes[-1] == ("error", "hot")

    tagger.request("wake", 3.0, b"x")  # wake-ups still try, and this one succeeds
    await settle()
    assert tagger.scheduler.errors_in_a_row == 0
    assert tagger.scheduler.heartbeat_interval() == 60.0
    assert tagger._outcomes[-1] == ("returned", "wake")
    await tagger.aclose()


async def test_a_ceiling_timeout_counts_as_an_error_for_the_back_off() -> None:
    tagger = vlm.T0Tagger(
        vlm.FakeClient(latency=5.0), budget_s=0.01, ceiling_s=0.02, log_every=0,
        error_backoff_n=1,
    )
    await tagger.start()
    tagger.request("hot", 1.0, b"x")
    await asyncio.sleep(0.1)
    assert tagger.timeouts == 1 and tagger.scheduler.errors_in_a_row == 1
    assert tagger._outcomes[-1] == ("timeout", "hot")
    await tagger.aclose()


# -- the look plumbing ---------------------------------------------------------


async def test_a_look_carries_the_question_and_the_answer_is_split_out_of_the_fields() -> None:
    client = vlm.FakeClient(latency=0.0, answer="yes, the cup is empty")
    tagger = make_tagger(client)
    await tagger.start()
    tagger.request("look", 1.0, b"x", question="Is the cup empty?")
    await settle()
    assert client.questions == ["Is the cup empty?"]

    claimed = tagger.take(now=1.5)
    assert claimed is not None
    fields, frame_t = claimed
    assert frame_t == 1.0
    assert fields["_look_answer"] == "yes, the cup is empty"
    assert "answer" not in fields  # popped before coerce, which never saw it

    clean, answer = vlm.split_look_answer(fields)
    assert answer == "yes, the cup is empty"
    assert "answer" not in clean and "_look_answer" not in clean
    assert list(clean) == FIELD_ORDER  # the ai block is exactly coerce's output
    assert clean == coerce({"scene": "office", "activity": "seated", "conf": 0.9})
    assert "_look_answer" in fields  # split never mutates its input

    assert tagger.by_kind["look"] == {
        "requested": 1, "started": 1, "returned": 1, "landed": 1, "dropped": 0,
    }
    assert tagger._outcomes[-1] == ("returned", "look")
    await tagger.aclose()


async def test_split_is_a_no_op_without_an_answer_and_the_key_is_configurable() -> None:
    fields = coerce({"scene": "cafe"})
    same, answer = vlm.split_look_answer(fields)
    assert same is fields and answer is None

    client = vlm.FakeClient(latency=0.0, answer="x" * 500)
    tagger = make_tagger(client, look_answer_key="__q")
    await tagger.start()
    tagger.request("look", 1.0, b"x", question="q?")
    await settle()
    fields, _ = tagger.take(now=1.5)  # type: ignore[misc]
    assert "_look_answer" not in fields
    assert len(fields["__q"]) == 200  # bounded by LOOK_ANSWER_MAX_CHARS
    clean, answer = vlm.split_look_answer(fields, key="__q")
    assert answer == "x" * 200 and "__q" not in clean
    await tagger.aclose()


async def test_a_non_look_result_never_carries_the_look_key() -> None:
    tagger = make_tagger(vlm.FakeClient(latency=0.0))
    await tagger.start()
    tagger.request("wake", 1.0, b"x")
    await settle()
    fields, _ = tagger.take(now=1.5)  # type: ignore[misc]
    assert "_look_answer" not in fields and list(fields) == FIELD_ORDER
    await tagger.aclose()


def test_the_look_schema_adds_answer_and_the_default_schema_does_not() -> None:
    default = response_schema()
    assert "answer" not in default["properties"]
    assert default["required"] == FIELD_ORDER and default["propertyOrdering"] == FIELD_ORDER

    look = response_schema(extra_answer=True)
    assert look["properties"]["answer"] == {"type": "STRING", "maxLength": 200}
    assert look["required"] == FIELD_ORDER + ["answer"]
    assert look["propertyOrdering"][-1] == "answer"
    assert {k: v for k, v in look["properties"].items() if k != "answer"} == default["properties"]


def test_look_suffix_names_the_question_and_bounds_it() -> None:
    suffix = look_suffix("  Is the   cup empty? ")
    assert suffix.startswith("\n\n") and suffix.endswith("Is the cup empty?")
    assert "answer" in suffix and "200" in suffix
    assert len(look_suffix("q" * 1000)) < 700


# -- the scheduler -------------------------------------------------------------

TICK = 1.5
POINT = frozenset({"caffeine_visible"})


def make_sched(**kw: Any) -> vlm.LabelerScheduler:
    return vlm.LabelerScheduler(tick_interval_s=TICK, point_concepts=POINT, **kw)


def run(sched: vlm.LabelerScheduler, seconds: float, state: Any) -> list[tuple[float, str | None]]:
    out = []
    for i in range(int(seconds / TICK)):
        now = i * TICK
        hot, cooling, novelty = state(now)
        out.append((now, sched.on_tick(now, hot, cooling, novelty)))
    return out


def kinds(out: list[tuple[float, str | None]], lo: float, hi: float, kind: str) -> list[float]:
    return [now for now, k in out if lo <= now < hi and k == kind]


def test_transition_runs_every_tick_then_steady_hot_every_steady_s() -> None:
    sched = make_sched()
    out = run(sched, 300, lambda now: (frozenset({"screen_present"}), frozenset(), 0.5))
    assert kinds(out, 0, 60, "hot") == [i * TICK for i in range(40)]
    assert sched.mode == "steady"
    steady = kinds(out, 60, 300, "hot")
    gaps = [b - a for a, b in zip(steady, steady[1:])]
    # 10 s is not a multiple of the tick: the next tick at or past 10 s, i.e. 10.5 s.
    assert gaps and all(g == pytest.approx(10.5) for g in gaps)
    assert len(steady) == 22
    assert kinds(out, 0, 300, "heartbeat") == []


def test_a_point_concept_gets_its_transition_and_then_no_steady_requests() -> None:
    sched = make_sched()
    out = run(sched, 300, lambda now: (POINT, frozenset(), 0.5))
    assert len(kinds(out, 0, 60, "hot")) == 40  # the 40-call transition, once
    assert kinds(out, 60, 300, "hot") == []
    assert sched.mode == "spent"
    # Spent is idle: the heartbeat keeps running at its normal cadence.
    assert kinds(out, 60, 300, "heartbeat") == [118.5, 178.5, 238.5, 298.5]


def test_cooling_runs_every_tick_for_cooling_s_then_cold_heartbeats() -> None:
    sched = make_sched()
    out = run(sched, 300, lambda now: (
        frozenset(), frozenset({"screen_present"}) if now < 30 else frozenset(), 0.5,
    ))
    assert kinds(out, 0, 30, "hot") == [i * TICK for i in range(20)]
    assert kinds(out, 30, 300, "hot") == []
    assert kinds(out, 30, 300, "heartbeat") == [88.5, 148.5, 208.5, 268.5]
    assert sched.mode == "cold"


def test_cold_is_one_heartbeat_per_heartbeat_s() -> None:
    sched = make_sched()
    out = run(sched, 300, lambda now: (frozenset(), frozenset(), 0.5))
    assert kinds(out, 0, 300, "hot") == []
    assert kinds(out, 0, 300, "heartbeat") == [0.0, 60.0, 120.0, 180.0, 240.0]
    assert sched.stats() == {
        "mode": "cold", "capped": False, "errors_in_a_row": 0,
        "calls_last_hour": 0, "max_per_hour": 600, "heartbeat_s": 60.0,
    }


def test_flat_novelty_goes_dormant_and_any_novelty_ends_it() -> None:
    sched = make_sched()
    out = run(sched, 900, lambda now: (frozenset(), frozenset(), 0.0))
    assert kinds(out, 0, 300, "heartbeat") == [0.0, 60.0, 120.0, 180.0, 240.0]
    assert kinds(out, 300, 900, "heartbeat") == [540.0, 840.0]
    assert sched.mode == "dormant"
    assert sched.on_tick(900.0, frozenset(), frozenset(), 0.9) == "heartbeat"
    assert sched.mode == "cold"


def test_a_cooling_concept_is_not_also_hot() -> None:
    sched = make_sched()
    both = frozenset({"screen_present"})
    assert all(sched.on_tick(i * TICK, both, both, 0.5) == "hot" for i in range(20))
    assert sched.mode == "cooling"
    # Window over: not hot (it is cooling), not cooling (past cooling_s) -> idle.
    assert sched.on_tick(30.0, both, both, 0.5) is None and sched.mode == "cold"
    assert sched.on_tick(88.5, both, both, 0.5) == "heartbeat"


def test_the_cap_turns_hot_mode_off_and_leaves_the_heartbeat() -> None:
    sched = make_sched(max_per_hour=3)
    for _ in range(3):
        sched.note_started("hot", 0.0)
    assert sched.capped(0.0) and sched.calls_last_hour(0.0) == 3
    hot = frozenset({"screen_present"})
    assert sched.on_tick(0.0, hot, frozenset(), 0.5) == "heartbeat"
    assert sched.stats()["capped"] and sched.mode == "transition"
    assert all(sched.on_tick(i * TICK, hot, frozenset(), 0.5) is None for i in range(1, 40))
    assert sched.on_tick(60.0, hot, frozenset(), 0.5) == "heartbeat"
    # `clock` is the wall time the cap is measured on when it differs from `now`.
    # Uncapped again the concept is steady hot: due 10 s after the last call.
    assert sched.on_tick(61.5, hot, frozenset(), 0.5, clock=3601.0) is None
    assert not sched.capped(3601.0) and sched.mode == "steady"
    assert sched.on_tick(70.0, hot, frozenset(), 0.5, clock=3601.0) == "hot"


def test_error_back_off_stretches_the_heartbeat_until_a_success() -> None:
    sched = make_sched(error_backoff_n=5)
    assert sched.on_tick(0.0, frozenset(), frozenset(), 0.5) == "heartbeat"
    for _ in range(5):
        sched.note_error()
    assert sched.heartbeat_interval() == 300.0 and sched.errors_in_a_row == 5
    assert sched.on_tick(60.0, frozenset(), frozenset(), 0.5) is None
    assert sched.on_tick(300.0, frozenset(), frozenset(), 0.5) == "heartbeat"
    sched.note_success()
    assert sched.heartbeat_interval() == 60.0
    assert sched.on_tick(360.0, frozenset(), frozenset(), 0.5) == "heartbeat"


def test_the_tagger_builds_its_scheduler_from_the_same_keywords_or_takes_one() -> None:
    tagger = vlm.T0Tagger(
        vlm.FakeClient(latency=0.0), budget_s=1.5, log_every=0,
        heartbeat_s=30.0, max_per_hour=7, point_concepts=POINT,
    )
    s = tagger.scheduler
    assert (s.tick_interval_s, s.heartbeat_s, s.max_per_hour) == (1.5, 30.0, 7)
    assert s._point == POINT
    own = make_sched()
    assert vlm.T0Tagger(vlm.FakeClient(), log_every=0, scheduler=own).scheduler is own
    assert "scheduler" in tagger.stats() and "mode=" in tagger.stats_line()
