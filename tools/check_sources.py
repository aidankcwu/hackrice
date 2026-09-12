#!/usr/bin/env python3
"""Verify the `replay` (A3) and `webcam` (A9) capture adapters.

    uv run python tools/check_sources.py

Builds a synthetic JPEG corpus in a temp directory — never in the repo's `corpus/`,
which is where A4's real recording goes — and drives `ReplaySource` over it, checking
order, `device is None`, real decodable JPEG bytes, and that the cadence is actually
1 Hz rather than approximately-1-Hz-drifting-to-2. Observed intervals and cumulative
drift are printed, not asserted silently.

The webcam half runs camera-free wherever it can: the encode path takes a numpy array
and the drop rule is exercised against a fake capture object. Opening the real camera
is attempted last and reported SKIP, not FAIL, when the OS has no device or denies
permission — which is the normal case in a sandbox or over SSH.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from longevity.sources.base import CaptureSource, Frame  # noqa: E402
from longevity.sources.replay import (  # noqa: E402
    ReplayCorpusError,
    ReplaySource,
    parse_stamp,
    scan_corpus,
)
from longevity.sources.webcam import (  # noqa: E402
    JPEG_QUALITY,
    TARGET_PX,
    CameraUnavailableError,
    WebcamSource,
    _LatestFrameReader,
    encode_jpeg,
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    _results.append((status, name, detail))
    tag = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}[status]
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))


def check(name: str, ok: bool, detail: str = "") -> bool:
    record(PASS if ok else FAIL, name, detail)
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --- synthetic corpus ---------------------------------------------------------


def make_jpeg(index: int, size: tuple[int, int] = (512, 288)) -> bytes:
    """A distinct, decodable JPEG per index. Distinct so order is provable by hash."""
    w, h = size
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    r = (x + index * 37) % 256
    g = (y + index * 11) % 256
    b = np.full((h, w), (index * 53) % 256, dtype=np.float32)
    arr = np.stack(np.broadcast_arrays(r, g, b), axis=-1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def build_corpus(
    root: Path,
    n: int,
    *,
    stamped: bool = True,
    gaps_ms: list[int] | None = None,
    jpeg_suffix_at: int | None = None,
) -> list[bytes]:
    """Write `n` frames as `frame_<unix_millis>.jpg`. Returns the expected order.

    `gaps_ms` gives the recorded spacing between consecutive frames (default 1000 ms,
    i.e. the nominal 1 Hz the glasses recorder aims for).
    """
    root.mkdir(parents=True, exist_ok=True)
    stamps = [1_757_700_842_000]
    for i in range(1, n):
        stamps.append(stamps[-1] + (gaps_ms[i - 1] if gaps_ms else 1000))
    # Deliberately shuffled write order — nothing may depend on directory order.
    for i in sorted(range(n), key=lambda k: (k * 7) % n):
        suffix = ".jpeg" if i == jpeg_suffix_at else ".jpg"
        name = f"frame_{stamps[i]}{suffix}" if stamped else f"img{i}.jpg"
        (root / name).write_bytes(make_jpeg(i))
    return [make_jpeg(i) for i in range(n)]


def digests(blobs: Iterable[bytes]) -> list[str]:
    return [hashlib.sha1(b).hexdigest()[:10] for b in blobs]


async def collect(source: CaptureSource, limit: int) -> tuple[list[Frame], list[float]]:
    """Take `limit` frames, recording the monotonic instant each one arrived."""
    out: list[Frame] = []
    marks: list[float] = []
    agen = source.frames()
    try:
        async for frame in agen:
            marks.append(time.monotonic())
            out.append(frame)
            if len(out) >= limit:
                break
    finally:
        await agen.aclose()
        await source.aclose()
    return out, marks


def timing_report(marks: list[float], step: float, label: str) -> tuple[float, float]:
    """Print inter-frame intervals and cumulative drift. Returns (mean gap, max drift)."""
    gaps = [b - a for a, b in zip(marks, marks[1:])]
    drifts = [(m - marks[0]) - i * step for i, m in enumerate(marks)]
    mean = statistics.fmean(gaps) if gaps else float("nan")
    worst = max(abs(d) for d in drifts)
    print(f"    {label}: nominal step {step * 1000:.1f} ms over {len(marks)} frames")
    shown = gaps if len(gaps) <= 12 else gaps[:6] + gaps[-6:]
    marker = "" if len(gaps) <= 12 else "  (first 6 … last 6)"
    print("      gaps ms  : " + " ".join(f"{g * 1000:7.1f}" for g in shown) + marker)
    shown_d = drifts if len(drifts) <= 12 else drifts[:6] + drifts[-6:]
    print("      drift ms : " + " ".join(f"{d * 1000:7.1f}" for d in shown_d))
    print(
        f"      mean {mean * 1000:.2f} ms | stdev "
        f"{(statistics.pstdev(gaps) * 1000 if len(gaps) > 1 else 0):.2f} ms | "
        f"final drift {drifts[-1] * 1000:+.1f} ms | worst |drift| {worst * 1000:.1f} ms"
    )
    return mean, worst


# --- A3: replay ---------------------------------------------------------------


def check_replay_order_and_shape(corpus: Path, expected: list[bytes]) -> None:
    section("A3 replay — order, device, JPEG bytes")

    entries, timing = scan_corpus(corpus)
    check("scan finds every frame, ignores non-JPEGs", len(entries) == len(expected),
          f"{len(entries)} entries, {len(expected)} expected")
    check("timestamped corpus uses stamp ordering", timing == "stamped", f"timing={timing}")

    frames, _ = asyncio.run(collect(ReplaySource(corpus, speed=50.0), len(expected)))
    check("emits every frame once", len(frames) == len(expected), f"{len(frames)} frames")
    check("order matches filename timestamps",
          digests(f.jpeg for f in frames) == digests(expected),
          f"first={digests([frames[0].jpeg])[0]} last={digests([frames[-1].jpeg])[0]}")
    check("device is None on every frame (§12.1)", all(f.device is None for f in frames))
    check("Frame.t strictly increases", all(b.t > a.t for a, b in zip(frames, frames[1:])))

    decoded = []
    for f in frames:
        img = Image.open(io.BytesIO(f.jpeg))
        img.load()
        decoded.append(img.size)
    check("every payload is a real decodable JPEG", len(decoded) == len(frames),
          f"sizes {decoded[0]}, mean {statistics.fmean(len(f.jpeg) for f in frames):.0f} B")
    check("src_t carried through from the filename",
          all(isinstance(f.meta.get("src_t"), float) for f in frames),
          f"first src_t={frames[0].meta['src_t']:.3f}")

    # Determinism: same directory, same order, every run (SPEC §11.3).
    again, _ = asyncio.run(collect(ReplaySource(corpus, speed=50.0), len(expected)))
    check("deterministic across runs", digests(f.jpeg for f in again) == digests(expected))


def check_replay_cadence(corpus: Path) -> None:
    section("A3 replay — 1 Hz cadence and drift")

    n = 6
    frames, marks = asyncio.run(collect(ReplaySource(corpus, speed=1.0), n))
    mean, worst = timing_report(marks, 1.0, "speed 1.0")
    check("mean inter-frame gap is ~1.000 s", 0.97 <= mean <= 1.05, f"{mean:.4f} s")
    check("cumulative drift stays under 100 ms at 1 Hz", worst < 0.100,
          f"worst {worst * 1000:.1f} ms over {n} frames")
    check("frames arrive in order at 1 Hz", len(frames) == n)

    # The drift test that matters. 200 frames at 20x is 200 sleeps: a loop that slept a
    # fixed duration each time would land ~200 scheduler overshoots late by the end.
    fast, fmarks = asyncio.run(collect(ReplaySource(corpus, speed=20.0, loop=True), 200))
    mean_f, worst_f = timing_report(fmarks, 0.05, "speed 20.0, 200 frames, looped")
    check("no drift accumulation over 200 scheduled wakeups", worst_f < 0.150,
          f"worst {worst_f * 1000:.1f} ms")
    check("looping restarts the corpus", fast[-1].meta["pass"] > 0,
          f"reached pass {fast[-1].meta['pass']}")


def check_replay_speed(corpus: Path) -> None:
    section("A3 replay — speed multiplier")

    n = 9
    results = {}
    for speed in (4.0, 16.0):
        _, marks = asyncio.run(collect(ReplaySource(corpus, speed=speed), n))
        mean, _ = timing_report(marks, 1.0 / speed, f"speed {speed:g}")
        results[speed] = mean
        nominal = 1.0 / speed
        check(f"speed {speed:g} emits at ~{nominal * 1000:.0f} ms",
              abs(mean - nominal) < max(0.010, nominal * 0.25),
              f"observed {mean * 1000:.2f} ms vs nominal {nominal * 1000:.1f} ms")

    ratio = results[4.0] / results[16.0]
    check("4x -> 16x is a proportional 4x speedup", 3.4 <= ratio <= 4.6,
          f"gap ratio {ratio:.2f}x")


def check_replay_recorded_spacing(root: Path) -> None:
    section("A3 replay — recorded spacing is reproduced, not flattened")

    # A plausibly irregular recording: the glasses aim for 1 Hz and miss. The 47 s gap
    # is the pause between A4's two takes and must be clamped, not slept through.
    gaps = [1000, 1480, 520, 1000, 2000, 300, 47_000, 1000, 900]
    irregular = root / "irregular"
    build_corpus(irregular, len(gaps) + 1, gaps_ms=gaps, jpeg_suffix_at=3)
    (irregular / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")

    entries, timing = scan_corpus(irregular)
    check("`.jpeg` suffix accepted alongside `.jpg`", len(entries) == len(gaps) + 1,
          f"{len(entries)} entries, one of them .jpeg")
    check(".DS_Store ignored silently", timing == "stamped", f"timing={timing}")

    speed, max_gap = 10.0, 5.0
    src = ReplaySource(irregular, speed=speed, max_gap=max_gap)
    frames, marks = asyncio.run(collect(src, len(gaps) + 1))
    observed = [(b - a) * 1000 for a, b in zip(marks, marks[1:])]
    wanted = [min(g, max_gap * 1000) / speed for g in gaps]
    print("      recorded ms : " + " ".join(f"{g:7.0f}" for g in gaps))
    print(f"      wanted ms   : " + " ".join(f"{g:7.1f}" for g in wanted) + f"  (/{speed:g})")
    print("      observed ms : " + " ".join(f"{g:7.1f}" for g in observed))
    worst = max(abs(o - w) for o, w in zip(observed, wanted))
    check("each gap matches the recorded spacing / speed", worst < 15.0,
          f"worst deviation {worst:.1f} ms")
    check("gaps are not flattened to a metronome",
          len(set(round(o, -1) for o in observed)) > 3,
          f"{len(set(round(o, -1) for o in observed))} distinct gaps")
    clamped = [i for i, f in enumerate(frames) if f.meta["gap_clamped"]]
    check("the between-takes gap is clamped to max_gap", clamped == [gaps.index(47_000) + 1],
          f"clamped at frame(s) {clamped}, {observed[gaps.index(47_000)]:.0f} ms not 4700 ms")
    check("Frame.t spacing mirrors the recorded timeline",
          abs((frames[1].t - frames[0].t) - gaps[0] / 1000 / speed) < 1e-6,
          f"delta t {(frames[1].t - frames[0].t) * 1000:.1f} ms")


def check_replay_fallback(root: Path) -> None:
    section("A3 replay — untimestamped corpus fallback")

    plain = root / "unstamped"
    build_corpus(plain, 12, stamped=False)
    entries, timing = scan_corpus(plain)
    check("falls back to filename order", timing == "filename", f"timing={timing}")
    names = [e.path.name for e in entries]
    check("natural sort puts img2 before img10",
          names == [f"img{i}.jpg" for i in range(12)], f"{names[:4]} … {names[-2:]}")

    frames, marks = asyncio.run(collect(ReplaySource(plain, speed=20.0), 12))
    check("fallback emits in sorted order",
          digests(f.jpeg for f in frames) == digests(make_jpeg(i) for i in range(12)))
    check("fallback still emits at a fixed interval",
          all(f.meta["src_t"] is None for f in frames)
          and abs(statistics.fmean(b - a for a, b in zip(marks, marks[1:])) - 0.05) < 0.010,
          f"mean gap {statistics.fmean(b - a for a, b in zip(marks, marks[1:])) * 1000:.2f} ms")


def check_stamp_parser(root: Path) -> None:
    section("A3 replay — filename timestamp convention")

    cases = {
        "frame_1757700842250.jpg": 1757700842.250,   # canonical: unix millis
        "frame_1757700842.jpg": 1757700842.0,        # epoch seconds
        "frame_1757700842.250.jpg": 1757700842.250,  # fractional seconds
        "20260912T190402Z.jpg": 1789239842.0,        # ISO-8601 basic, UTC
        "frame_20260912T190402_250Z.jpg": 1789239842.250,
        "1757700842250.jpg": 1757700842.250,         # bare stamp
        "seated-0003-1757700842250.jpg": 1757700842.250,
    }
    ok = True
    for name, want in cases.items():
        got = parse_stamp(name)
        if got is None or abs(got - want) > 1e-6:
            ok = False
            print(f"      {name!r}: got {got!r}, want {want!r}")
    check("all documented filename forms parse", ok, f"{len(cases)} forms")
    check("unstamped names return None", parse_stamp("photo.jpg") is None)
    check("short digit runs are not mistaken for epochs", parse_stamp("img0042.jpg") is None)

    empty = root / "empty"
    empty.mkdir(exist_ok=True)
    (empty / "README.txt").write_text("no frames here")
    (empty / "broken.jpg").write_bytes(b"not actually a jpeg")
    try:
        scan_corpus(empty)
        check("empty/garbage corpus raises ReplayCorpusError", False)
    except ReplayCorpusError as exc:
        check("empty/garbage corpus raises ReplayCorpusError", True, str(exc)[:70])
    try:
        scan_corpus(root / "does-not-exist")
        check("missing directory raises ReplayCorpusError", False)
    except ReplayCorpusError:
        check("missing directory raises ReplayCorpusError", True)


# --- A9: webcam ---------------------------------------------------------------


class _FakeCapture:
    """Stands in for cv2.VideoCapture. Produces a new numbered frame on every read."""

    def __init__(self) -> None:
        self.n = 0
        self.released = False

    def read(self):
        self.n += 1
        img = np.full((16, 16, 3), self.n % 256, dtype=np.uint8)
        time.sleep(0.002)  # a camera is not infinitely fast either
        return True, img

    def release(self) -> None:
        self.released = True


def check_webcam_interface() -> None:
    section("A9 webcam — interface conformance")

    # The optional extra must stay optional: importing the package, and constructing a
    # source, must not pull opencv in. Only open() may.
    check("importing sources does not import opencv", "cv2" not in sys.modules,
          "cv2 absent from sys.modules")

    src = WebcamSource(0)
    check("WebcamSource is a CaptureSource", isinstance(src, CaptureSource))
    check("name is 'webcam'", src.name == "webcam", src.name)
    check("frames() returns an async iterator, not a coroutine",
          hasattr(src.frames(), "__anext__"))
    check("implements aclose()", asyncio.iscoroutinefunction(src.aclose))
    asyncio.run(src.aclose())
    asyncio.run(src.aclose())  # invariant: safe to call twice
    check("aclose() is idempotent", True)


def check_webcam_encode() -> None:
    section("A9 webcam — encode path (no camera needed)")

    check("constants match §2.2", (TARGET_PX, JPEG_QUALITY) == (512, 70),
          f"{TARGET_PX} px, q{JPEG_QUALITY}")

    rng = np.random.default_rng(7)
    # A 720p BGR frame, structured rather than pure noise so the byte size is realistic.
    yy, xx = np.mgrid[0:720, 0:1280].astype(np.float32)
    base = (np.sin(xx / 40) * 60 + np.cos(yy / 30) * 60 + 128)
    frame = np.stack([base, base * 0.8 + 30, base * 0.6 + 60], axis=-1)
    frame = np.clip(frame + rng.normal(0, 6, frame.shape), 0, 255).astype(np.uint8)

    jpeg = encode_jpeg(frame)
    img = Image.open(io.BytesIO(jpeg))
    img.load()
    check("output is a decodable JPEG", img.format == "JPEG", f"{img.format} {img.mode}")
    check("long side resized to 512 px", max(img.size) == TARGET_PX, f"{img.size}")
    check("aspect ratio preserved (1280x720 -> 512x288)", img.size == (512, 288), f"{img.size}")
    print(f"      encoded {len(jpeg) / 1024:.1f} KB from a 1280x720 source (§2.2 says ~40 KB)")

    ref70 = _pil_reference(frame, 70)
    check("bytes are exactly a PIL q70 encode", jpeg == ref70,
          f"{len(jpeg)} B vs reference {len(ref70)} B")
    check("q70 is not silently q50 or q90",
          jpeg != _pil_reference(frame, 50) and jpeg != _pil_reference(frame, 90))

    # Channel order. cv2 hands back BGR; treating it as RGB would swap red and blue and
    # quietly wreck `cct` (A5), with no error anywhere.
    blue_bgr = np.zeros((64, 64, 3), dtype=np.uint8)
    blue_bgr[:, :, 0] = 240  # B channel in BGR
    out = np.asarray(Image.open(io.BytesIO(encode_jpeg(blue_bgr))).convert("RGB"))
    mean = out.reshape(-1, 3).mean(axis=0)
    check("BGR input is converted to RGB before encoding", mean[2] > 200 > mean[0],
          f"decoded mean RGB {mean.round(1).tolist()}")

    small = np.zeros((240, 320, 3), dtype=np.uint8)
    check("smaller-than-512 input is not upscaled",
          Image.open(io.BytesIO(encode_jpeg(small))).size == (320, 240))
    check("grayscale input is accepted",
          Image.open(io.BytesIO(encode_jpeg(np.zeros((600, 800), np.uint8)))).size == (512, 384))


def _pil_reference(bgr: np.ndarray, quality: int) -> bytes:
    img = Image.fromarray(bgr[:, :, ::-1], "RGB").resize((512, 288), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=2, optimize=False,
             progressive=False)
    return buf.getvalue()


def check_webcam_drop_rule() -> None:
    section("A9 webcam — drop, never queue (fake capture)")

    cap = _FakeCapture()
    reader = _LatestFrameReader(cap)
    reader.start()
    try:
        if not reader.wait_first(2.0):
            check("reader produces a frame", False)
            return
        time.sleep(0.25)  # let a backlog build, if one can
        first = reader.snapshot()
        time.sleep(0.25)
        second = reader.snapshot()
        assert first is not None and second is not None
        check("snapshot returns the newest frame, not the oldest",
              second.read_seq > first.read_seq + 1,
              f"read_seq {first.read_seq} -> {second.read_seq}")
        check("intermediate frames are dropped, not queued", second.dropped > 0,
              f"{second.dropped} frames discarded between samples")
        check("nothing accumulates in the reader", reader._slot is not None
              and isinstance(reader._slot.image, np.ndarray))
    finally:
        reader.stop()
    check("reader thread stops on request", not reader._thread.is_alive())


def check_webcam_live() -> None:
    section("A9 webcam — live camera")

    src = WebcamSource(0, warmup_s=4.0)
    outcome: dict[str, object] = {}

    def _open() -> None:
        try:
            src.open()
            outcome["ok"] = True
        except BaseException as exc:  # noqa: BLE001 - the whole point is to not die
            outcome["err"] = exc

    # Own daemon thread, not to_thread: a wedged camera driver must not keep the
    # interpreter alive at exit.
    th = threading.Thread(target=_open, daemon=True)
    th.start()
    th.join(12.0)

    if th.is_alive():
        record(SKIP, "open default camera", "open() did not return within 12 s")
        return
    if "err" in outcome:
        exc = outcome["err"]
        kind = type(exc).__name__ if not isinstance(exc, CameraUnavailableError) else "unavailable"
        record(SKIP, "open default camera", f"{kind}: {exc}")
        return

    check("default camera opened", True)
    frames, marks = asyncio.run(collect(src, 3))
    check("live frames captured", len(frames) == 3)
    check("live device is None (§12.1)", all(f.device is None for f in frames))
    sizes = []
    for f in frames:
        img = Image.open(io.BytesIO(f.jpeg))
        img.load()
        sizes.append(img.size)
    check("live frames are 512 px q70 JPEGs", all(max(s) <= TARGET_PX for s in sizes),
          f"sizes {sizes}, bytes {[len(f.jpeg) for f in frames]}")
    if len(marks) > 1:
        mean, worst = timing_report(marks, 1.0, "live camera")
        check("live cadence is ~1 Hz", 0.95 <= mean <= 1.10, f"{mean:.4f} s")
    print(f"      raw camera size {frames[0].meta['raw_size']}, "
          f"dropped between samples: {[f.meta['dropped'] for f in frames]}")


# --- main ---------------------------------------------------------------------


def main() -> int:
    # Adapter warnings ("skipping X — not a JPEG") are part of what is being checked,
    # so send them to stdout where they interleave with the results in order.
    logging.basicConfig(level=logging.WARNING, format="      log: %(message)s",
                        stream=sys.stdout)
    print("check_sources — A3 replay adapter, A9 webcam adapter")
    with tempfile.TemporaryDirectory(prefix="check_sources_") as tmp:
        root = Path(tmp)
        corpus = root / "corpus"
        expected = build_corpus(corpus, 20)
        (corpus / "notes.txt").write_text("ignored")
        (corpus / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (corpus / "truncated.jpg").write_bytes(b"\x00\x01\x02")  # filtered by SOI check
        print(f"  synthetic corpus: {corpus} ({len(expected)} frames + 3 decoys)")

        check_replay_order_and_shape(corpus, expected)
        check_stamp_parser(root)
        check_replay_recorded_spacing(root)
        check_replay_fallback(root)
        check_replay_speed(corpus)
        check_replay_cadence(corpus)

        check_webcam_interface()
        check_webcam_encode()
        check_webcam_drop_rule()
        check_webcam_live()

    passed = sum(1 for s, _, _ in _results if s == PASS)
    failed = [r for r in _results if r[0] == FAIL]
    skipped = [r for r in _results if r[0] == SKIP]
    print(f"\n{len(_results)} checks: {passed} passed, {len(failed)} failed, "
          f"{len(skipped)} skipped")
    for _, name, detail in skipped:
        print(f"  SKIP {name} — {detail}")
    for _, name, detail in failed:
        print(f"  FAIL {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
