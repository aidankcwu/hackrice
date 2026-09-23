"""Verification for src/longevity/sensors.py (docs/PERSON_A.md A5).

    uv run python tools/check_sensors.py

Answers the two questions A5 says must not be assumed:

  1. **Is the block actually under the ~5 ms budget?** "Print the timing; do not
     assume it." Per-field and total, mean and p99, over many iterations.
  2. **Is the phash real?** B picks the four escalation frames by phash Hamming
     distance (SPEC §4.3, §12.3). A weak hash degrades T1's input silently — there is
     no error anywhere downstream. So: near-identical frames must land close, a scene
     change must land far, and the actual distances get printed for eyeballing.

No external files: every test frame is synthesised here with Pillow/numpy at 512 px
q70, matching what A12 encodes on the phone. Synthetic frames are cleaner than real
ones, so treat the thresholds below as necessary, not sufficient — re-run against the
A4 glasses corpus once it exists.

Exits non-zero if any check fails.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from longevity.sensors import (  # noqa: E402
    HASH_BITS,
    SENSOR_FIELDS,
    SensorComputer,
    empty_sensor_block,
    hamming,
)

SIZE = 512
QUALITY = 70
ITERATIONS = 300
BUDGET_MS = 5.0

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


# --- synthetic scenes ---------------------------------------------------------


def _office() -> Image.Image:
    """Desk, monitor, mug, a page of text-like lines. Structured and high-frequency."""
    im = Image.new("RGB", (SIZE, SIZE), (104, 108, 118))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 330, SIZE, SIZE], fill=(132, 114, 92))          # desk
    d.rectangle([120, 110, 400, 300], fill=(36, 38, 44))            # bezel
    d.rectangle([132, 122, 388, 288], fill=(196, 204, 220))         # screen
    for y in range(132, 285, 14):                                   # screen text
        d.line([144, y, 144 + (y * 37) % 230, y], fill=(70, 80, 110), width=3)
    d.ellipse([430, 300, 486, 356], fill=(180, 70, 60))             # mug
    d.rectangle([40, 360, 230, 470], fill=(228, 226, 218))          # paper
    for y in range(374, 465, 11):                                   # paper text
        d.line([52, y, 52 + (y * 53) % 160, y], fill=(90, 90, 95), width=2)
    return im


def _meal() -> Image.Image:
    """A plate of food on a table. Different structure, similar brightness to _office."""
    im = Image.new("RGB", (SIZE, SIZE), (120, 100, 84))
    d = ImageDraw.Draw(im)
    d.ellipse([90, 110, 430, 430], fill=(236, 234, 228))            # plate
    d.ellipse([140, 160, 380, 380], fill=(228, 224, 214))
    d.ellipse([170, 190, 300, 320], fill=(150, 90, 50))             # protein
    d.ellipse([280, 250, 360, 340], fill=(70, 120, 60))             # greens
    d.ellipse([200, 300, 260, 360], fill=(200, 170, 70))
    d.rectangle([440, 120, 466, 420], fill=(190, 190, 196))         # knife
    return im


def _outdoor() -> Image.Image:
    """Bright sky gradient, sun, trees, grass. High luminance, cool at the top."""
    sky = np.zeros((SIZE, SIZE, 3), np.uint8)
    ramp = np.linspace(255, 150, 330).astype(np.uint8)
    sky[:330, :, 0] = ramp[:, None] * 0.72
    sky[:330, :, 1] = ramp[:, None] * 0.86
    sky[:330, :, 2] = ramp[:, None]
    sky[330:, :, 0], sky[330:, :, 1], sky[330:, :, 2] = 96, 150, 74
    im = Image.fromarray(sky)
    d = ImageDraw.Draw(im)
    d.ellipse([380, 40, 470, 130], fill=(255, 252, 230))            # sun
    for x0 in (40, 150, 260):
        d.ellipse([x0, 170, x0 + 110, 300], fill=(40, 92, 46))      # canopy
        d.rectangle([x0 + 48, 280, x0 + 62, 360], fill=(70, 54, 36))
    return im


def _tint(im: Image.Image, gain: tuple[float, float, float]) -> Image.Image:
    a = np.asarray(im, np.float32) * np.array(gain, np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _jitter(im: Image.Image, rng: np.random.Generator, shift: int = 1) -> Image.Image:
    """One frame later, same scene: sub-degree head motion, sensor noise, AE breathing."""
    a = np.asarray(im.transform(im.size, Image.AFFINE, (1, 0, shift, 0, 1, shift)), np.float32)
    a = a * 1.03 + rng.normal(0, 2.5, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _pan(im: Image.Image, px: int) -> Image.Image:
    return im.transform(im.size, Image.AFFINE, (1, 0, px, 0, 1, 0))


def _grain(im: Image.Image, rng: np.random.Generator) -> Image.Image:
    """Sensor grain. Clean synthetic scenes compress to ~10 KB; a real 512 px q70
    glasses frame is ~40 KB (§2.2, A12) and costs proportionally more to decode, so
    the timing section measures grained frames or it measures a fantasy."""
    a = np.asarray(im, np.float32) + rng.normal(0, 9, (SIZE, SIZE, 3))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def to_jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=QUALITY)
    return buf.getvalue()


# --- checks -------------------------------------------------------------------


def check_shape(frames: dict[str, bytes]) -> None:
    print("\n1. Block shape and types (§12.1: sensor is always present)")
    s = SensorComputer()
    bad: list[str] = []
    for name, jpeg in frames.items():
        b = s.compute(jpeg)
        if tuple(b) != SENSOR_FIELDS:
            bad.append(f"{name}: keys {tuple(b)}")
            continue
        if not (isinstance(b["lux_proxy"], int) and isinstance(b["sharpness"], int) and isinstance(b["cct"], int)):
            bad.append(f"{name}: int fields are not int")
        for f in ("hist_spread", "frame_delta", "flow_mag"):
            if not isinstance(b[f], float) or not (0.0 <= b[f] <= 1.0):
                bad.append(f"{name}: {f}={b[f]!r} not a float in 0..1")
        if not (isinstance(b["phash"], str) and len(b["phash"]) == HASH_BITS // 4):
            bad.append(f"{name}: phash {b['phash']!r}")
        else:
            int(b["phash"], 16)
    check("all 7 fields, right names and order, on every frame", not bad, "; ".join(bad))

    s2 = SensorComputer()
    garbage = [b"", b"not a jpeg at all", bytes(range(256)), to_jpeg(_office())[:400]]
    ok, note = True, ""
    for g in garbage:
        try:
            b = s2.compute(g)
        except Exception as e:  # noqa: BLE001
            ok, note = False, f"raised {type(e).__name__}"
            break
        if tuple(b) != SENSOR_FIELDS or b != empty_sensor_block():
            ok, note = False, f"bad default block {b}"
            break
    check("malformed JPEG returns defaults, never raises", ok, note or f"{s2.decode_failures}/4 decode failures logged")

    size = len(json.dumps(SensorComputer().compute(frames["office"]), separators=(",", ":")))
    check("sensor block stays small", size < 160, f"{size} bytes of JSON")

    # The webcam adapter (A9) hands over whatever the camera produces. Cost is bounded
    # by WORK_PX, not by the input, and the block must come out the same shape.
    wide = Image.new("RGB", (1280, 720), (90, 100, 120))
    ImageDraw.Draw(wide).rectangle([200, 120, 900, 560], fill=(210, 200, 180))
    s3 = SensorComputer()
    t: dict[str, float] = {}
    b = s3.compute(to_jpeg(wide), timings=t)
    check(
        "non-square oversized frame (1280x720) still produces a bounded block",
        tuple(b) == SENSOR_FIELDS and sum(t.values()) < BUDGET_MS,
        f"{sum(t.values()):.2f} ms, lux_proxy={b['lux_proxy']}",
    )


def check_values(frames: dict[str, bytes]) -> None:
    print("\n2. Field values (§12.1: lux_proxy is relative, ordinal, not lux)")
    v = {k: SensorComputer().compute(j) for k, j in frames.items()}

    lux = {k: v[k]["lux_proxy"] for k in ("office_dim", "office", "outdoor")}
    check(
        "lux_proxy orders dim < indoor < outdoor",
        lux["office_dim"] < lux["office"] < lux["outdoor"],
        " < ".join(f"{k}={x}" for k, x in lux.items()),
    )
    check("lux_proxy on flat mid-grey is the ~216 reference", 200 <= v["grey"]["lux_proxy"] <= 230, f"{v['grey']['lux_proxy']} (§4.2 envelope shows 205–210 seated indoor)")

    check("cct on neutral grey lands on D65", 6300 <= v["grey"]["cct"] <= 6700, f"{v['grey']['cct']} K")
    check(
        "cct orders tungsten < neutral < daylight",
        v["office_dim"]["cct"] < v["grey"]["cct"] < v["outdoor"]["cct"],
        f"tungsten={v['office_dim']['cct']} K, grey={v['grey']['cct']} K, sky={v['outdoor']['cct']} K",
    )

    check(
        "sharpness collapses under blur",
        v["office_blur"]["sharpness"] * 4 < v["office"]["sharpness"],
        f"sharp={v['office']['sharpness']}, blurred={v['office_blur']['sharpness']}",
    )
    check(
        "hist_spread: flat grey ~0, full-range ramp ~1",
        v["grey"]["hist_spread"] < 0.05 < 0.5 < v["ramp"]["hist_spread"],
        f"grey={v['grey']['hist_spread']}, office={v['office']['hist_spread']}, ramp={v['ramp']['hist_spread']}",
    )

    # frame_delta / flow_mag are inter-frame, so they need a live computer.
    s = SensorComputer()
    s.compute(frames["office"])
    first = SensorComputer().compute(frames["office"])
    check("first frame of a session reports zero motion", first["frame_delta"] == 0.0 and first["flow_mag"] == 0.0)

    same = s.compute(frames["office"])
    s.reset()
    s.compute(frames["office"])
    jit = s.compute(frames["office_jitter"])
    s.reset()
    s.compute(frames["office"])
    cut = s.compute(frames["outdoor"])
    check(
        "frame_delta orders identical < jitter < scene cut",
        same["frame_delta"] <= jit["frame_delta"] < cut["frame_delta"],
        f"identical={same['frame_delta']}, jitter={jit['frame_delta']}, cut={cut['frame_delta']}",
    )

    # A 64 px pan of a 512 px frame is 1/8 of frame width: flow_mag should read ~0.125.
    s.reset()
    s.compute(frames["office"])
    pan = s.compute(frames["office_pan64"])
    s.reset()
    s.compute(frames["office"])
    still = s.compute(frames["office"])
    check(
        "flow_mag measures a known 64 px pan (expect ~0.125) and reads ~0 when still",
        abs(pan["flow_mag"] - 0.125) < 0.03 and still["flow_mag"] < 0.02,
        f"pan={pan['flow_mag']}, still={still['flow_mag']}",
    )


def check_phash(frames: dict[str, bytes]) -> None:
    print("\n3. phash (§4.3 — B selects escalation frames by this distance)")
    names = ["office", "office_jitter", "office_dim", "office_blur", "meal", "outdoor"]
    h = {n: SensorComputer().compute(frames[n])["phash"] for n in names}

    print(f"    {'':14}" + "".join(f"{n[:9]:>10}" for n in names))
    for a in names:
        print(f"    {a:14}" + "".join(f"{hamming(h[a], h[b]):>10}" for b in names))
    print("    hashes: " + ", ".join(f"{n}={h[n]}" for n in names[:3]))

    d_same = hamming(h["office"], h["office"])
    d_jit = hamming(h["office"], h["office_jitter"])
    d_dim = hamming(h["office"], h["office_dim"])
    d_blur = hamming(h["office"], h["office_blur"])
    d_meal = hamming(h["office"], h["meal"])
    d_out = hamming(h["office"], h["outdoor"])

    check("identical frames hash identically", d_same == 0, f"distance {d_same}/{HASH_BITS}")
    check("near-identical frame (1 px shift + noise + 3% exposure) stays close", d_jit <= 6, f"distance {d_jit}/{HASH_BITS}")
    check("same scene re-exposed dim and warm stays close", d_dim <= 12, f"distance {d_dim}/{HASH_BITS} — phash tracks content, not exposure")
    check("blur moves it but does not scramble it", d_blur <= 16, f"distance {d_blur}/{HASH_BITS}")
    check("genuine scene change is far", d_meal >= 16 and d_out >= 16, f"office→meal {d_meal}, office→outdoor {d_out}")
    check("scene change beats near-identical by 3x", min(d_meal, d_out) >= 3 * max(1, d_jit), f"{min(d_meal, d_out)} vs {d_jit}")

    # A hash whose bits are stuck is worse than useless: it looks like it works.
    rng = np.random.default_rng(7)
    corpus = [to_jpeg(Image.fromarray(rng.integers(0, 255, (SIZE, SIZE, 3), dtype=np.uint8).repeat(1, 0))) for _ in range(24)]
    corpus += [to_jpeg(_pan(_office(), p)) for p in range(0, 240, 10)]
    bits = np.array([[(int(SensorComputer().compute(j)["phash"], 16) >> k) & 1 for k in range(HASH_BITS)] for j in corpus])
    ones = bits.mean(axis=0)
    check(
        "no constant bits across a 48-frame corpus",
        bool(((ones > 0.02) & (ones < 0.98)).all()),
        f"{int(((ones <= 0.02) | (ones >= 0.98)).sum())} stuck bits; bit balance {ones.mean():.2f} (0.5 is ideal)",
    )
    uniq = {SensorComputer().compute(j)["phash"] for j in corpus}
    check("no collisions across the corpus", len(uniq) == len(corpus), f"{len(uniq)}/{len(corpus)} distinct")


def check_timing(frames: dict[str, bytes]) -> None:
    print(f"\n4. Timing over {ITERATIONS} frames (invariant 1: T0 never blocks; budget ~{BUDGET_MS} ms)")
    rng = np.random.default_rng(11)
    seq = [to_jpeg(_grain(im, rng)) for im in (_office(), _jitter(_office(), rng), _pan(_office(), 64), _meal(), _outdoor(), _tint(_office(), (0.42, 0.33, 0.24)))]
    print(f"    measured on grained frames at {min(len(j) for j in seq) // 1024}–{max(len(j) for j in seq) // 1024} KB, "
          f"the ~40 KB size A12 encodes on the phone")
    s = SensorComputer()
    for j in seq:
        s.compute(j)  # warm numpy/libjpeg paths before measuring

    rows: dict[str, list[float]] = {}
    totals: list[float] = []
    for i in range(ITERATIONS):
        t: dict[str, float] = {}
        s.compute(seq[i % len(seq)], timings=t)
        for k, ms in t.items():
            rows.setdefault(k, []).append(ms)
        totals.append(sum(t.values()))

    print(f"    {'stage':<14}{'mean ms':>10}{'p99 ms':>10}")
    for k in ("decode", "lux_proxy", "cct", "hist_spread", "sharpness", "frame_delta", "flow_mag", "phash"):
        a = np.array(rows[k])
        print(f"    {k:<14}{a.mean():>10.3f}{np.percentile(a, 99):>10.3f}")
    tot = np.array(totals)
    mean, p99 = tot.mean(), float(np.percentile(tot, 99))
    print(f"    {'TOTAL':<14}{mean:>10.3f}{p99:>10.3f}   ({s.flow_mode} flow backend)")
    check(f"mean total under {BUDGET_MS} ms", mean < BUDGET_MS, f"{mean:.3f} ms")
    check(f"p99 total under {BUDGET_MS} ms", p99 < BUDGET_MS, f"{p99:.3f} ms")

    # Informational: what the optional OpenCV backend would cost. Never fails the run —
    # the extra may not be installed, and flow_mag is first on the cut list.
    try:
        cv = SensorComputer(flow="opencv")
    except Exception:  # noqa: BLE001
        cv = None
    if cv is not None and cv.flow_mode == "opencv":
        cv.compute(seq[0])
        cvt: list[float] = []
        for i in range(ITERATIONS):
            t = {}
            cv.compute(seq[i % len(seq)], timings=t)
            cvt.append(t["flow_mag"])
        a, b = np.array(rows["flow_mag"]), np.array(cvt)
        print(f"    [info] flow_mag backends: numpy {a.mean():.3f} ms vs opencv(Farneback) {b.mean():.3f} ms")
    else:
        print("    [info] opencv extra not installed — numpy flow backend is the only one available")


def main() -> int:
    rng = np.random.default_rng(42)
    office = _office()
    frames = {
        "office": to_jpeg(office),
        "office_jitter": to_jpeg(_jitter(office, rng)),
        "office_dim": to_jpeg(_tint(office, (0.42, 0.33, 0.24))),   # tungsten, underexposed
        "office_blur": to_jpeg(office.filter(ImageFilter.GaussianBlur(3.5))),
        "office_pan64": to_jpeg(_pan(office, 64)),
        "meal": to_jpeg(_meal()),
        "outdoor": to_jpeg(_outdoor()),
        "grey": to_jpeg(Image.new("RGB", (SIZE, SIZE), (128, 128, 128))),
        "ramp": to_jpeg(Image.fromarray(np.tile(np.linspace(0, 255, SIZE, dtype=np.uint8)[None, :, None], (SIZE, 1, 3)))),
    }
    print(f"synthetic frames: {len(frames)} at {SIZE} px q70, "
          f"{min(len(f) for f in frames.values()) // 1024}–{max(len(f) for f in frames.values()) // 1024} KB")

    check_shape(frames)
    check_values(frames)
    check_phash(frames)
    check_timing(frames)

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} check(s): " + "; ".join(_failures))
        return 1
    print("PASS — all checks green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
