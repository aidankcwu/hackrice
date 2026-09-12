"""The `webcam` capture adapter (PERSON_A.md A9, SPEC §11.3, §13.4).

The laptop's own camera, shaped to look exactly like the glasses path. §13.4: build
this before the `glasses` adapter, because it proves the whole Python pipeline end to
end while the iOS app is still being provisioned.

"Identically shaped" is the whole point and it is a byte-level claim, not a vibe. The
phone resizes to 512 px and encodes JPEG q70 (~40 KB, §2.2); so does this module, with
the same numbers in one place. Sensor fields (A5) are statistics over decoded JPEG
pixels — `lux_proxy`, `sharpness`, `hist_spread` all move if the encode differs — so a
threshold tuned against webcam frames has to survive the move to glasses frames.

Two invariants shape the implementation:

  **Drop, never queue** (invariant 2). A capture driver will happily hand you a backlog:
  `VideoCapture.read()` returns the *oldest* buffered frame, so a loop that reads once a
  second drifts steadily further into the past. A reader thread therefore drains the
  camera at its native rate into a **single slot**, overwriting; the 1 Hz sampler takes
  whatever is in that slot. Discarded frames are counted, not kept. This mirrors A12 on
  the phone: stream at 2 fps, sample at 1 Hz, throw the other frame away.

  **T0 never blocks** (invariant 1). `read()` and JPEG encoding are blocking C calls.
  The read loop is its own thread and the encode goes through `asyncio.to_thread`, so
  the event loop is only ever awaiting.

`device is None` — §12.1, the tick's `device` block is absent under `webcam`.

OpenCV is an optional extra (`pip install 'longevity[webcam]'`) and is imported lazily
inside the functions that need it. Importing `longevity.sources` must stay free.
`encode_jpeg()` is deliberately camera-free — it takes a numpy array — so the encode
path is testable on a machine with no camera or no permission to open one.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from .base import CaptureSource, Frame

log = logging.getLogger(__name__)

# The encode contract, §2.2 / §13.4. These two numbers must match the phone's
# resize-to-512 / jpegData(compressionQuality: 0.7) path in A12. Changing either here
# without changing it there silently decouples every sensor threshold.
TARGET_PX = 512
JPEG_QUALITY = 70

# 4:2:0, stated rather than inherited, so frame size does not drift with the Pillow
# version. It is also what UIImage's JPEG encoder does.
_SUBSAMPLING = 2

DEFAULT_INTERVAL_S = 1.0

# How long to wait for the first frame after opening the device. Camera cold start
# (exposure, white balance) is routinely a second or more on a laptop.
_FIRST_FRAME_TIMEOUT_S = 5.0

# Backoff after a failed read inside the reader thread. Long enough not to spin a core
# on a camera that has been unplugged, short enough to be invisible at 1 Hz.
_READ_RETRY_S = 0.05
_MAX_CONSECUTIVE_READ_FAILURES = 100


class CameraUnavailableError(RuntimeError):
    """No camera, no permission, or no opencv. Callers may treat this as "skip"."""


def _import_cv2() -> Any:
    """Import opencv on demand. The optional extra stays optional."""
    try:
        import cv2  # noqa: PLC0415 — lazy on purpose, see module docstring
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise CameraUnavailableError(
            "opencv-python is not installed; `uv sync --extra webcam`"
        ) from exc
    return cv2


def encode_jpeg(
    image: np.ndarray,
    *,
    target_px: int = TARGET_PX,
    quality: int = JPEG_QUALITY,
    bgr: bool = True,
) -> bytes:
    """Resize to `target_px` on the long side and encode JPEG. The glasses contract.

    `image` is an (H, W, 3) array in OpenCV's BGR channel order by default, or (H, W)
    grayscale. Downscale only: a camera smaller than 512 px is left alone, because
    upscaling invents detail the phone path would never have produced.

    Blocking, a few milliseconds. Callers on the event loop go through a thread.
    """
    if image.ndim == 2:
        img = Image.fromarray(_as_uint8(image), mode="L").convert("RGB")
    elif image.ndim == 3 and image.shape[2] == 3:
        arr = _as_uint8(image)
        if bgr:
            arr = arr[:, :, ::-1]
        img = Image.fromarray(arr, mode="RGB")
    else:
        raise ValueError(f"expected (H, W) or (H, W, 3), got shape {image.shape}")

    w, h = img.size
    longest = max(w, h)
    if longest > target_px:
        scale = target_px / longest
        # round() over int() so a 1280x720 frame lands on 512x288, not 512x287.
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        img = img.resize(size, Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(
        buf,
        format="JPEG",
        quality=quality,
        subsampling=_SUBSAMPLING,
        optimize=False,
        progressive=False,
    )
    return buf.getvalue()


def _as_uint8(arr: np.ndarray) -> np.ndarray:
    """Cameras hand back uint8; a synthetic test frame might not."""
    if arr.dtype == np.uint8:
        return arr
    return np.clip(arr, 0, 255).astype(np.uint8)


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """The newest frame the reader has seen, plus how many were dropped to get it."""

    t: float
    image: np.ndarray
    read_seq: int
    dropped: int


class _LatestFrameReader:
    """Drains a `VideoCapture` into a one-slot buffer. The drop rule, implemented.

    The slot is overwritten on every read, so its occupant is always the newest frame
    and the backlog is always empty. Nothing here ever grows.
    """

    def __init__(self, cap: Any, *, name: str = "webcam-reader") -> None:
        self._cap = cap
        self._lock = threading.Lock()
        self._slot: _Snapshot | None = None
        self._first = threading.Event()
        self._stop = threading.Event()
        self._read_seq = 0
        self._taken_seq = -1
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            ok, image = self._cap.read()
            if not ok or image is None:
                failures += 1
                if failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                    log.error("webcam: %d consecutive failed reads, reader stopping", failures)
                    return
                self._stop.wait(_READ_RETRY_S)
                continue
            failures = 0
            with self._lock:
                self._read_seq += 1
                # Overwrite. The previous occupant is dropped, by design.
                self._slot = _Snapshot(
                    t=time.time(), image=image, read_seq=self._read_seq, dropped=0
                )
            self._first.set()

    def wait_first(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def snapshot(self) -> _Snapshot | None:
        """The newest frame, or None if the camera has not produced one yet."""
        with self._lock:
            snap = self._slot
            if snap is None:
                return None
            dropped = max(0, snap.read_seq - self._taken_seq - 1)
            self._taken_seq = snap.read_seq
        return _Snapshot(t=snap.t, image=snap.image, read_seq=snap.read_seq, dropped=dropped)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)


class WebcamSource(CaptureSource):
    """Sample the local camera at 1 Hz and emit glasses-shaped frames.

    Args:
        index: OpenCV device index. 0 is the built-in camera.
        interval: seconds between emissions. 1 Hz is the tick rate (§2.1).
        target_px: long-side resize, matching the phone (§2.2). Leave it at 512.
        quality: JPEG quality, matching the phone. Leave it at 70.
        warmup_s: how long to wait for the camera's first frame before giving up.
    """

    name = "webcam"

    def __init__(
        self,
        index: int = 0,
        *,
        interval: float = DEFAULT_INTERVAL_S,
        target_px: int = TARGET_PX,
        quality: int = JPEG_QUALITY,
        warmup_s: float = _FIRST_FRAME_TIMEOUT_S,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        self.index = int(index)
        self.interval = float(interval)
        self.target_px = int(target_px)
        self.quality = int(quality)
        self.warmup_s = float(warmup_s)
        self._cap: Any | None = None
        self._reader: _LatestFrameReader | None = None
        self._closed = False

    # --- camera lifecycle -----------------------------------------------------

    def open(self) -> None:
        """Open the device and block until it yields a frame. Blocking; raises.

        Failing here rather than mid-stream is deliberate: "no camera" and "no
        permission" are indistinguishable from the API and both are a startup
        condition, not a runtime one.
        """
        if self._cap is not None:
            return
        cv2 = _import_cv2()
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            cap.release()
            raise CameraUnavailableError(
                f"could not open camera index {self.index} "
                "(no device, or the OS denied camera permission)"
            )
        try:
            # Best effort — most backends honour it, AVFoundation quietly ignores it.
            # The reader thread is what actually guarantees freshness.
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # pragma: no cover - backend dependent
            pass

        reader = _LatestFrameReader(cap, name=f"webcam-{self.index}-reader")
        reader.start()
        if not reader.wait_first(self.warmup_s):
            reader.stop()
            cap.release()
            raise CameraUnavailableError(
                f"camera index {self.index} opened but produced no frame in "
                f"{self.warmup_s:.1f}s (permission prompt unanswered, or device busy)"
            )
        self._cap, self._reader = cap, reader

    # --- CaptureSource --------------------------------------------------------

    async def frames(self) -> AsyncIterator[Frame]:
        await asyncio.to_thread(self.open)
        reader = self._reader
        assert reader is not None

        # Absolute targets off one monotonic anchor. sleep(1) in a loop accumulates
        # every overshoot; this one only ever loses the frame it was late for.
        t0 = time.monotonic()
        seq = 0
        while not self._closed:
            delay = (t0 + seq * self.interval) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if self._closed:
                return

            snap = reader.snapshot()
            if snap is None:  # reader died, or camera stalled between ticks
                log.warning("webcam: no frame available at seq %d", seq)
                seq += 1
                continue

            jpeg = await asyncio.to_thread(
                encode_jpeg,
                snap.image,
                target_px=self.target_px,
                quality=self.quality,
            )
            if self._closed:
                return

            h, w = snap.image.shape[:2]
            yield Frame(
                t=snap.t,  # when the camera produced it, not when we encoded it
                jpeg=jpeg,
                device=None,  # §12.1 — absent under webcam
                meta={
                    "source": self.name,
                    "seq": seq,
                    "index": self.index,
                    "raw_size": (w, h),
                    "dropped": snap.dropped,
                    "age_ms": round((time.time() - snap.t) * 1000, 1),
                    "bytes": len(jpeg),
                },
            )
            seq += 1

    async def aclose(self) -> None:
        self._closed = True
        reader, cap = self._reader, self._cap
        self._reader = self._cap = None
        if reader is not None:
            await asyncio.to_thread(reader.stop)
        if cap is not None:
            await asyncio.to_thread(cap.release)
