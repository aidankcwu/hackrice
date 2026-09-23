"""Sensor field computation (docs/PERSON_A.md A5) — the tick's `sensor` block (SPEC §12).

Seven numbers per frame, derived from the decoded JPEG and nothing else:

    lux_proxy   cct   hist_spread   frame_delta   flow_mag   sharpness   phash

§12.1 guarantees this block is **always present**, so nothing in here may raise.
An undecodable or truncated JPEG returns `empty_sensor_block()` and the clock keeps
running — a tick with zeroed sensors is recoverable, a missing tick is not.

Invariant 1 — T0 never blocks. Everything here is synchronous, allocation-light and
bounded by a fixed working resolution (`WORK_PX`), so cost does not track the input
frame size. Measured on this laptop at ~2 ms for the whole block; `tools/check_sensors.py`
prints the current numbers per field. Do not trust that figure, re-run the tool.

Two fields are named more confidently than they deserve:

  * `lux_proxy` is **not lux** (§12.1). It is relative luminance from an auto-exposed
    JPEG, and auto-exposure is actively fighting the thing we are trying to measure.
    Usable for indoor/outdoor and bright/dim discrimination, and for nothing else.
  * `cct` is estimated from an auto-*white-balanced* JPEG, which has the same problem
    one step further along: AWB pulls the image toward neutral, so the estimate
    understates how warm or cool the real illuminant was. Ordinal evidence at best.

State: `frame_delta` and `flow_mag` are inter-frame, so `SensorComputer` carries the
previous frame's 64x64 luma grid. One instance per capture session, not thread-safe,
not shared between adapters.
"""

from __future__ import annotations

import math
import os
from io import BytesIO
from time import perf_counter
from typing import Any

import numpy as np
from PIL import Image

# The field names, in §12 order, in exactly one place. If §12 gains a sensor field
# it gets added here and in `compute()`; nothing else in the pipeline names them.
SENSOR_FIELDS: tuple[str, ...] = (
    "lux_proxy",
    "cct",
    "hist_spread",
    "frame_delta",
    "flow_mag",
    "sharpness",
    "phash",
)

# Fixed working resolution. Every frame is normalised to this before any statistic is
# computed, which (a) bounds the cost regardless of what the adapter hands us and
# (b) makes `sharpness` comparable across adapters — a variance-of-Laplacian figure
# is meaningless if the resolution moves under it. 256 divides evenly by both the
# motion grid and the hash grid, so the downsamples below are exact reshape-means.
WORK_PX = 256
THUMB_PX = 64  # motion grid for frame_delta / flow_mag
HASH_PX = 32   # pHash DCT input
HASH_BITS = 64

# McCamy's approximation is fitted around 2000–12500 K. Clamp wide enough to keep
# weird frames legible but narrow enough that a degenerate chromaticity can't emit
# a 10^6 K tick field.
CCT_MIN = 1500
CCT_MAX = 20000

# lux_proxy is reported on a 0–1000 scale: relative luminance (0..1, linear light)
# times 1000. 1000 is a fully clipped white frame. Reference points from the spec
# examples: §12 shows 340, the §4.2 envelope shows ~205–210 for a seated indoor
# scene, both of which this formula reproduces for those scenes.
LUX_SCALE = 1000

_FLOW_MODES = ("numpy", "opencv", "off")


def _srgb_linear_lut() -> np.ndarray:
    """sRGB EOTF over the 256 possible 8-bit codes — gamma-encoded value → linear light."""
    c = np.arange(256, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return lin.astype(np.float32)


def _dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II basis, so `D @ img @ D.T` is a separable 2-D DCT."""
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n))
    m[0] *= math.sqrt(1.0 / n)
    m[1:] *= math.sqrt(2.0 / n)
    return m.astype(np.float32)


_LINEAR_LUT = _srgb_linear_lut()
_DCT = _dct_matrix(HASH_PX)
# Hann window for phase correlation. Without it the frame edges act as a hard
# rectangular aperture and the correlation peak sits on the frame border instead of
# on the real translation.
_HANN = np.outer(np.hanning(THUMB_PX), np.hanning(THUMB_PX)).astype(np.float32)

# Returned whenever the frame cannot be decoded. All seven keys present (§12.1).
# phash is all-zero rather than carried forward from the previous frame: a stale hash
# would quietly corrupt B's frame selection (§4.3), which is exactly the failure mode
# nothing downstream can detect.
_EMPTY: dict[str, Any] = {
    "lux_proxy": 0,
    "cct": 0,
    "hist_spread": 0.0,
    "frame_delta": 0.0,
    "flow_mag": 0.0,
    "sharpness": 0,
    "phash": "0" * (HASH_BITS // 4),
}


def empty_sensor_block() -> dict[str, Any]:
    """A full, sane `sensor` block for a frame that could not be decoded."""
    return dict(_EMPTY)


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex phashes, in bits.

    B selects the four escalation frames by this distance (§4.3, §12.3); it lives here
    so both sides compute it the same way. Mismatched-length or non-hex input scores
    the maximum distance rather than raising — an undecodable frame should look
    maximally different, not crash the selector.
    """
    try:
        return int(int(a, 16) ^ int(b, 16)).bit_count()
    except (TypeError, ValueError):
        return HASH_BITS


def quick_quality(jpeg: bytes, work_px: int = 128) -> dict[str, float]:
    """`sharpness` and `lux_proxy` only, from a `work_px` decode. Never raises.

    The watcher's quality gate on frames that never become a tick (the glasses packet
    hook sees every frame the phone sends; only one per interval gets the full block).
    Same formulas as `SensorComputer.compute`, but at 128 px: `lux_proxy` is a mean
    and barely moves with resolution; `sharpness` (Laplacian variance) is scale-dependent,
    so a 128 px figure is not the same number as the tick's 256 px one (the downscale
    averages away pixel noise and compresses real edges). Good enough for "is this
    frame usable at all"; not for comparing against a tick. An undecodable frame
    scores 0/0, which the gate rejects.

    Measured 2026-09-23 on this Mac with a 512x288 q70 JPEG (~42 KB): 0.45 ms per
    call, against 1.5 ms for the full seven-field block on the same frame.
    """
    try:
        rgb, gray = SensorComputer(flow="off", work_px=work_px)._decode(jpeg)
    except Exception:
        return {"sharpness": 0.0, "lux_proxy": 0.0}
    n = rgb.shape[0] * rgb.shape[1]
    flat = rgb.reshape(-1, 3)
    lin = [float(np.dot(np.bincount(flat[:, c], minlength=256), _LINEAR_LUT)) / n for c in range(3)]
    lux_proxy = min(1.0, 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]) * LUX_SCALE
    g = gray.astype(np.float32)
    lap = 4.0 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return {"sharpness": float(round(float(lap.var()))), "lux_proxy": float(round(lux_proxy))}


class SensorComputer:
    """Turns a JPEG into the §12 `sensor` block, carrying the inter-frame state.

    Construct one per capture session::

        sensors = SensorComputer()
        block = sensors.compute(frame.jpeg)     # 7 fields, always

    `flow` selects the `flow_mag` backend — see `_flow_numpy` for why the default is
    numpy. Pass "off" (or set LONGEVITY_FLOW=off) when B confirms nothing reads the
    field; it is first on the cut list (docs/PERSON_A.md).
    """

    def __init__(self, *, flow: str | None = None, work_px: int = WORK_PX) -> None:
        self.work_px = int(work_px)
        self.frames_seen = 0
        self.decode_failures = 0

        mode = (flow or os.environ.get("LONGEVITY_FLOW") or "numpy").lower()
        if mode not in _FLOW_MODES:
            mode = "numpy"
        self._cv2 = None
        if mode == "opencv":
            # Lazy, one-off, at construction — never at module import and never in the
            # per-frame path. SPEC §11.5: OpenCV "only if optical flow is actually
            # needed". Missing extra → documented fallback, not a crash.
            try:
                import cv2  # type: ignore[import-not-found]

                self._cv2 = cv2
            except ImportError:
                mode = "numpy"
        self.flow_mode = mode

        self._prev_thumb: np.ndarray | None = None

    def reset(self) -> None:
        """Forget the previous frame — the next frame reports zero motion."""
        self._prev_thumb = None

    def compute(self, jpeg: bytes, *, timings: dict[str, float] | None = None) -> dict[str, Any]:
        """The whole `sensor` block for one frame. Never raises.

        Pass `timings` to have per-stage milliseconds written into it (used by
        tools/check_sensors.py). The hot path pays two branch checks for that.
        """
        self.frames_seen += 1
        clock = perf_counter if timings is not None else None

        t = clock() if clock else 0.0
        try:
            rgb, gray = self._decode(jpeg)
        except Exception:
            # Truncated packet, non-image bytes, decoder bomb — all the same answer.
            self.decode_failures += 1
            if timings is not None:
                timings["decode"] = (clock() - t) * 1e3  # type: ignore[misc]
            return empty_sensor_block()
        if clock:
            timings["decode"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        # --- lux_proxy + cct: both fall out of the three per-channel linear means ---
        n = rgb.shape[0] * rgb.shape[1]
        flat = rgb.reshape(-1, 3)
        lin = [float(np.dot(np.bincount(flat[:, c], minlength=256), _LINEAR_LUT)) / n for c in range(3)]
        # Rec.709 relative luminance on linear-light channel means. Linearising via a
        # 256-entry LUT over the channel histogram means the gamma curve is applied
        # per pixel (correct) rather than to the mean (wrong), for the cost of three
        # bincounts.
        lux_proxy = int(round(min(1.0, 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]) * LUX_SCALE))
        if clock:
            timings["lux_proxy"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        cct = _cct_mccamy(lin[0], lin[1], lin[2])
        if clock:
            timings["cct"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        # --- hist_spread: how much of the 8-bit range the luma histogram occupies ---
        # p5..p95 span / 255, so a couple of blown highlights or one black corner do
        # not peg it at 1.0. Low = flat/foggy/dark-wall frame, high = full-range scene.
        hist = np.bincount(gray.ravel(), minlength=256)
        cdf = np.cumsum(hist)
        lo = int(np.searchsorted(cdf, 0.05 * n, side="left"))
        hi = int(np.searchsorted(cdf, 0.95 * n, side="left"))
        hist_spread = max(0.0, min(1.0, (hi - lo) / 255.0))
        if clock:
            timings["hist_spread"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        # --- sharpness: variance of the 4-neighbour Laplacian, at WORK_PX ---
        # The classic focus measure. Unitless and only comparable within this pipeline
        # (hence the fixed working resolution). Near zero = blurred, out of focus, or
        # staring at a blank wall; hundreds = crisp textured scene.
        g = gray.astype(np.float32)
        lap = 4.0 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
        sharpness = int(round(float(lap.var())))
        if clock:
            timings["sharpness"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        # --- frame_delta: mean absolute luma change on a 64x64 grid, 0..1 ---
        # Box-downsampling first is deliberate: at full resolution this measures JPEG
        # noise and one-pixel head jitter. It conflates real motion with exposure
        # change, which is fine — both mean "the scene the VLM saw is no longer true".
        thumb = gray.reshape(THUMB_PX, self.work_px // THUMB_PX, THUMB_PX, self.work_px // THUMB_PX).mean(
            axis=(1, 3), dtype=np.float32
        )
        prev = self._prev_thumb
        frame_delta = 0.0 if prev is None else float(np.abs(thumb - prev).mean()) / 255.0
        if clock:
            timings["frame_delta"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        flow_mag = 0.0
        if prev is not None and self.flow_mode != "off":
            flow_mag = self._flow_opencv(prev, thumb) if self._cv2 is not None else _flow_numpy(prev, thumb)
        self._prev_thumb = thumb
        if clock:
            timings["flow_mag"] = (clock() - t) * 1e3  # type: ignore[index]
            t = clock()

        phash = _phash(gray, self.work_px)
        if clock:
            timings["phash"] = (clock() - t) * 1e3  # type: ignore[index]

        return {
            "lux_proxy": lux_proxy,
            "cct": cct,
            "hist_spread": round(hist_spread, 3),
            "frame_delta": round(frame_delta, 3),
            "flow_mag": round(flow_mag, 3),
            "sharpness": sharpness,
            "phash": phash,
        }

    # -- internals ---------------------------------------------------------------

    def _decode(self, jpeg: bytes) -> tuple[np.ndarray, np.ndarray]:
        """JPEG bytes → (WORK_PX x WORK_PX x 3 uint8, WORK_PX x WORK_PX uint8 luma).

        `draft()` asks libjpeg to scale during the DCT-domain decode, so a 512 px
        capture is decoded straight to 256 px rather than decoded and then resized.
        It is a hint, not a guarantee (and a no-op for non-JPEG), so the resize below
        still runs when the size did not land exactly. Aspect ratio is deliberately
        not preserved: every consumer here is a statistic or a hash, none is a picture.
        """
        im = Image.open(BytesIO(jpeg))
        im.draft("RGB", (self.work_px, self.work_px))
        im = im.convert("RGB")
        if im.size != (self.work_px, self.work_px):
            im = im.resize((self.work_px, self.work_px), Image.BOX)
        return np.asarray(im, dtype=np.uint8), np.asarray(im.convert("L"), dtype=np.uint8)

    def _flow_opencv(self, prev: np.ndarray, cur: np.ndarray) -> float:
        """Dense Farneback flow, mean magnitude as a fraction of frame width.

        Only reachable with `flow="opencv"` and the `flow` extra installed. Measures
        non-translational motion (someone walking across a static scene) that the
        numpy path cannot see, for roughly 4x the cost. Same units as `_flow_numpy`.
        """
        f = self._cv2.calcOpticalFlowFarneback(
            prev.astype(np.uint8), cur.astype(np.uint8), None, 0.5, 2, 9, 2, 5, 1.1, 0
        )
        return float(np.sqrt((f * f).sum(axis=-1)).mean()) / THUMB_PX


def _flow_numpy(prev: np.ndarray, cur: np.ndarray) -> float:
    """Global translation between two 64x64 luma grids, by phase correlation.

    **This is the default, and it is numpy-only on purpose.** SPEC §11.5 allows
    OpenCV "only if optical flow is actually needed", and `flow_mag` is first on the
    cut list, so the shipped path must not drag in the dependency. Phase correlation
    is one rfft2 pair plus an argmax (~0.1 ms) and, unlike a gradient method, it
    survives the large displacements you get between frames a full second apart.

    Limits, since the number is going into a tick B will threshold on:
      * it estimates *one* global translation — head turn, walking. Motion inside a
        still frame reads as near zero. Use `frame_delta` for "did anything change".
      * it saturates at half a grid width (0.5 here) and aliases past that; a hard
        cut or a fast head whip is "big", not a meaningful magnitude.
      * units are fraction of frame width per tick interval (~1 s at 1 Hz).
    """
    a = np.fft.rfft2(prev * _HANN)
    b = np.fft.rfft2(cur * _HANN)
    cross = b * np.conj(a)
    cross /= np.abs(cross) + 1e-9
    corr = np.fft.irfft2(cross, s=(THUMB_PX, THUMB_PX))
    dy, dx = divmod(int(np.argmax(corr)), THUMB_PX)
    if dy > THUMB_PX // 2:
        dy -= THUMB_PX
    if dx > THUMB_PX // 2:
        dx -= THUMB_PX
    return min(0.5, math.hypot(dx, dy) / THUMB_PX)


def _phash(gray: np.ndarray, work_px: int) -> str:
    """64-bit DCT perceptual hash, 16 lowercase hex chars (§12 example: 16 chars).

    Standard construction: box-downsample the luma to 32x32, 2-D DCT, keep the 8x8
    low-frequency corner, threshold each coefficient against the block median. Robust
    to exposure shifts, JPEG noise and small motion; moves sharply when the scene
    changes, which is the property B's escalation-frame selection depends on (§4.3).

    One deviation from the textbook version: the DC coefficient is replaced by
    `dct[0, 8]` before thresholding. DC is total brightness and is always far above
    the median of the AC coefficients around it, so the textbook hash spends one of
    its 64 bits on a constant. Substituting a neighbouring horizontal frequency keeps
    all 64 bits informative. Distances are only ever compared against hashes from
    this same function, so the deviation costs nothing.
    """
    s = gray.reshape(HASH_PX, work_px // HASH_PX, HASH_PX, work_px // HASH_PX).mean(axis=(1, 3), dtype=np.float32)
    d = _DCT @ s @ _DCT.T
    low = d[:8, :8].ravel().copy()
    low[0] = d[0, 8]
    bits = low > np.median(low)
    return np.packbits(bits).tobytes().hex()


def _cct_mccamy(r_lin: float, g_lin: float, b_lin: float) -> int:
    """Correlated colour temperature in Kelvin from mean linear sRGB.

    Mean linear RGB → CIE XYZ (sRGB/D65 primaries) → xy chromaticity → McCamy's 1992
    cubic in n = (x - 0.3320) / (0.1858 - y). A neutral grey frame returns ~6500 K
    (D65), which is the sanity check in tools/check_sensors.py.

    Warm scenes (tungsten, sunset) pull it down, daylight and screens push it up —
    but see the module docstring: the JPEG has already been auto-white-balanced, so
    the estimate is compressed toward neutral and should be read as a lean, not a
    measurement. 0 means the frame was too dark or too degenerate to say.
    """
    x_ = 0.4124 * r_lin + 0.3576 * g_lin + 0.1805 * b_lin
    y_ = 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin
    z_ = 0.0193 * r_lin + 0.1192 * g_lin + 0.9505 * b_lin
    total = x_ + y_ + z_
    if total <= 1e-6:
        return 0
    x = x_ / total
    y = y_ / total
    denom = 0.1858 - y
    if abs(denom) < 1e-6:
        return 0
    n = (x - 0.3320) / denom
    cct = 449.0 * n**3 + 3525.0 * n**2 + 6823.3 * n + 5520.33
    if not math.isfinite(cct):
        return 0
    return int(round(min(CCT_MAX, max(CCT_MIN, cct))))
