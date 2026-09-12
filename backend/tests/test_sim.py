from __future__ import annotations

import asyncio
import time

import pytest

from pipeline.frames import InMemoryFrameStore
from pipeline.models import Tick, phash_distance
from pipeline.sim import DEFAULT_SCENARIO, SimSource
from pipeline.sim.scenario import Scenario, Segment


# -- scenario ------------------------------------------------------------


def test_default_scenario_is_about_six_minutes() -> None:
    assert 330 <= DEFAULT_SCENARIO.total_duration_s <= 420
    assert len(DEFAULT_SCENARIO) == 9


def test_default_scenario_covers_the_demo_metric_set() -> None:
    """SPEC §10: nature, social, screen, meal + caffeine cutoff, alcohol."""

    segs = list(DEFAULT_SCENARIO)
    assert any(s.vegetation_visible for s in segs)
    assert any(s.people_present for s in segs)
    assert any(s.screen_present for s in segs)
    assert any(s.food_present and s.food_type == "rice_bowl" for s in segs)
    assert any(s.caffeine_visible for s in segs)
    assert any(s.alcohol_visible for s in segs)


def test_segment_at_walks_and_loops() -> None:
    idx, seg, off = DEFAULT_SCENARIO.segment_at(0)
    assert idx == 0 and seg.name == "office_screen" and off == 0

    idx, seg, off = DEFAULT_SCENARIO.segment_at(70)
    assert seg.name == "desk_coffee" and off == pytest.approx(10)

    total = DEFAULT_SCENARIO.total_duration_s
    assert DEFAULT_SCENARIO.segment_at(total + 5)[1].name == "office_screen"


def test_zero_duration_scenario_rejected() -> None:
    empty = Scenario(name="empty", segments=[])
    with pytest.raises(ValueError):
        empty.segment_at(0)


# -- source --------------------------------------------------------------


def collect(n: int, **kwargs) -> list[Tick]:
    src = SimSource(DEFAULT_SCENARIO, **kwargs)
    return [src.next_tick() for _ in range(n)]


def test_ticks_are_valid_and_sequential() -> None:
    ticks = collect(30)
    assert [t.seq for t in ticks] == list(range(30))
    assert [t.tick_id for t in ticks] == [f"t_{i:08d}" for i in range(30)]
    assert [t.frame_ref for t in ticks] == [f"f_{i:08d}" for i in range(30)]
    for t in ticks:
        assert t.v == 1
        assert t.device is not None  # sim always ships the phone sensor block
        assert len(t.sensor.phash) == 16
        assert t.t > 1_700_000_000  # real wall clock


def test_timestamps_are_monotonic_simulated_clock() -> None:
    before = time.time()
    ticks = collect(10)
    # Simulated clock: starts at wall-clock launch time, then +1.0 s per tick.
    assert before <= ticks[0].t <= time.time() + 1.0
    assert all(b.t - a.t == 1.0 for a, b in zip(ticks, ticks[1:]))


def test_ai_coverage_is_roughly_as_configured() -> None:
    """SPEC §2.4: expect 0.5-0.8 Hz of AI-populated ticks."""

    ticks = collect(1000, ai_coverage=0.65, seed=7)
    covered = sum(1 for t in ticks if t.ai is not None)
    assert 0.60 <= covered / len(ticks) <= 0.70
    assert any(t.ai is None for t in ticks), "gaps must exist (SPEC §12.2)"


@pytest.mark.parametrize("coverage", [0.0, 1.0])
def test_ai_coverage_extremes(coverage: float) -> None:
    ticks = collect(50, ai_coverage=coverage, seed=3)
    covered = sum(1 for t in ticks if t.ai is not None)
    assert covered == (0 if coverage == 0.0 else 50)


def test_age_ms_measures_gap_since_last_ai_block() -> None:
    ticks = collect(200, seed=1)
    ai_ticks = [t for t in ticks if t.ai is not None]
    assert ai_ticks[0].ai.age_ms == 0  # type: ignore[union-attr]
    for prev, cur in zip(ai_ticks, ai_ticks[1:]):
        expected = int((cur.t - prev.t) * 1000)
        assert abs(cur.ai.age_ms - expected) <= 1  # type: ignore[union-attr]


def test_ai_tags_follow_the_scenario() -> None:
    ticks = collect(400, ai_coverage=1.0, seed=2)
    by_seq = {t.seq: t.ai for t in ticks}
    # 0-59 office screen; 60-89 coffee; 150-209 cafe lunch; 240-299 park
    assert by_seq[10].screen_present and by_seq[10].scene == "office"  # type: ignore[union-attr]
    assert by_seq[70].caffeine_visible  # type: ignore[union-attr]
    assert by_seq[70].drink == "coffee"  # type: ignore[union-attr]
    assert by_seq[70].caption == "desk coffee"  # type: ignore[union-attr]
    assert 2 <= len(by_seq[70].objects) <= 3  # type: ignore[union-attr]
    assert by_seq[170].food_present and by_seq[170].food_type == "rice_bowl"  # type: ignore[union-attr]
    assert by_seq[170].people_present and by_seq[170].scene == "cafe"  # type: ignore[union-attr]
    assert by_seq[260].vegetation_visible and by_seq[260].scene == "park"  # type: ignore[union-attr]
    # The widened menus reach the demo: a specific room and a specific activity
    # where the old script could only say "home" and "seated".
    assert by_seq[320].scene == "home"  # type: ignore[union-attr]
    assert by_seq[320].activity == "phone_use"  # type: ignore[union-attr]
    assert by_seq[350].alcohol_visible  # type: ignore[union-attr]
    assert by_seq[350].drink == "alcohol"  # type: ignore[union-attr]


def test_phash_stays_near_constant_within_a_segment_and_jumps_between() -> None:
    ticks = collect(200, seed=5)
    hashes = {t.seq: t.sensor.phash for t in ticks}

    # Within the first (60 s) segment, consecutive ticks barely move.
    within = [phash_distance(hashes[i], hashes[i + 1]) for i in range(5, 55)]
    assert max(within) <= 6

    # Crossing 59 -> 60 (office_screen -> desk_coffee) is a large jump.
    for boundary in (60, 90, 135, 195):
        jump = phash_distance(hashes[boundary - 1], hashes[boundary])
        assert jump > 12, f"boundary at seq {boundary} did not jump (d={jump})"


def test_sensor_fields_track_the_segment() -> None:
    ticks = collect(300, seed=11)
    office = [t for t in ticks if 0 <= t.seq < 60]  # office_screen
    park = [t for t in ticks if 230 <= t.seq < 280]  # park (225-285)

    assert min(t.sensor.lux_proxy for t in park) > max(
        t.sensor.lux_proxy for t in office
    ), "outdoors must read brighter than the office"
    assert sum(t.sensor.frame_delta for t in park) > sum(
        t.sensor.frame_delta for t in office
    ), "walking must move more than sitting"
    assert all(t.device.gps_speed > 0.8 for t in park)  # type: ignore[union-attr]
    assert all(t.device.gps_speed < 0.2 for t in office)  # type: ignore[union-attr]


def test_deterministic_for_a_given_seed() -> None:
    a = collect(40, seed=42)
    b = collect(40, seed=42)
    assert [t.sensor.phash for t in a] == [t.sensor.phash for t in b]
    assert [t.ai is None for t in a] == [t.ai is None for t in b]


def test_frames_are_written_to_the_store() -> None:
    store = InMemoryFrameStore(ttl_s=90)
    src = SimSource(DEFAULT_SCENARIO, store, seed=0)
    ticks = [src.next_tick() for _ in range(5)]
    got = store.get([t.frame_ref for t in ticks])
    assert len(got) == 5
    for blob in got.values():
        assert blob[:2] == b"\xff\xd8"  # JPEG SOI
        assert len(blob) > 500


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        SimSource(DEFAULT_SCENARIO, speed=0)
    with pytest.raises(ValueError):
        SimSource(DEFAULT_SCENARIO, ai_coverage=1.5)
    with pytest.raises(ValueError):
        SimSource(DEFAULT_SCENARIO, interval_s=0)


# -- tick cadence (S9) ---------------------------------------------------


def test_interval_s_drives_the_tick_clock() -> None:
    """Person A's glasses emit every 1.5 s; `tick.t` follows, `seq` does not."""

    src = SimSource(DEFAULT_SCENARIO, frame_store=None, seed=1, interval_s=1.5)
    ticks = [src.next_tick() for _ in range(10)]
    assert [t.seq for t in ticks] == list(range(10))
    assert all(b.t - a.t == pytest.approx(1.5) for a, b in zip(ticks, ticks[1:]))
    assert ticks[0].t == src.start_t
    assert ticks[-1].t == pytest.approx(src.start_t + 9 * 1.5)


def test_segment_boundaries_land_on_the_right_seq_at_1_5s() -> None:
    """Segments are scripted in seconds, so a 60 s segment is 40 ticks at 1.5 s."""

    src = SimSource(DEFAULT_SCENARIO, frame_store=None, seed=2, ai_coverage=1.0,
                    interval_s=1.5)
    ticks = [src.next_tick() for _ in range(260)]
    # Cumulative scenario seconds: 60, 90, 135, 195, 225, 285 ...
    boundaries = {60: 40, 90: 60, 135: 90, 195: 130, 225: 150, 285: 190}
    for second, seq in boundaries.items():
        assert seq * 1.5 == second, "boundary must fall on a tick, not between"
        before = DEFAULT_SCENARIO.segment_at((seq - 1) * 1.5)[0]
        after = DEFAULT_SCENARIO.segment_at(seq * 1.5)[0]
        assert after == before + 1, f"seq {seq} did not cross a boundary"

    # And the tags follow: seq 90 (= 135 s) is the cafe, seq 150 the park.
    by_seq = {t.seq: t.ai for t in ticks}
    assert by_seq[95].scene == "cafe" and by_seq[95].food_present  # type: ignore[union-attr]
    assert by_seq[160].scene == "park" and by_seq[160].vegetation_visible  # type: ignore[union-attr]


@pytest.mark.parametrize("interval_s", [1.0, 1.5])
def test_the_seeded_hr_spike_still_lands_in_the_lunch_segment(interval_s: float) -> None:
    """Seeded series are keyed on scenario *seconds*, so cadence cannot move them.

    ``seed_biometric_series`` writes at ``start_t + second`` and ``SimSource``
    starts its clock at the same ``start_t``, so the spike sits over lunch at
    any tick rate -- which is what makes `biometric_anomaly` demonstrable.
    """

    from pipeline.seed.biometrics import LUNCH_END, LUNCH_START, heart_rate_series

    src = SimSource(DEFAULT_SCENARIO, frame_store=None, seed=0, ai_coverage=1.0,
                    interval_s=interval_s)
    ticks = [src.next_tick() for _ in range(int(300 / interval_s))]

    series = heart_rate_series(src.start_t, duration_s=600, resting_hr=58.0)
    peak_t = max(series, key=lambda row: row[2])[0]
    assert LUNCH_START <= peak_t - src.start_t <= LUNCH_END

    nearest = min(ticks, key=lambda t: abs(t.t - peak_t))
    assert abs(nearest.t - peak_t) <= interval_s
    assert nearest.ai is not None
    assert nearest.ai.scene == "cafe" and nearest.ai.food_present


async def test_async_iteration_respects_speed() -> None:
    src = SimSource(DEFAULT_SCENARIO, speed=100.0, max_ticks=10)
    started = time.monotonic()
    seen = [tick.seq async for tick in src]
    elapsed = time.monotonic() - started
    assert seen == list(range(10))
    assert elapsed < 1.0, "speed=100 must not take a second per tick"


async def test_async_iteration_can_be_cancelled() -> None:
    src = SimSource(DEFAULT_SCENARIO, speed=50.0)
    seen: list[int] = []

    async def run() -> None:
        async for tick in src:
            seen.append(tick.seq)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(seen) >= 2


def test_custom_scenario() -> None:
    scenario = Scenario(
        name="gym",
        segments=[
            Segment(
                name="lift",
                duration_s=5,
                scene="gym",
                activity="exercising",
                motion_level=0.9,
            )
        ],
    )
    src = SimSource(scenario, ai_coverage=1.0, seed=0)
    tick = src.next_tick()
    assert tick.ai is not None and tick.ai.scene == "gym"
    assert tick.ai.activity == "exercising"


def test_tick_time_is_simulated_clock():
    """One clock: tick.t advances exactly 1.0 per tick regardless of speed."""
    from pipeline.sim.scenario import DEFAULT_SCENARIO
    from pipeline.sim.source import SimSource

    src = SimSource(DEFAULT_SCENARIO, frame_store=None, speed=50.0, seed=1)
    a, b, c = src.next_tick(), src.next_tick(), src.next_tick()
    assert b.t - a.t == 1.0 and c.t - b.t == 1.0
    assert a.t == src.start_t


async def test_async_iteration_sleeps_interval_over_speed() -> None:
    """The wall gap is `interval_s / speed`, so a slow cadence is not slower."""

    src = SimSource(DEFAULT_SCENARIO, speed=100.0, max_ticks=10, interval_s=1.5)
    started = time.monotonic()
    seen = [tick.t async for tick in src]
    assert time.monotonic() - started < 1.0
    assert all(b - a == pytest.approx(1.5) for a, b in zip(seen, seen[1:]))
