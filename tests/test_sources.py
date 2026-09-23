"""Lane 4 — the `replay` (A3) and `webcam` (A9) capture adapters.

The camera-dependent parts of A9 are covered by `test_webcam_encode_*`, which need no
camera. Only `test_webcam_live_*` opens a device, and it skips when there isn't one.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time

import numpy as np
import pytest
from PIL import Image

from longevity.sources.replay import (
    ReplayCorpusError,
    ReplaySource,
    parse_stamp,
    scan_corpus,
)
from longevity.sources.webcam import TARGET_PX, CameraUnavailableError, WebcamSource, encode_jpeg


# --- fixtures -----------------------------------------------------------------

T0 = 1757700842.0


@pytest.fixture
def corpus(tmp_path):
    """A 12-frame corpus in the canonical `frame_<unix_millis>.jpg` form."""
    for i in range(12):
        img = Image.new("RGB", (512, 384), (20 + i * 15, 60, 120 - i * 5))
        img.save(tmp_path / f"frame_{int((T0 + i) * 1000)}.jpg", quality=70)
    return tmp_path


async def collect(src, limit=None):
    out = []
    async for f in src.frames():
        out.append(f)
        if limit is not None and len(out) >= limit:
            break
    await src.aclose()
    return out


# --- replay: the corpus contract ---------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("frame_1757700842250.jpg", 1757700842.25),
        ("frame_1757700842.jpg", 1757700842.0),
        ("frame_1757700842.250.jpg", 1757700842.25),
        ("1757700842250.jpg", 1757700842.25),
        ("IMG_0042.JPG", None),
    ],
)
def test_parse_stamp(name, expected):
    assert parse_stamp(name) == expected


def test_scan_uses_stamped_timing_for_canonical_names(corpus):
    entries, timing = scan_corpus(corpus)
    assert timing == "stamped"
    assert len(entries) == 12


def test_scan_falls_back_wholesale_when_any_stamp_is_missing(corpus):
    """Mixed modes are never used — half-real timestamps are worse than none."""
    (corpus / "IMG_0042.jpg").write_bytes((corpus / "frame_1757700842000.jpg").read_bytes())
    _, timing = scan_corpus(corpus)
    assert timing == "filename"


# --- replay: SPEC guarantees --------------------------------------------------


def test_replay_omits_device_block(corpus):
    """SPEC §12.1: the tick's `device` block is absent under `replay`."""
    frames = asyncio.run(collect(ReplaySource(corpus, speed=100.0)))
    assert all(f.device is None for f in frames)


def test_replay_timestamps_are_live_not_recorded(corpus):
    """`Frame.t` must be wall-clock now.

    The corpus is stamped 2025; the ring buffer's 90 s TTL (§12.3) and the `ai` block's
    `age_ms` are both measured against `Frame.t`, so emitting recorded times would
    expire every frame_ref on arrival.
    """
    before = time.time()
    frames = asyncio.run(collect(ReplaySource(corpus, speed=100.0)))
    after = time.time()
    assert all(before <= f.t <= after for f in frames)
    assert frames[0].meta["src_t"] == pytest.approx(T0)


def test_replay_is_deterministic(corpus):
    """Same directory, same frames, same order, same bytes, every run."""
    runs = [
        [hashlib.sha256(f.jpeg).hexdigest() for f in asyncio.run(collect(ReplaySource(corpus, speed=100.0)))]
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert len(runs[0]) == 12


# speed=10 makes a step 0.1 s. Not faster: Windows' default timer tick is 15.6 ms, and on
# Python 3.11 `time.monotonic()` (the clock asyncio schedules on) ticks at the same
# 15.6 ms, so any one wake-up can land a tick or two late.
CADENCE_SPEED = 10.0
# The consumer's per-frame work, as a fraction of a step. It blocks the loop, like a
# downstream stage that is slow but keeps up.
CADENCE_WORK = 0.25


async def _stamp_with_work(frames, work_s):
    """Stamp each frame on arrival, then hold the event loop for `work_s`."""
    stamps = []
    async for _ in frames:
        stamps.append(time.perf_counter())  # QPC; monotonic() is a 15.6 ms tick here
        while time.perf_counter() < stamps[-1] + work_s:
            pass
    return stamps


def _drift(stamps, step):
    """How far the schedule slid from the first half of the run to the second, in seconds.

    `stamps[i] - i * step` is where frame 0 would have been had every gap been exactly
    `step`. A deadline pacer keeps that anchor put: each frame is late by its own
    wake-up and no more. A sleep-in-a-loop pacer walks it forward by every frame's work
    and overshoot. Lateness only adds, so the earliest anchor in each half is the one
    that shrugs off a stall, including a slow first frame while the thread pool spins up.
    """
    anchors = [t - i * step for i, t in enumerate(stamps)]
    half = len(anchors) // 2
    return min(anchors[half:]) - min(anchors[:half])


def test_replay_holds_cadence_without_drift(corpus):
    """A3 done-when: a steady 1 Hz, and a slow consumer does not push the schedule.

    Run at speed=10 so the test costs ~1.1 s rather than 12 s. The step is 0.1 s because
    of timer resolution (see CADENCE_SPEED): at speed=20 the old ±25 % mean-gap bound was
    12.5 ms, under one 15.6 ms tick, and a single late wake-up on a loaded machine failed
    it. At 0.1 s the drift bound is three ticks.

    Drift is a property of the pacing algorithm, not the interval, so a compressed run
    still catches a `sleep(interval)`-in-a-loop regression;
    `test_cadence_check_catches_sleep_in_a_loop` proves this assertion fails for one.
    """
    src = ReplaySource(corpus, speed=CADENCE_SPEED)
    step = src.interval / src.speed
    stamps = asyncio.run(_stamp_with_work(src.frames(), step * CADENCE_WORK))

    assert len(stamps) == 12
    # Cumulative drift is the real assertion: overshoot must not accumulate. It also
    # holds the average gap well inside the old ±25 % bound.
    assert abs(_drift(stamps, step)) < step * 0.5


def test_cadence_check_catches_sleep_in_a_loop():
    """The drift bound above must fail for the pacer ReplaySource exists to avoid.

    Same consumer, step, frame count and assertion; only the pacer differs. The
    consumer's work lands between sleeps, so every gap is at least step + work, and the
    anchor walks 6 x 25 ms between halves against a 50 ms bound. asyncio cannot shave
    that much off a sleep, so this does not flake.
    """
    step = 1.0 / CADENCE_SPEED

    async def naive(n):
        for _ in range(n):
            yield None
            await asyncio.sleep(step)

    stamps = asyncio.run(_stamp_with_work(naive(12), step * CADENCE_WORK))

    assert abs(_drift(stamps, step)) >= step * 0.5


def test_replay_loops(corpus):
    frames = asyncio.run(collect(ReplaySource(corpus, speed=100.0, loop=True), limit=30))
    assert len(frames) == 30
    assert frames[0].meta["pass"] == 0
    assert frames[-1].meta["pass"] == 2


def test_replay_rejects_bad_arguments(corpus):
    for kwargs in ({"speed": 0}, {"speed": -1}, {"interval": 0}):
        with pytest.raises(ValueError):
            ReplaySource(corpus, **kwargs)


def test_replay_validates_lazily_not_at_construction(tmp_path):
    """Constructing a source must not touch the disk; iterating it may.

    This keeps `--source replay --dir ...` cheap to wire up and puts the corpus error
    where the caller can actually report it.
    """
    src = ReplaySource(tmp_path)  # empty dir, no raise
    with pytest.raises(ReplayCorpusError):
        asyncio.run(collect(src))

    missing = ReplaySource(tmp_path / "does_not_exist")
    with pytest.raises(ReplayCorpusError):
        asyncio.run(collect(missing))


# --- webcam: the encode contract (no camera) ---------------------------------


def test_webcam_encode_matches_the_phone_target():
    """§2.2 / §11.4: 512 px long side, q70 — the same shape the phone produces."""
    jpeg = encode_jpeg(np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8))
    assert max(Image.open(io.BytesIO(jpeg)).size) == TARGET_PX


def test_webcam_encode_is_downscale_only():
    """A camera smaller than 512 px is left alone rather than upscaled into fake detail."""
    jpeg = encode_jpeg(np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8))
    assert Image.open(io.BytesIO(jpeg)).size == (320, 240)


def test_webcam_encode_handles_grayscale():
    """Grayscale in, JPEG out. 640 px is over target, so it downscales like any frame."""
    jpeg = encode_jpeg(np.random.randint(0, 255, (480, 640), dtype=np.uint8))
    assert max(Image.open(io.BytesIO(jpeg)).size) == TARGET_PX
    small = encode_jpeg(np.random.randint(0, 255, (240, 320), dtype=np.uint8))
    assert Image.open(io.BytesIO(small)).size == (320, 240)


# --- webcam: live (skips without a camera) -----------------------------------


def _camera_available() -> bool:
    try:
        frames = asyncio.run(collect(WebcamSource(0), limit=1))
    except (CameraUnavailableError, Exception):
        return False
    return bool(frames)


@pytest.mark.skipif(not _camera_available(), reason="no camera / no permission")
def test_webcam_live_produces_glasses_shaped_frames():
    """A9 done-when: identically shaped to the glasses path, minus `device`."""
    frames = asyncio.run(collect(WebcamSource(0), limit=3))
    assert len(frames) == 3
    for f in frames:
        assert f.device is None  # §12.1
        assert max(Image.open(io.BytesIO(f.jpeg)).size) <= TARGET_PX
        assert f.jpeg[:2] == b"\xff\xd8"  # JPEG SOI
