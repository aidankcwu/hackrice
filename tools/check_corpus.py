"""Validate a recorded replay corpus (docs/PERSON_A.md A4 "done when").

Checks the things that quietly ruin a corpus: wrong encode parameters (so sensor
thresholds tuned on it don't transfer), timing gaps (so the replay adapter's 1 Hz is a
lie), and truncated writes.

    uv run python tools/check_corpus.py corpus/
"""

from __future__ import annotations

import io
import re
import statistics
import sys
from pathlib import Path

from PIL import Image

NAME_RE = re.compile(r"^frame_(\d{13})\.jpg$")


def main(dirname: str) -> int:
    d = Path(dirname)
    if not d.is_dir():
        print(f"not a directory: {d}")
        return 2

    files = sorted(d.glob("*.jpg"))
    if not files:
        print(f"no .jpg files in {d}")
        return 2

    stamps: list[int] = []
    sizes: list[int] = []
    edges: list[int] = []
    bad_name: list[str] = []
    unreadable: list[str] = []

    for f in files:
        m = NAME_RE.match(f.name)
        if not m:
            bad_name.append(f.name)
            continue
        raw = f.read_bytes()
        try:
            im = Image.open(io.BytesIO(raw))
            im.load()
        except Exception:
            unreadable.append(f.name)
            continue
        stamps.append(int(m.group(1)))
        sizes.append(len(raw))
        edges.append(max(im.size))

    if not stamps:
        print("no parseable frames — check the filename format: frame_<unix_millis>.jpg")
        return 2

    stamps.sort()
    span = (stamps[-1] - stamps[0]) / 1000.0
    gaps = [(b - a) / 1000.0 for a, b in zip(stamps, stamps[1:])]
    big = [g for g in gaps if g > 2.0]

    print(f"frames       {len(stamps)}")
    print(f"duration     {span:.1f}s  ({span / 60:.1f} min)")
    if gaps:
        print(f"gap median   {statistics.median(gaps):.2f}s   (want ~1.00)")
        print(f"gap max      {max(gaps):.2f}s")
    print(f"jpeg size    median {statistics.median(sizes) / 1024:.0f} KB, "
          f"max {max(sizes) / 1024:.0f} KB   (want ~40 KB)")
    print(f"longest edge {sorted(set(edges))}   (want [512])")

    ok = True
    if big:
        print(f"\n!  {len(big)} gap(s) over 2s — longest {max(big):.1f}s. "
              "Stream dropped or recording was paused.")
    if bad_name:
        print(f"\n!  {len(bad_name)} file(s) with unexpected names, e.g. {bad_name[:3]}")
        ok = False
    if unreadable:
        print(f"\n!  {len(unreadable)} unreadable file(s), e.g. {unreadable[:3]} "
              "— likely truncated on transfer")
        ok = False
    if set(edges) - {512}:
        print("\n!  not every frame is 512 px on the longest edge — encode mismatch "
              "with A12 means thresholds tuned here won't transfer")
        ok = False
    if statistics.median(sizes) > 120 * 1024:
        print("\n!  frames much larger than ~40 KB — check the q70 / scale=1 settings")
        ok = False

    print("\nOK" if ok else "\nproblems above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "corpus"))
