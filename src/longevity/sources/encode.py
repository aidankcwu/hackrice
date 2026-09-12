"""JPEG normalisation shared by the laptop-side adapters.

The glasses path encodes on the phone (§2.2, §11.4): 512 px, quality 70, ~40 KB. The
`webcam` and `replay` adapters must land on the same shape, or every sensor threshold
tuned on one path is wrong on the other — `sharpness` and `phash` in particular are
sensitive to resolution and compression.
"""

from __future__ import annotations

import io

from PIL import Image

# §2.2 / §11.4. The phone encodes to these; the laptop adapters match them.
TARGET_PX = 512
JPEG_QUALITY = 70


def encode_jpeg(img: Image.Image, *, max_px: int = TARGET_PX, quality: int = JPEG_QUALITY) -> bytes:
    """Downscale so the longest side is `max_px`, then encode JPEG."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > max_px:
        scale = max_px / longest
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def normalise_jpeg(data: bytes, *, max_px: int = TARGET_PX, quality: int = JPEG_QUALITY) -> bytes:
    """Re-encode only if the image is larger than target.

    A corpus recorded through the glasses is already 512/q70, and re-encoding it would
    double-compress — visibly shifting `sharpness` and perturbing `phash` relative to
    the live glasses path the corpus is supposed to stand in for. So a correctly sized
    frame is passed through byte-identical.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            if max(img.size) <= max_px:
                return data
            return encode_jpeg(img, max_px=max_px, quality=quality)
    except OSError:
        return data
