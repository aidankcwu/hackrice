"""SPEC §2.5 / §12.3: 90-second ring, expired refs omitted rather than fatal."""

from __future__ import annotations

from pipeline.frames import FrameStore, InMemoryFrameStore


def test_put_and_get() -> None:
    store = InMemoryFrameStore(ttl_s=90)
    store.put("f_1", b"jpegA", 1000.0)
    store.put("f_2", b"jpegB", 1001.0)
    assert store.get(["f_1", "f_2"]) == {"f_1": b"jpegA", "f_2": b"jpegB"}
    assert len(store) == 2


def test_missing_ref_is_omitted_not_raised() -> None:
    store = InMemoryFrameStore()
    store.put("f_1", b"x", 1000.0)
    got = store.get(["f_1", "f_nope"])
    assert got == {"f_1": b"x"}
    assert store.misses == 1


def test_ttl_expiry_via_expire() -> None:
    store = InMemoryFrameStore(ttl_s=90)
    store.put("old", b"a", 1000.0)
    store.put("mid", b"b", 1060.0)
    store.put("new", b"c", 1089.0)

    evicted = store.expire(1091.0)  # cutoff 1001.0 -> only "old" is stale
    assert evicted == 1
    assert len(store) == 2
    assert store.get(["old", "mid", "new"]).keys() == {"mid", "new"}


def test_ttl_expiry_on_put() -> None:
    store = InMemoryFrameStore(ttl_s=90)
    store.put("f_1", b"a", 1000.0)
    store.put("f_2", b"b", 1095.0)  # 95 s later: f_1 falls out of the window
    assert len(store) == 1
    assert store.get(["f_1"]) == {}
    assert store.get(["f_2"]) == {"f_2": b"b"}


def test_everything_expires_eventually() -> None:
    store = InMemoryFrameStore(ttl_s=90)
    for i in range(10):
        store.put(f"f_{i}", b"x" * 100, 1000.0 + i)
    assert store.bytes_held == 1000
    store.expire(2000.0)
    assert len(store) == 0
    assert store.evicted == 10


def test_max_frames_guard() -> None:
    store = InMemoryFrameStore(ttl_s=1e9, max_frames=5)
    for i in range(20):
        store.put(f"f_{i}", b"x", 1000.0 + i)
    assert len(store) == 5
    assert "f_19" in store
    assert "f_0" not in store


def test_satisfies_protocol() -> None:
    """Person A's real ring buffer must be droppable in unchanged."""

    store = InMemoryFrameStore()
    assert isinstance(store, FrameStore)
