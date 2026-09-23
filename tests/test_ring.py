"""A7 — frame ring buffer. Done when steady-state holds ~90 frames and memory is flat."""

from __future__ import annotations

import pytest

from longevity.ring import DEFAULT_MAX_FRAMES, FrameRing

T0 = 1_000_000.0
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 39_996  # ~40 KB, the §2.2 size


def ref(i: int) -> str:
    return f"f_{i:08d}"


def fill(ring: FrameRing, n: int, *, hz: float = 1.0, start: float = T0) -> float:
    """Drive `n` frames at `hz`, with the clock advancing in lockstep. Returns end time."""
    now = start
    for i in range(n):
        now = start + i / hz
        ring.put(ref(i), JPEG, t=now, now=now)
    return now


def test_steady_state_is_about_ninety_frames():
    ring = FrameRing()
    now = fill(ring, 200)
    s = ring.stats(now=now)
    assert 88 <= s.count <= 92, s.count
    assert 3.3e6 <= s.bytes <= 3.8e6, s.bytes  # §2.5 predicts ~3.5 MB


def test_memory_is_flat_over_a_fifteen_minute_run():
    """900 ticks is the whole demo (§2.5). Count and bytes must not trend upward."""
    ring = FrameRing()
    samples = []
    for i in range(900):
        now = T0 + i
        ring.put(ref(i), JPEG, t=now, now=now)
        if i >= 120 and i % 60 == 0:
            s = ring.stats(now=now)
            samples.append((s.count, s.bytes))

    counts = {c for c, _ in samples}
    byte_counts = {b for _, b in samples}
    assert len(counts) == 1, f"count drifted across the run: {sorted(counts)}"
    assert len(byte_counts) == 1, f"bytes drifted: {sorted(byte_counts)}"
    assert ring.stats(now=T0 + 899).evicted_ttl > 0


def test_frames_older_than_ttl_are_evicted():
    ring = FrameRing()
    now = fill(ring, 200)
    assert ring.get(ref(199), now=now) is not None
    assert ring.get(ref(0), now=now) is None
    assert ring.get(ref(5), now=now) is None


def test_expired_ref_reads_as_a_miss_even_when_the_producer_stalls():
    """Eviction is driven by puts. A stalled producer must not keep serving dead frames."""
    ring = FrameRing()
    ring.put(ref(1), JPEG, t=T0, now=T0)
    assert ring.get(ref(1), now=T0 + 89) is not None
    assert ring.get(ref(1), now=T0 + 91) is None


def test_cap_bounds_memory_when_frames_arrive_faster_than_real_time():
    """`replay --speed 10` puts 10 frames per wall second; the TTL alone would not bind."""
    ring = FrameRing(max_frames=50)
    now = fill(ring, 400, hz=100.0)
    s = ring.stats(now=now)
    assert s.count == 50
    assert s.evicted_cap > 0


def test_overwriting_a_ref_does_not_double_count_bytes():
    ring = FrameRing()
    ring.put(ref(1), b"a" * 1000, t=T0, now=T0)
    ring.put(ref(1), b"b" * 10, t=T0, now=T0)
    s = ring.stats(now=T0)
    assert s.count == 1
    assert s.bytes == 10


def test_get_many_preserves_order_and_reports_misses():
    ring = FrameRing()
    now = fill(ring, 200)
    found, missing = ring.get_many([ref(199), ref(150), ref(5), ref(198)], now=now)
    assert [f.ref for f in found] == [ref(199), ref(150), ref(198)]
    assert missing == [ref(5)]


def test_bytes_are_returned_unchanged():
    ring = FrameRing()
    ring.put(ref(1), JPEG, t=T0, now=T0)
    got = ring.get(ref(1), now=T0)
    assert got is not None and got.jpeg == JPEG


def test_sweep_evicts_without_a_put():
    ring = FrameRing()
    fill(ring, 10)
    assert len(ring) == 10
    assert ring.sweep(now=T0 + 500) == 10
    assert len(ring) == 0


def test_steady_producer_evicts_at_most_one_frame_per_put():
    """Eviction must stay bounded — invariant 1, T0 never blocks."""
    ring = FrameRing()
    fill(ring, 120)
    for i in range(120, 300):
        now = T0 + i
        assert ring.put(ref(i), JPEG, t=now, now=now) <= 1


@pytest.mark.parametrize("ttl", [1.0, 30.0, 90.0])
def test_ttl_is_configurable(ttl: float):
    ring = FrameRing(ttl_s=ttl)
    ring.put(ref(1), JPEG, t=T0, now=T0)
    assert ring.get(ref(1), now=T0 + ttl - 0.1) is not None
    assert ring.get(ref(1), now=T0 + ttl + 0.1) is None


def test_ring_never_touches_the_disk():
    """§2.5: 'Frames are never written to disk from T0.' Enforced by inspection."""
    import inspect

    from longevity import ring as ring_module

    src = inspect.getsource(ring_module)
    for forbidden in ("open(", "Path(", "os.write", "shutil", "pickle.dump"):
        assert forbidden not in src, f"ring.py must not reference {forbidden}"
    assert DEFAULT_MAX_FRAMES >= 90


def test_expired_bytes_do_not_linger_in_ram_when_the_producer_stalls():
    """§2.5's privacy claim is a 90 s lifetime in RAM, not merely a 90 s serving window.

    Eviction is put-driven, so a dead capture loop would otherwise leave bytes resident.
    The read paths sweep, so they don't.
    """
    ring = FrameRing()
    fill(ring, 10)
    assert ring.stats(now=T0 + 1).count == 10
    ring.get_many([ref(0)], now=T0 + 500)
    assert len(ring) == 0, "expired frames still resident after a read"


def test_eviction_does_not_assume_frames_arrive_in_order():
    """An unsorted directory listing in the replay adapter would insert out of order."""
    ring = FrameRing()
    ring.put(ref(50), JPEG, t=T0 + 50, now=T0 + 50)      # newer, inserted first
    ring.put(ref(1), JPEG, t=T0 - 500, now=T0 + 50)      # ancient, inserted second
    assert len(ring) == 1, "an expired frame behind a live one was never evicted"
    assert ring.get(ref(50), now=T0 + 50) is not None


def test_cap_eviction_drops_the_oldest_by_capture_time():
    ring = FrameRing(max_frames=2)
    now = T0 + 1000
    for i, t in [(3, T0 + 990), (1, T0 + 970), (2, T0 + 980)]:
        ring.put(ref(i), JPEG, t=t, now=now)
    assert sorted(f.ref for f in ring) == [ref(2), ref(3)]


# --- US-W11: the cap follows the frame rate ------------------------------------


def test_fps_hint_sizes_the_cap_to_hold_a_full_ttl():
    ring = FrameRing(ttl_s=90, fps_hint=7)
    assert ring.max_frames >= 630
    assert ring.max_frames == 646
    s = ring.stats(now=T0)
    assert s.max_frames == 646 and s.fps_hint == 7


def test_no_hints_keeps_the_old_default_cap():
    ring = FrameRing()
    assert ring.max_frames == DEFAULT_MAX_FRAMES == 256
    s = ring.stats(now=T0)
    assert s.max_frames == 256 and s.fps_hint is None


def test_explicit_max_frames_wins_over_fps_hint():
    assert FrameRing(ttl_s=90, max_frames=50, fps_hint=7).max_frames == 50


def test_max_frames_is_read_only():
    ring = FrameRing()
    with pytest.raises(AttributeError):
        ring.max_frames = 10  # type: ignore[misc]


def test_cap_eviction_holds_at_the_fps_derived_cap():
    ring = FrameRing(ttl_s=90, fps_hint=7)
    cap = ring.max_frames
    now = T0
    for i in range(cap + 5):
        now = T0 + i / 1000  # well inside the TTL, so only the cap can bind
        ring.put(ref(i), JPEG, t=now, now=now)
    s = ring.stats(now=now)
    assert s.count == cap
    assert s.evicted_cap == 5 and s.evicted_ttl == 0
    assert all(ring.get(ref(i), now=now) is None for i in range(5))
    assert ring.get(ref(5), now=now) is not None
