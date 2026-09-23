"""Acceptance checks for A10 — the T0 VLM call, its budget, and the drop rule.

docs/PERSON_A.md on this task: "Test this by injecting a 3-second artificial delay into the
call. If the tick stream stutters, invariant 1 is broken. This is the single most
likely thing to be quietly wrong in your whole half of the system, and it will not
announce itself."

So that is check 1, and it measures the tick clock rather than trusting it.

Run:  uv run python tools/check_vlm.py
      uv run python tools/check_vlm.py --live     # also measure real Gemini coverage
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time

from longevity.vlm import FakeClient, T0Tagger

# A tiny but real JPEG, so the live path has something valid to send.
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffc00011080001000103011100021101031101"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b51000"
    "02010303020403050504040000017d01020300041105122131410613516107227114328191a108"
    "2342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748"
    "494a535455565758595a636465666768696a737475767778797a838485868788898a9293949596"
    "9798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9"
    "dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fb7e8a28a2803fff"
    "d9"
)

FAIL = 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global FAIL
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAIL += 1


async def drive(tagger: T0Tagger, n_ticks: int, period: float = 1.0):
    """A minimal stand-in for the A6 tick loop: monotonic target, offer, take.

    Deliberately mirrors how A6 will call the tagger — offer the frame, immediately
    take whatever has landed, never await the network — so that a stutter here is a
    stutter there.
    """
    starts: list[float] = []
    served: list[tuple[dict, float] | None] = []
    t0 = time.perf_counter()
    for seq in range(n_ticks):
        target = t0 + seq * period
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        starts.append(time.perf_counter())
        tagger.offer(time.time() + seq, JPEG)
        served.append(tagger.take())
    intervals = [b - a for a, b in zip(starts, starts[1:])]
    drift = starts[-1] - t0 - (n_ticks - 1) * period
    return intervals, drift, served


async def check_slow_api_does_not_stall(n: int = 8) -> None:
    """THE headline check: a 3 s API against a 1 s budget must not move the clock."""
    print("\n1. Injected 3 s API delay — does the tick stream stutter?")
    client = FakeClient(latency=3.0)
    tagger = T0Tagger(client, log_every=0)
    await tagger.start()
    intervals, drift, served = await drive(tagger, n)
    await tagger.aclose()

    worst = max(abs(i - 1.0) for i in intervals)
    print(
        f"     intervals: mean={statistics.mean(intervals):.4f}s "
        f"max_dev={worst * 1000:.1f}ms  cumulative_drift={drift * 1000:+.1f}ms"
    )
    report("tick cadence holds 1 Hz under a 3 s API", worst < 0.05,
           f"worst deviation {worst * 1000:.1f}ms")
    report("cumulative drift stays sub-100ms", abs(drift) < 0.1,
           f"{drift * 1000:+.1f}ms over {n} ticks")
    report("every over-budget tick omits the ai block", all(s is None for s in served),
           f"{sum(s is None for s in served)}/{n} ticks had no ai block")
    report("calls were abandoned, not awaited", tagger.overruns >= n - 2,
           f"{tagger.overruns} overruns, {tagger.returned} returned")
    report("abandoned calls were actually cancelled", client.cancelled >= n - 2,
           f"{client.cancelled} cancelled")


async def check_self_scheduling(n: int = 6) -> None:
    """§5.3: fire the next call when the previous returns or its budget expires."""
    print("\n2. Self-scheduling vs. a fixed timer")
    client = FakeClient(latency=3.0)
    tagger = T0Tagger(client, log_every=0)
    await tagger.start()
    await drive(tagger, n)
    await tagger.aclose()
    print(f"     calls={client.calls}  max_concurrent={client.max_in_flight}")
    report("never more than one call in flight", client.max_in_flight == 1,
           f"max_in_flight={client.max_in_flight} (a fixed timer would stack these up)")


async def check_fast_api(n: int = 6) -> None:
    print("\n3. Fast API (300 ms) — coverage should be near total")
    tagger = T0Tagger(FakeClient(latency=0.3), log_every=0)
    await tagger.start()
    intervals, drift, served = await drive(tagger, n)
    await tagger.aclose()
    got = sum(s is not None for s in served)
    print(f"     {tagger.stats_line()}")
    # The first tick can never be served: its frame is offered and taken in the same
    # breath, before any call has had time to return. That is inherent, not a bug.
    report("coverage is high when the API keeps up", got >= n - 2, f"{got}/{n} ticks served")
    report("cadence still 1 Hz", max(abs(i - 1.0) for i in intervals) < 0.05)


async def check_never_stale(n: int = 12) -> None:
    """Invariant 3: absent, never stale. Results are consume-once."""
    print("\n4. No result is ever reused across ticks (absent, never stale)")
    rnd = random.Random(7)
    tagger = T0Tagger(FakeClient(latency=lambda: rnd.uniform(0.2, 1.8)), log_every=0)
    await tagger.start()
    _, _, served = await drive(tagger, n)
    await tagger.aclose()
    as_ofs = [s[1] for s in served if s is not None]
    gaps = sum(s is None for s in served)
    print(f"     {tagger.stats_line()}")
    print(f"     served as_of values: {len(as_ofs)}, distinct: {len(set(as_ofs))}, gaps: {gaps}")
    report("every served tick carries a distinct observation",
           len(as_ofs) == len(set(as_ofs)), "a repeat would mean a stale carry-forward")
    report("gaps actually occur and are survivable", gaps > 0, f"{gaps} ticks had no ai block")


async def check_robustness(n: int = 5) -> None:
    print("\n5. A failing API does not stop the clock")

    class Exploding(FakeClient):
        async def tag(self, jpeg: bytes) -> dict:
            self.calls += 1
            raise RuntimeError("simulated 500 from the API")

    client = Exploding(latency=0.1)
    tagger = T0Tagger(client, log_every=0)
    await tagger.start()
    intervals, _, served = await drive(tagger, n)
    await tagger.aclose()
    report("cadence survives a hard API error", max(abs(i - 1.0) for i in intervals) < 0.05)
    report("errors are counted, ai block omitted",
           tagger.errors > 0 and all(s is None for s in served),
           f"{tagger.errors} errors, {sum(s is None for s in served)}/{n} ticks without ai")


async def check_live(n: int = 20) -> None:
    print(f"\n6. LIVE Gemini — measuring real coverage over {n} ticks")
    try:
        from longevity.vlm import GeminiClient

        client = GeminiClient()
    except Exception as exc:  # noqa: BLE001
        print(f"  [SKIP] live check unavailable: {exc}")
        return
    tagger = T0Tagger(client, log_every=0)
    await tagger.start()
    intervals, drift, served = await drive(tagger, n)
    await tagger.aclose()
    print(f"     {tagger.stats_line()}")
    print(f"     drift={drift * 1000:+.1f}ms  max_dev={max(abs(i - 1.0) for i in intervals) * 1000:.1f}ms")
    report("cadence holds against the real API",
           max(abs(i - 1.0) for i in intervals) < 0.05)
    report("coverage in the 50-80% band predicted by §2.4",
           0.5 <= tagger.coverage <= 0.8,
           f"measured {tagger.coverage:.1%} — outside the band is informative, not fatal")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="also hit the real Gemini API")
    args = ap.parse_args()

    print("A10 — T0 VLM call, budget, and drop rule")
    await check_slow_api_does_not_stall()
    await check_self_scheduling()
    await check_fast_api()
    await check_never_stale()
    await check_robustness()
    if args.live:
        await check_live()

    print(f"\n{'ALL CHECKS PASSED' if FAIL == 0 else f'{FAIL} CHECK(S) FAILED'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
