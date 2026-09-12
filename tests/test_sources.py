"""Lane 4 — the `replay` (A3) and `webcam` (A9) capture adapters.

The camera-dependent parts of A9 are covered by `test_webcam_encode_*`, which need no
camera. Only `test_webcam_live_*` opens a device, and it skips when there isn't one.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import statistics
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


def test_replay_holds_cadence_without_drift(corpus):
    """A3 done-when: a steady 1 Hz.

    Run at speed=20 so the test costs ~0.5 s rather than 12 s. Drift is a property of
    the pacing algorithm, not the interval, so a compressed run still catches a
    `sleep(interval)`-in-a-loop regression.
    """
    src = ReplaySource(corpus, speed=20.0)
    stamps = []

    async def run():
        async for _ in src.frames():
            stamps.append(time.monotonic())

    asyncio.run(run())
    step = src.interval / src.speed
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    elapsed = stamps[-1] - stamps[0]
    ideal = step * (len(stamps) - 1)

    assert statistics.mean(gaps) == pytest.approx(step, abs=step * 0.25)
    # Cumulative drift is the real assertion: overshoot must not accumulate.
    assert abs(elapsed - ideal) < step * 0.5


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
