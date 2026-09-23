"""The `replay` capture adapter (docs/PERSON_A.md A3, SPEC §11.3).

A directory of JPEGs in, `Frame`s out at the rate they were recorded. SPEC §11.3 calls
this "deterministic debugging" and §13.4 makes the corpus Person A's *first*
deliverable: B builds the entire downstream system against it and never touches Swift.

Three properties this adapter owes its callers:

  **Deterministic.** Same directory, same frames, same order, same spacing, every run.
  Nothing here reads a clock to decide *what* to emit — only *when*.

  **Drift-free.** Emission targets are absolute offsets from one `time.monotonic()`
  anchor, not `await asyncio.sleep(1)` in a loop. Repeated sleep(1) accumulates the
  scheduler's overshoot and it shows at minute twelve, not minute one.

  **`device is None`.** §12.1: the tick's `device` block is absent under `replay`.

`speed` is the reason this adapter is worth more than a fixture. `speed=10` runs a
ten-minute corpus in one minute, which is how the gate, the episode builder and the
ring buffer get debugged in an afternoon instead of a weekend.


## Corpus filename convention — FIXED. The iOS recorder (A4) writes exactly this.

    frame_<unix_millis>.jpg          e.g.  frame_1757700842123.jpg

Unix epoch milliseconds, 13 digits, integer, no padding. Agreed with the Swift side
before recording, and not to be changed unilaterally on either end. It is (a) sortable
— 13 digits stays 13 digits until the year 2286, so lexicographic order *is*
chronological order, (b) timezone-proof, which a local-time stamp is not, and (c) one
line of Swift in the throwaway save-to-Documents hack A4 describes:

    let ms = Int((Date().timeIntervalSince1970 * 1000).rounded())
    let name = "frame_\\(ms).jpg"

`.jpeg` is accepted too. The parser also tolerates, so a corpus from any other tool
still replays in order:

    frame_1757700842.jpg             10-digit epoch seconds
    frame_1757700842.250.jpg         fractional epoch seconds
    frame_20260912T190402_250Z.jpg   ISO-8601 basic, **UTC only**, millis optional
    1757700842250.jpg                bare stamp, any prefix or suffix around it

Everything that is not a `.jpg`/`.jpeg` file is ignored silently — macOS will drop a
`.DS_Store` into the corpus directory and that is not an error.


## Cadence

The embedded millisecond stamps set the spacing: frame *i* is emitted
`(t[i] - t[i-1]) / speed` seconds after frame *i-1*. The glasses recorder samples at
1 Hz but a dropped DAT frame or a stalled write makes real spacing irregular, and
replaying the irregularity is what keeps downstream debugging honest — code that only
works on a metronome should fail here, not in the demo.

If *any* file in the directory fails to yield a timestamp, the whole directory falls
back to natural-sorted filename order at a flat `interval` (`timing="filename"` in
`Frame.meta`). Mixed modes are never used — half-real timestamps are worse than none.

Gaps longer than `max_gap` are clamped. A4 says to record the scripted sequence twice,
so a corpus routinely holds two takes with a coffee break between them; without the
clamp, replay would sit silent for that break.

`Frame.t` is the wall-clock instant of emission, exactly as a live adapter would set
it — the original corpus timeline shifted to now and scaled by `speed`. The recorded
stamp is preserved in `meta["src_t"]`. Downstream (the ring buffer's 90 s TTL, `age_ms`)
is built against a live stream, so replay hands it one.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .base import CaptureSource, Frame

log = logging.getLogger(__name__)

# Fallback cadence, used when the corpus carries no parseable timestamps. 1 Hz is the
# tick rate (§2.1). Also the spacing used when `loop=True` wraps around.
DEFAULT_INTERVAL_S = 1.0

# Longest corpus-time gap that is replayed literally, in seconds before `speed`.
# Anything longer is a gap between takes, not a gap in the scene.
DEFAULT_MAX_GAP_S = 5.0

# What counts as a corpus file. Everything else in the directory (.DS_Store, a README,
# the ticks.jsonl someone dropped in) is ignored rather than fataled on.
JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})

# SOI marker. A file that does not start with these two bytes is not a JPEG whatever
# its name says, and would only fail later inside the sensor block's decode (A5).
_JPEG_MAGIC = b"\xff\xd8"

# --- Timestamp parsing --------------------------------------------------------
# Tried in order. ISO first: `20260912T190402` has no run of 9+ digits, so the epoch
# pattern cannot steal it, but being explicit about precedence costs nothing.

_ISO_RE = re.compile(r"(?P<d>\d{8})T(?P<hms>\d{6})(?:[._-](?P<frac>\d{1,6}))?Z?")
_EPOCH_RE = re.compile(r"(?<!\d)(?P<int>\d{9,})(?:\.(?P<frac>\d{1,6}))?(?!\d)")


class ReplayCorpusError(ValueError):
    """The directory is missing, empty, or holds nothing that looks like a frame."""


@dataclass(frozen=True, slots=True)
class ReplayEntry:
    """One corpus file, after scanning. Ordered by `t` when the corpus is timestamped."""

    path: Path
    t: float | None
    """Timestamp parsed out of the filename, or None if the corpus is untimestamped."""


def parse_stamp(name: str) -> float | None:
    """Unix epoch seconds from a corpus filename, or None if it carries no stamp.

    Takes the filename, not the path — the directory above it may well contain digits.
    """
    m = _ISO_RE.search(name)
    if m:
        frac = m.group("frac") or ""
        try:
            dt = datetime.strptime(m.group("d") + m.group("hms"), "%Y%m%d%H%M%S")
        except ValueError:  # 20261340T..., i.e. digits that are not a date
            return None
        micros = int(frac.ljust(6, "0")) if frac else 0
        return dt.replace(tzinfo=timezone.utc).timestamp() + micros / 1_000_000

    m = _EPOCH_RE.search(name)
    if m:
        whole, frac = m.group("int"), m.group("frac")
        if frac is not None:  # an explicit decimal point always means seconds
            return float(f"{whole}.{frac}")
        # No decimal point, so the unit is inferred from magnitude. 10 digits is
        # seconds until 2286; 13 is milliseconds over the same span; 16 is micros.
        digits = len(whole)
        if digits >= 16:
            return int(whole) / 1_000_000
        if digits >= 13:
            return int(whole) / 1_000
        return float(whole)

    return None


def _natural_key(path: Path) -> tuple[object, ...]:
    """Sort key that puts img2 before img10. Only used in the filename fallback."""
    parts = re.split(r"(\d+)", path.name)
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in parts)


def scan_corpus(directory: Path | str) -> tuple[list[ReplayEntry], str]:
    """List a corpus directory. Returns (ordered entries, timing mode).

    Timing mode is `"stamped"` when every file yielded a timestamp and `"filename"`
    when at least one did not. Synchronous — call it from a thread on the hot path.
    """
    d = Path(directory)
    if not d.is_dir():
        raise ReplayCorpusError(f"replay corpus is not a directory: {d}")

    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in JPEG_SUFFIXES]
    usable: list[Path] = []
    for p in sorted(files, key=_natural_key):
        try:
            with p.open("rb") as fh:
                head = fh.read(2)
        except OSError as exc:
            log.warning("replay: skipping unreadable %s (%s)", p.name, exc)
            continue
        if head != _JPEG_MAGIC:
            log.warning("replay: skipping %s — not a JPEG (no SOI marker)", p.name)
            continue
        usable.append(p)

    if not usable:
        raise ReplayCorpusError(
            f"no JPEGs in {d} (looked for {sorted(JPEG_SUFFIXES)}; "
            f"{len(files)} candidate file(s) present)"
        )

    stamps = [parse_stamp(p.name) for p in usable]
    if all(s is not None for s in stamps):
        entries = [ReplayEntry(p, s) for p, s in zip(usable, stamps)]
        # (t, name) so two frames sharing a millisecond still order deterministically.
        entries.sort(key=lambda e: (e.t, e.path.name))
        return entries, "stamped"

    missing = sum(s is None for s in stamps)
    log.info(
        "replay: %d/%d file(s) carry no timestamp — falling back to filename order",
        missing,
        len(usable),
    )
    return [ReplayEntry(p, None) for p in usable], "filename"


class ReplaySource(CaptureSource):
    """Emit a directory of JPEGs as frames, at recorded spacing divided by `speed`.

    Args:
        directory: corpus directory, as recorded by A4. Scanned on first iteration,
            not in `__init__`, so constructing a source never touches the disk.
        speed: wall-clock multiplier. 10.0 replays ten minutes in one. Values below 1
            slow the corpus down; values above it are how the pipeline gets debugged.
        interval: spacing used when the corpus carries no timestamps, and across a
            `loop` wrap. Seconds, before `speed`. 1 Hz is the tick rate.
        max_gap: longest recorded gap replayed literally, seconds before `speed`.
            Longer gaps — the pause between two takes — are clamped to this.
        loop: restart at the first frame when the corpus runs out, instead of ending.
            Useful for soak tests: a 60-frame corpus can drive a 15-minute drift run.
    """

    name = "replay"

    def __init__(
        self,
        directory: Path | str,
        *,
        speed: float = 1.0,
        interval: float = DEFAULT_INTERVAL_S,
        max_gap: float = DEFAULT_MAX_GAP_S,
        loop: bool = False,
    ) -> None:
        if speed <= 0:
            raise ValueError(f"speed must be positive, got {speed}")
        if interval <= 0:
            raise ValueError(f"interval must be positive, got {interval}")
        if max_gap <= 0:
            raise ValueError(f"max_gap must be positive, got {max_gap}")
        self.directory = Path(directory)
        self.speed = float(speed)
        self.interval = float(interval)
        self.max_gap = float(max_gap)
        self.loop = bool(loop)
        self._closed = False

    def scan(self) -> tuple[list[ReplayEntry], str]:
        """Scan the corpus up front, to fail fast on a bad `--dir`. Blocking."""
        return scan_corpus(self.directory)

    def gap(self, prev_t: float | None, next_t: float | None) -> float:
        """Corpus-time spacing between two frames, before `speed`. Never negative."""
        if prev_t is None or next_t is None:  # untimestamped corpus, or a loop wrap
            return self.interval
        delta = next_t - prev_t
        if delta <= 0:  # same millisecond, or stamps that lied; emit back to back
            return 0.0
        return min(delta, self.max_gap)

    async def frames(self) -> AsyncIterator[Frame]:
        entries, timing = await asyncio.to_thread(self.scan)
        span = 0.0
        if timing == "stamped" and len(entries) > 1:
            span = entries[-1].t - entries[0].t
        log.info(
            "replay: %d frame(s) from %s, timing=%s, span %.1f s -> %.1f s at %.2fx",
            len(entries),
            self.directory,
            timing,
            span,
            span / self.speed,
            self.speed,
        )

        # One anchor for the whole run. Every target is an absolute offset from it, so
        # a late frame costs that frame and nothing after it — errors never accumulate.
        t0_mono = time.monotonic()
        t0_epoch = time.time()

        offset = 0.0
        prev_src: float | None = None
        prev_pass = 0
        seq = 0

        for pass_no, entry in _passes(entries, self.loop):
            if self._closed:
                return
            if pass_no != prev_pass:  # wrapped: no meaningful recorded gap across it
                prev_src, prev_pass = None, pass_no

            raw_gap = self.gap(prev_src, entry.t)
            if seq:
                offset += raw_gap / self.speed
            target = t0_mono + offset

            # Read during the wait window, so disk latency comes out of the gap rather
            # than out of the cadence. to_thread because invariant 1 — T0 never blocks.
            jpeg = await asyncio.to_thread(entry.path.read_bytes)

            late = time.monotonic() - target
            if late < 0:
                await asyncio.sleep(-late)
            if self._closed:
                return

            yield Frame(
                t=t0_epoch + offset,
                jpeg=jpeg,
                device=None,  # §12.1 — absent under replay
                meta={
                    "source": self.name,
                    "seq": seq,
                    "pass": pass_no,
                    "path": str(entry.path),
                    "src_t": entry.t,
                    "timing": timing,
                    "speed": self.speed,
                    "gap_s": round(raw_gap, 4) if seq else 0.0,
                    "gap_clamped": bool(
                        seq
                        and prev_src is not None
                        and entry.t is not None
                        and entry.t - prev_src > self.max_gap
                    ),
                    "late_ms": round(max(0.0, late) * 1000, 3),
                },
            )
            prev_src = entry.t
            seq += 1

    async def aclose(self) -> None:
        # Nothing to release — the generator holds no file handle between yields. The
        # flag stops an in-flight iteration at its next checkpoint.
        self._closed = True


def _passes(entries: list[ReplayEntry], loop: bool) -> Iterator[tuple[int, ReplayEntry]]:
    """(pass number, entry) over the corpus once, or forever when looping."""
    pass_no = 0
    while True:
        for entry in entries:
            yield pass_no, entry
        pass_no += 1
        if not loop:
            return
