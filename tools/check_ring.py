#!/usr/bin/env python3
"""Proof that A7 and A8 hold: `uv run python tools/check_ring.py`.

Four things worth checking, and none of them can be checked by staring at the code:

  1. The 90 s TTL actually evicts. Driven by an injected clock — `put`, `get` and
     `stats` all take an explicit `now`, so a 15-minute run costs milliseconds and no
     test ever sleeps.
  2. Memory is flat (PERSON_A.md A7's done-when). 900 puts is the whole demo at 1 Hz.
     Each put allocates a *distinct* 40 KB payload, so `tracemalloc` measures real
     retention rather than 900 references to one shared object — reusing one buffer
     would make this check pass while proving nothing.
  3. Bytes survive the round trip through base64, unchanged.
  4. The 410 path is a logged miss, not a crash (§12.3).

Exits non-zero on the first failure of any check.
"""

from __future__ import annotations

import base64
import logging
import sys
import threading
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from longevity.ring import FrameRing, default_ring  # noqa: E402
from longevity.server.frames import attach  # noqa: E402
from longevity.tick import FRAME_TTL_S, frame_ref  # noqa: E402

T0 = 1_757_700_000.0  # a fixed fake epoch; nothing here reads the wall clock
DEMO_TICKS = 900  # 15 minutes at 1 Hz (§2.5)


def jpeg(i: int) -> bytes:
    """~40 KB (§2.2), and a fresh allocation every call so memory checks mean something."""
    return b"\xff\xd8\xff\xe0" + bytes([i % 251]) * 39_996


# --- the harness ---------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        try:
            detail = fn() or ""
            _results.append((name, True, detail))
        except AssertionError as e:
            _results.append((name, False, str(e)))
        except Exception as e:  # a crash is a failure, not a traceback
            _results.append((name, False, f"{type(e).__name__}: {e}"))
        ok, detail = _results[-1][1], _results[-1][2]
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n        {detail}" if detail else ""))
        return fn

    return wrap


def eq(got, want, what: str) -> None:
    assert got == want, f"{what}: got {got!r}, want {want!r}"


# --- A7: the ring --------------------------------------------------------------


@check("ttl: a frame is live at 89 s and gone at 91 s")
def _ttl_boundary() -> str:
    ring = FrameRing()
    ring.put(frame_ref(1), jpeg(1), t=T0, now=T0)
    assert ring.get(frame_ref(1), now=T0 + 89) is not None, "evicted early"
    assert ring.get(frame_ref(1), now=T0 + FRAME_TTL_S + 1) is None, "served past the TTL"
    eq(len(ring), 0, "bytes still resident after the read swept them")

    # A producer that stalls never calls put, so the read paths and `sweep` are what
    # keep §2.5's claim true: 90 s in RAM, not 90 s of being served.
    stalled = FrameRing()
    for i in range(5):
        stalled.put(frame_ref(i), jpeg(i), t=T0 + i, now=T0 + i)
    eq(stalled.sweep(now=T0 + 500), 5, "frames swept after a stall")
    eq(stalled.nbytes, 0, "bytes held after the sweep")
    return f"TTL={FRAME_TTL_S}s, live at +89s, gone at +91s, 5 stalled frames swept to 0 B"


@check("ttl: eviction is O(1) amortised — at most one frame per put at 1 Hz")
def _bounded_eviction() -> str:
    ring = FrameRing()
    worst = 0
    for i in range(300):
        now = T0 + i
        worst = max(worst, ring.put(frame_ref(i), jpeg(i), t=now, now=now))
    assert worst <= 1, f"a single put evicted {worst} frames — that is a scan, not a pop"
    return f"max evictions in one put over 300 puts: {worst}"


@check(f"steady state: ~90 entries and flat bytes across {DEMO_TICKS} puts (15 min at 1 Hz)")
def _steady_state() -> str:
    tracemalloc.start()
    ring = FrameRing()
    samples: list[tuple[int, int, int]] = []  # (count, ring bytes, traced bytes)

    for i in range(DEMO_TICKS):
        now = T0 + i
        ring.put(frame_ref(i), jpeg(i), t=now, now=now)
        if i >= 180 and i % 60 == 0:
            s = ring.stats(now=now)
            samples.append((s.count, s.bytes, tracemalloc.get_traced_memory()[0]))

    tracemalloc.stop()
    counts = {c for c, _, _ in samples}
    ring_bytes = {b for _, b, _ in samples}
    traced = [t for _, _, t in samples]
    drift = (max(traced) - min(traced)) / min(traced)

    assert len(counts) == 1, f"entry count drifted: {sorted(counts)}"
    assert len(ring_bytes) == 1, f"byte footprint drifted: {sorted(ring_bytes)}"
    count, nbytes = samples[0][0], samples[0][1]
    assert 88 <= count <= 92, f"steady state is {count} entries, expected ~90"
    assert 3.0e6 <= nbytes <= 4.0e6, f"{nbytes} bytes, §2.5 predicts ~3.5 MB"
    assert drift < 0.05, f"real heap drifted {drift:.1%} across the run: {traced}"

    s = ring.stats(now=T0 + DEMO_TICKS - 1)
    assert s.evicted_ttl > 0 and s.evicted_cap == 0, "expected TTL eviction only"
    return (
        f"{count} entries, {nbytes / 1e6:.2f} MB tracked, "
        f"heap {min(traced) / 1e6:.2f}–{max(traced) / 1e6:.2f} MB (drift {drift:.2%}), "
        f"{s.puts} puts, {s.evicted_ttl} evicted"
    )


@check("privacy: §2.5 — nothing in ring.py can reach the disk")
def _no_disk() -> str:
    src = (Path(__file__).resolve().parent.parent / "src/longevity/ring.py").read_text()
    banned = ("open(", "tempfile", "shutil", "pickle", "os.write", "mmap", "Path(")
    hits = [w for w in banned if w in src]
    eq(hits, [], "disk-capable names in ring.py")
    return "no file, tempfile, or serialisation calls — frames exist in RAM only"


@check("concurrency: a 1 Hz writer and HTTP readers do not corrupt the accounting")
def _threaded() -> str:
    """FastAPI runs a `def` handler in a threadpool, so reads really are off-loop.

    The lock is what makes that safe. Without it `self._bytes` and the heap drift apart
    under load — which is to say, during the demo and not before it.
    """
    ring = FrameRing()
    stop = threading.Event()
    errors: list[str] = []

    def write() -> None:
        i = 0
        while not stop.is_set():
            now = time.time()
            ring.put(frame_ref(i), jpeg(i), t=now, now=now)
            i += 1

    def read() -> None:
        while not stop.is_set():
            try:
                found, _ = ring.get_many([frame_ref(i) for i in range(0, 400, 7)])
                for f in found:
                    assert len(f.jpeg) == 40_000, "torn payload"
                ring.stats()
            except Exception as e:  # noqa: BLE001 - the whole point is to catch it
                errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=write)] + [
        threading.Thread(target=read) for _ in range(3)
    ]
    for th in threads:
        th.start()
    time.sleep(0.4)
    stop.set()
    for th in threads:
        th.join()

    eq(errors[:1], [], "exceptions raised under concurrent access")
    s = ring.stats()
    eq(s.bytes, s.count * 40_000, "byte counter vs. entries held")
    return f"{s.puts} puts and {s.hits + s.misses} reads across 4 threads, accounting exact"


# --- A8: the endpoint ----------------------------------------------------------


def _client() -> tuple[TestClient, list[str], str]:
    """Four live refs plus one that aged out 100 s ago, on a real wall clock."""
    ring = FrameRing()
    now = time.time()
    live = [frame_ref(i) for i in range(1738, 1743)]
    for offset, ref in enumerate(reversed(live)):
        ring.put(ref, jpeg(offset), t=now - offset, now=now)
    dead = frame_ref(99)
    ring.put(dead, jpeg(7), t=now - 100, now=now - 100)  # past the 90 s window

    app = FastAPI()
    attach(app, ring)
    return TestClient(app), live, dead


@check("GET /frames: four live refs round-trip byte-for-byte through base64")
def _round_trip() -> str:
    client, live, _ = _client()
    wanted = [live[0], live[2], live[3], live[4]]
    r = client.get("/frames", params={"refs": ",".join(wanted)})

    eq(r.status_code, 200, "status")
    body = r.json()
    eq(body["missing"], [], "missing")
    eq([f["ref"] for f in body["frames"]], wanted, "order of returned frames")

    ring: FrameRing = client.app.state.frame_ring  # type: ignore[attr-defined]
    total = 0
    for f in body["frames"]:
        decoded = base64.b64decode(f["b64"])
        stored = ring.get(f["ref"])
        assert stored is not None, f"{f['ref']} vanished mid-request"
        eq(decoded, stored.jpeg, f"bytes for {f['ref']}")
        eq(f["bytes"], len(decoded), "declared length")
        eq(f["mime"], "image/jpeg", "mime")
        assert 0 <= f["age_ms"] <= FRAME_TTL_S * 1000, f"age_ms {f['age_ms']} out of window"
        total += len(decoded)
    return f"4 refs, {total / 1e3:.0f} KB of JPEG, decoded identical to the ring"


@check("GET /frames: one expired ref out of four still returns the other three (200)")
def _partial() -> str:
    client, live, dead = _client()
    r = client.get("/frames", params={"refs": f"{live[0]},{dead},{live[3]},{live[4]}"})
    eq(r.status_code, 200, "status")
    body = r.json()
    eq([f["ref"] for f in body["frames"]], [live[0], live[3], live[4]], "frames")
    eq(body["missing"], [dead], "missing")
    return "3 of 4 delivered, the expired one named in `missing` — B keeps its context"


@check("GET /frames: every ref expired or unknown → 410, logged not crashed (§12.3)")
def _all_gone() -> str:
    client, _, dead = _client()
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    log = logging.getLogger("longevity.server.frames")
    handler = Capture()
    log.addHandler(handler)
    try:
        expired = client.get("/frames", params={"refs": dead})
        unknown = client.get("/frames", params={"refs": "f_99999999,f_00000001"})
    finally:
        log.removeHandler(handler)

    eq(expired.status_code, 410, "status for an expired ref")
    eq(expired.json()["missing"], [dead], "missing on 410")
    eq(expired.json()["frames"], [], "frames on 410")
    eq(unknown.status_code, 410, "status for unknown refs")
    assert any(dead in m for m in records), f"the miss was not logged: {records}"
    assert client.get("/frames/stats").status_code == 200, "server did not survive"
    return f"410 with `missing` populated; logged {len(records)} warning(s), no exception"


@check("GET /frames: repeated and comma-joined refs parse alike, de-duped, order kept")
def _parsing() -> str:
    client, live, _ = _client()
    a = client.get("/frames", params={"refs": f" {live[0]} , {live[0]},{live[1]}"}).json()
    b = client.get("/frames", params=[("refs", live[0]), ("refs", live[1])]).json()
    eq([f["ref"] for f in a["frames"]], [live[0], live[1]], "comma form")
    eq([f["ref"] for f in b["frames"]], [live[0], live[1]], "repeated form")
    eq(client.get("/frames", params={"refs": " , ,"}).status_code, 400, "empty refs")
    eq(client.get("/frames").status_code, 422, "absent refs param")
    return "?refs=a,b == ?refs=a&refs=b; blank → 400; absent → 422"


@check("GET /frames/stats: the health numbers the run-log prints")
def _stats() -> str:
    client, live, _ = _client()
    client.get("/frames", params={"refs": live[0]})
    s = client.get("/frames/stats").json()
    # Five live frames, not six: the 100 s-old one was swept, not merely hidden.
    eq(s["count"], len(live), "live entries")
    eq(s["ttl_s"], FRAME_TTL_S, "ttl")
    assert s["hits"] >= 1 and s["bytes"] > 0, s
    eq(s["bytes"], len(live) * 40_000, "bytes held")
    return (
        f"count={s['count']} bytes={s['bytes']} hits={s['hits']} "
        f"misses={s['misses']} evicted_ttl={s['evicted_ttl']}"
    )


@check("wiring: an app that only includes the router reads the process-wide ring")
def _fallback() -> str:
    from longevity.server.frames import router

    app = FastAPI()
    app.include_router(router)  # no attach() — this is what server/app.py does
    ring = default_ring()
    ring.clear()
    now = time.time()
    ring.put(frame_ref(4242), jpeg(1), t=now, now=now)

    r = TestClient(app).get("/frames", params={"refs": frame_ref(4242)})
    eq(r.status_code, 200, "status")
    eq(len(base64.b64decode(r.json()["frames"][0]["b64"])), 40_000, "payload length")
    ring.clear()
    return "capture loop writes ring.default_ring(); the unattached router reads it"


if __name__ == "__main__":
    failed = [name for name, ok, _ in _results if not ok]
    print()
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    sys.exit(1 if failed else 0)
