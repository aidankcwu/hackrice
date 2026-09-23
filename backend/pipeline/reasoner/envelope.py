"""The T1 context envelope (SPEC §4.2, §4.3).

Five parts, ordered by volatility so the stable prefix is cacheable: the
objective, persona and 7-day summary go in a system message (see
:func:`~.prompts.build_system_prompt` for why the objective leads); today's
summary, the tick table and the frames go in one user message.

Two rules do the real work here:

* **Interleave text and images chronologically, oldest first.** Each image is
  immediately preceded by a text label naming its offset and key tick fields.
  The trigger frame goes last, closest to the question. A block of images
  followed by a block of text gives the model no way to bind a frame to the
  tick it came from.
* **Subsample by change, not by even time spacing** -- :func:`select_frames`
  picks the moments where the perceptual hash moved most. Even spacing across a
  motionless fifty seconds yields four identical frames.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from ..models import Escalation, Tick, TodaySummaryLine, phash_distance
from .prompts import build_system_prompt

__all__ = [
    "UNKNOWN",
    "local_time",
    "CLERK_FRAMES",
    "TODAY_MAX_LINES",
    "select_frames",
    "tick_table",
    "frame_label",
    "build_envelope",
]

#: Rendered for any field the VLM did not report. Unknown is not False.
#: Freshness limit for reading ``ai`` fields into the tick table and frame
#: labels. The Reasoner sets this from ``Timings.ai_max_age_ms`` so a 1.5 s
#: cadence does not render genuinely fresh blocks as '?'.
AI_MAX_AGE_MS = 3000

UNKNOWN = "?"

#: Frames per clerk call: the trigger frame (sent at detail high) and the one
#: context frame at the biggest scene change in the window (detail low). The
#: clerk's job on a wake-up is "what is in hand right now"; the trigger frame
#: answers that, and one before-the-change frame gives it the contrast. Four
#: frames spent two images on the two sides of the same old phash hop (real
#: selections sat at t-35s/t-34s) and paid two more vision passes per call.
#: The Reasoner owns the count it copies at admission and passes it as ``k``.
CLERK_FRAMES = 2

#: How many of today's memory lines the clerk sees, newest last. Uncapped, the
#: block grew to ~86 lines (~1.5 K tokens) by the end of a day -- the largest
#: part of the user message -- and it re-reads earlier sightings as "again".
#: The conversation agent already caps its own copy at the same 15; repeats
#: beyond the cap are still caught by the questions block and learned lines.
TODAY_MAX_LINES = 15

#: One tick-table row per this many seconds of window (SPEC §4.3: compact).
TABLE_ROW_EVERY_S = 5.0

_COLUMNS = (
    "t",
    "scene",
    "activity",
    "food",
    "screen",
    "people",
    "caff",
    "alc",
    "drink",
    "delta",
    "lux",
)


def local_time(t: float, fmt: str = "%H:%M:%S") -> str:
    """Local wall-clock rendering of a simulated timestamp."""

    return datetime.fromtimestamp(t).astimezone().strftime(fmt)


# -- frame selection ------------------------------------------------------


def select_frames(
    window: list[Tick], trigger_tick: Tick, k: int = CLERK_FRAMES
) -> list[Tick]:
    """Pick up to ``k`` ticks whose frames to send, chronologically, trigger last.

    The trigger tick is always included and always last. The other ``k-1`` are
    the ticks sitting next to the largest ``sensor.phash`` movements between
    consecutive ticks -- the moments something changed (SPEC §4.3). A tick is
    scored by the larger of the two hops on either side of it, so both the last
    frame before a change and the first one after it are strong candidates.
    Ticks without a phash cannot be compared and are skipped; a short window
    simply yields fewer frames.
    """

    if k <= 0:
        return []
    if k == 1:
        return [trigger_tick]

    hashed = [t for t in window if t.sensor.phash is not None]
    n = len(hashed)

    hops: list[int] = [0] * n  # hops[i] = distance between i-1 and i
    for i in range(1, n):
        try:
            hops[i] = phash_distance(
                hashed[i - 1].sensor.phash,  # type: ignore[arg-type]
                hashed[i].sensor.phash,  # type: ignore[arg-type]
            )
        except ValueError:  # pragma: no cover - schema pins the length
            hops[i] = 0

    scored: list[tuple[int, int, Tick]] = []
    seen: set[str] = {trigger_tick.tick_id}
    for i, tick in enumerate(hashed):
        if tick.tick_id in seen:
            continue
        seen.add(tick.tick_id)
        before = hops[i] if i > 0 else 0
        after = hops[i + 1] if i + 1 < n else 0
        # Later ties win: a change closer to the trigger is more relevant.
        scored.append((max(before, after), i, tick))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = [tick for _, _, tick in scored[: k - 1]]
    chosen.sort(key=lambda tick: tick.t)
    chosen.append(trigger_tick)
    return chosen


# -- text rendering -------------------------------------------------------


def _offset(t: float, origin: float) -> str:
    delta = int(round(origin - t))
    return f"t-{delta}s" if delta > 0 else "t-0s"


def _bool_cell(tick: Tick, field: str) -> str:
    value = tick.flag(field, AI_MAX_AGE_MS)
    if value is None:
        return UNKNOWN
    return "y" if value else "n"


def _food_cell(tick: Tick) -> str:
    present = tick.flag("food_present", AI_MAX_AGE_MS)
    if present is None:
        return UNKNOWN
    if not present:
        return "n"
    return tick.enum("food_type", AI_MAX_AGE_MS) or "y"


def _num_cell(value: float | None, fmt: str) -> str:
    return UNKNOWN if value is None else format(value, fmt)


def _row(tick: Tick, origin: float) -> list[str]:
    return [
        _offset(tick.t, origin),
        tick.enum("scene", AI_MAX_AGE_MS) or UNKNOWN,
        tick.enum("activity", AI_MAX_AGE_MS) or UNKNOWN,
        _food_cell(tick),
        _bool_cell(tick, "screen_present"),
        _bool_cell(tick, "people_present"),
        _bool_cell(tick, "caffeine_visible"),
        _bool_cell(tick, "alcohol_visible"),
        tick.enum("drink", AI_MAX_AGE_MS) or UNKNOWN,
        _num_cell(tick.sensor.frame_delta, ".2f"),
        _num_cell(tick.sensor.lux_proxy, ".0f"),
    ]


def _subsample(window: list[Tick], every_s: float = TABLE_ROW_EVERY_S) -> list[Tick]:
    """One tick per ``every_s`` seconds, keeping the last tick of the window."""

    if not window:
        return []
    rows: list[Tick] = []
    last_t: float | None = None
    for tick in window:
        if last_t is None or (tick.t - last_t) >= every_s:
            rows.append(tick)
            last_t = tick.t
    if rows[-1] is not window[-1]:
        rows.append(window[-1])
    return rows


def tick_table(window: list[Tick], origin: float) -> str:
    """A compact fixed-width table of tick fields across the window.

    Cheap, and it carries the temporal shape -- light dropped, motion stopped,
    screen appeared (SPEC §4.3). ``?`` marks a field the VLM did not report.
    """

    rows = [_row(tick, origin) for tick in _subsample(window)]
    if not rows:
        return "(no ticks in window)"
    table = [list(_COLUMNS)] + rows
    widths = [max(len(r[i]) for r in table) for i in range(len(_COLUMNS))]
    rendered = "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in table
    )
    trigger_ai = window[-1].ai if window[-1].ai_fresh(AI_MAX_AGE_MS) else None
    if trigger_ai and trigger_ai.caption:
        objects = ", ".join(trigger_ai.objects or [])
        rendered += f"\nTrigger frame: {trigger_ai.caption}"
        if objects:
            rendered += f"; objects: {objects}"
    return rendered


def frame_label(tick: Tick, origin: float, is_trigger: bool = False) -> str:
    """The text label that immediately precedes a frame.

    Sensor fields lead because they are always present (SPEC §12.1); the VLM
    tags follow and may be missing entirely, which is exactly when a label of
    "motion 0.07, lux 337" is the only thing binding the frame to its second.
    """

    bits: list[str] = [
        f"motion {_num_cell(tick.sensor.frame_delta, '.2f')}",
        f"lux {_num_cell(tick.sensor.lux_proxy, '.0f')}",
        f"scene {tick.enum('scene', AI_MAX_AGE_MS) or UNKNOWN}",
    ]
    activity = tick.enum("activity", AI_MAX_AGE_MS)
    if activity:
        bits.append(activity)
    for field, word in (
        ("screen_present", "screen"),
        ("food_present", "food"),
        ("people_present", "people"),
        ("caffeine_visible", "caffeine"),
        ("alcohol_visible", "alcohol"),
        ("vegetation_visible", "vegetation"),
    ):
        if tick.flag(field, AI_MAX_AGE_MS):
            bits.append(word)
    head = _offset(tick.t, origin)
    if is_trigger:
        head += " (trigger frame)"
    label = f"{head} — " + ", ".join(bits)
    caption = tick.ai.caption if tick.ai_fresh(AI_MAX_AGE_MS) and tick.ai else None
    if caption:
        label += f' — "{caption}"'
    return label


def _today_block(
    lines: list[TodaySummaryLine], limit: int = TODAY_MAX_LINES
) -> str:
    if not lines:
        return "Today so far:\nnothing yet"
    shown = lines[-limit:] if limit > 0 else lines
    body = "\n".join(f"  {local_time(l.t, '%H:%M')} {l.line}" for l in shown)
    # Say when lines were cut, so a short block is not read as a quiet day.
    head = (
        f"Today so far (last {len(shown)} of {len(lines)} lines):"
        if len(shown) < len(lines)
        else "Today so far:"
    )
    return f"{head}\n{body}"


RECENT_QUESTIONS = 5


def _questions_block(questions: list[Any]) -> str:
    """The sliding window of what was just asked, newest first (ASK_DESIGN).

    Today's memory only carries a question once it was *answered*; an open,
    expired or suppressed one left no trace, so three wake-ups in thirty
    seconds each asked about the same cereal box afresh. This block is the
    short-term memory of the mouth: the last few questions with what became
    of each, so the model can see it already asked before it asks again.
    """

    if not questions:
        return "Questions you already asked (last 5):\nnone yet"
    rows = []
    for q in list(questions)[:RECENT_QUESTIONS]:
        status = getattr(q, "status", "")
        if status == "answered":
            parsed = getattr(q, "parsed", None) or {}
            settled = ", ".join(
                f"{key} {parsed[key]}" for key in ("confirmed", "count", "food_type")
                if parsed.get(key) is not None
            )
            what = f"answered: \"{getattr(q, 'answer_text', '') or ''}\""
            if settled:
                what += f" ({settled})"
        elif status == "open":
            what = "still open — waiting for the answer"
        elif status == "expired":
            what = "no answer heard"
        else:
            what = f"not sent ({getattr(q, 'suppressed_reason', None) or status})"
        rows.append(f"  {local_time(q.created_t, '%H:%M')} \"{q.question}\" → {what}")
    return (
        "Questions you already asked (last 5, newest first):\n" + "\n".join(rows)
        + "\nDo not ask any of these again, or a rewording of one, unless the "
        "frames show the answer has changed. An unanswered one may be asked once "
        "more only if the moment is still in front of the wearer."
    )


def _window_span_s(window: list[Tick], origin: float) -> int:
    if not window:
        return 0
    return max(0, int(round(origin - window[0].t)))


def _data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


# -- the envelope ---------------------------------------------------------


def build_envelope(
    esc: Escalation,
    frames: dict[str, bytes],
    today_lines: list[TodaySummaryLine],
    seven_day: str,
    persona: str,
    k: int = CLERK_FRAMES,
    learned: list[str] | None = None,
    recent_questions: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the Responses API ``input`` for one escalation.

    ``frames`` is the durable copy already taken at admission, keyed by
    ``frame_ref``; a ref missing from it (expired before the copy) simply drops
    its image and label. Selection is deterministic, so re-running
    :func:`select_frames` here yields exactly the ticks the caller copied.

    ``learned`` is the active profile lines, oldest first; it joins the system
    prompt rather than the user turn because it is stable across a day, like
    the persona it extends.
    """

    origin = esc.t
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                f"Trigger: {esc.trigger} at {local_time(esc.t)}"
                + (f" — {esc.reason}" if esc.reason else "")
            ),
        },
        {"type": "input_text", "text": _today_block(today_lines)},
        {"type": "input_text", "text": _questions_block(recent_questions or [])},
        {
            "type": "input_text",
            "text": (
                f"Tick table (last {_window_span_s(esc.window, origin)} s):\n"
                + tick_table(esc.window, origin)
            ),
        },
    ]

    # Extra context the frames cannot supply -- the wearable HR series behind a
    # `biometric_anomaly` (SPEC §14.3). After the table, before the pixels, so
    # the numbers are already in hand when the model looks at the frames.
    content.extend(
        {"type": "input_text", "text": line} for line in esc.extra_text if line
    )

    selected = select_frames(esc.window, esc.tick, k=k)
    for tick in selected:
        jpeg = frames.get(tick.frame_ref)
        if jpeg is None:
            continue
        is_trigger = tick.tick_id == esc.tick.tick_id
        content.append(
            {"type": "input_text", "text": frame_label(tick, origin, is_trigger)}
        )
        content.append(
            {
                "type": "input_image",
                "image_url": _data_url(jpeg),
                # The trigger frame is the moment in question: send it sharp.
                # Context frames stay cheap (low ≈ 85 tokens each).
                "detail": "high" if is_trigger else "low",
            }
        )

    content.append({"type": "input_text", "text": "Decide the actions."})

    return [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": build_system_prompt(persona, seven_day, learned),
                }
            ],
        },
        {"role": "user", "content": content},
    ]
