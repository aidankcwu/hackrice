"""The context envelope (SPEC §4.2, §4.3).

Two properties carry the whole design and both are easy to break silently:
every image is immediately preceded by its own label with the trigger frame
last, and frames are chosen by phash movement rather than even spacing.
"""

from __future__ import annotations

import base64

from pipeline.models import AiBlock, Escalation, SensorBlock, Tick, TodaySummaryLine
from pipeline.reasoner.envelope import (
    UNKNOWN,
    build_envelope,
    frame_label,
    select_frames,
    tick_table,
)

T0 = 1_757_700_000.0


#: Distinguishes "use the default phash" from "this tick has none".
NO_PHASH = object()


def tick(
    seq: int,
    phash: object = None,
    ai: AiBlock | None = None,
    t: float | None = None,
    lux: float | None = 340.0,
) -> Tick:
    return Tick(
        tick_id=f"t_{seq:08d}",
        t=T0 + seq if t is None else t,
        seq=seq,
        sensor=SensorBlock(
            lux_proxy=lux,
            frame_delta=0.12,
            phash=None
            if phash is NO_PHASH
            else (phash if phash is not None else f"{seq:016x}"),  # type: ignore[arg-type]
        ),
        ai=ai,
        frame_ref=f"f_{seq:08d}",
    )


def office_ai(**flags: object) -> AiBlock:
    return AiBlock(as_of=T0, age_ms=0, scene="office", activity="seated", **flags)


def escalation(window: list[Tick], trigger: str = "food_in_frame") -> Escalation:
    return Escalation(
        trigger=trigger,
        t=window[-1].t,
        tick=window[-1],
        window=window,
        reason="food_present on 3 of the last 10 ticks",
    )


def frames_for(window: list[Tick]) -> dict[str, bytes]:
    return {tk.frame_ref: f"jpeg-{tk.seq}".encode() for tk in window}


# -- select_frames --------------------------------------------------------

#: Three plateaus with two large phash jumps between them.
A = "0000000000000000"
B = "ffffffff00000000"
C = "ffffffffffffffff"


def stepped_window() -> list[Tick]:
    phashes = [A, A, A, B, B, B, B, C, C, C]
    return [tick(i, phash=p) for i, p in enumerate(phashes)]


def test_select_frames_picks_change_points_and_ends_on_the_trigger():
    window = stepped_window()
    trigger = window[-1]

    chosen = select_frames(window, trigger, k=4)

    assert len(chosen) == 4
    assert chosen[-1] is trigger, "the trigger frame must be last"
    ids = [c.tick_id for c in chosen]
    assert "t_00000003" in ids, "the A->B jump is a change point"
    assert "t_00000007" in ids, "the B->C jump is a change point"
    assert len(set(ids)) == 4, "frames must be distinct"
    assert [c.t for c in chosen] == sorted(c.t for c in chosen), "chronological"


def test_select_frames_beats_even_spacing_on_a_motionless_window():
    # Nothing moves except at seq 8; even spacing would miss it entirely.
    phashes = [A] * 8 + [C, C]
    window = [tick(i, phash=p) for i, p in enumerate(phashes)]

    chosen = select_frames(window, window[-1], k=4)

    assert "t_00000008" in [c.tick_id for c in chosen]


def test_select_frames_skips_ticks_with_no_phash():
    window = [tick(0, phash=A), tick(1, phash=NO_PHASH), tick(2, phash=C)]

    chosen = select_frames(window, window[-1], k=4)

    assert all(c.sensor.phash is not None for c in chosen[:-1])
    assert "t_00000001" not in [c.tick_id for c in chosen]


def test_select_frames_returns_fewer_on_a_short_window():
    window = [tick(0, phash=A), tick(1, phash=C)]

    chosen = select_frames(window, window[-1], k=4)

    assert len(chosen) == 2
    assert chosen[-1] is window[-1]


def test_select_frames_never_repeats_the_trigger_tick():
    window = stepped_window()
    chosen = select_frames(window, window[-1], k=4)
    assert [c.tick_id for c in chosen].count(window[-1].tick_id) == 1


# -- tick table -----------------------------------------------------------


def test_tick_table_renders_unknown_fields_as_question_marks():
    window = [tick(0, ai=None, lux=None)]

    table = tick_table(window, origin=T0)

    header, row = table.splitlines()
    assert header.split() == [
        "t",
        "scene",
        "activity",
        "food",
        "screen",
        "people",
        "caff",
        "alc",
        "delta",
        "lux",
    ]
    cells = row.split()
    # scene, activity, food, screen, people, caff, alc, lux are all unknown.
    assert cells.count(UNKNOWN) == 8, row


def test_tick_table_distinguishes_false_from_unknown():
    window = [
        tick(0, ai=office_ai(screen_present=True, food_present=False)),
    ]

    row = tick_table(window, origin=T0).splitlines()[1].split()

    assert row[1] == "office"
    assert row[3] == "n", "food_present=False is 'n', not '?'"
    assert row[4] == "y"
    assert row[5] == UNKNOWN, "people_present unreported stays unknown"


def test_tick_table_shows_food_type_and_subsamples():
    window = [
        tick(i, ai=office_ai(food_present=True, food_type="mixed")) for i in range(20)
    ]

    table = tick_table(window, origin=window[-1].t)
    rows = table.splitlines()[1:]

    assert "mixed" in table
    assert 4 <= len(rows) <= 6, f"one row per ~5 s, got {len(rows)}"
    assert rows[0].startswith("t-19s")
    assert rows[-1].startswith("t-0s"), "the trigger second is always the last row"


def test_frame_label_falls_back_to_sensor_fields_with_no_ai_block():
    tk = tick(0, ai=None)

    label = frame_label(tk, origin=T0, is_trigger=True)

    assert label.startswith("t-0s (trigger frame) — ")
    assert "motion 0.12" in label
    assert f"scene {UNKNOWN}" in label


def test_frame_label_names_the_offset_and_the_live_flags():
    tk = tick(0, ai=office_ai(screen_present=True, people_present=False))

    label = frame_label(tk, origin=T0 + 45.0)

    assert label.startswith("t-45s — ")
    assert "motion 0.12" in label
    assert "lux 340" in label
    assert "scene office" in label
    assert "seated" in label
    assert "screen" in label
    assert "people" not in label, "False flags are not listed"


# -- build_envelope -------------------------------------------------------


def test_envelope_interleaves_a_label_before_every_image_trigger_last():
    window = stepped_window()
    esc = escalation(window)

    messages = build_envelope(
        esc, frames_for(window), [], "seven day text", "persona text"
    )

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    content = messages[1]["content"]

    # Header block: trigger, today, tick table -- before any pixels.
    assert content[0]["text"].startswith("Trigger: food_in_frame at ")
    assert "food_present on 3 of the last 10 ticks" in content[0]["text"]
    assert content[1]["text"].startswith("Today so far:")
    assert content[2]["text"].startswith("Tick table (last ")
    assert content[-1]["text"] == "Decide the actions."

    image_positions = [
        i for i, item in enumerate(content) if item["type"] == "input_image"
    ]
    assert len(image_positions) == 4
    for i in image_positions:
        assert content[i - 1]["type"] == "input_text", "every image needs a label"
        assert content[i - 1]["text"].startswith("t-")

    last_image = image_positions[-1]
    assert "(trigger frame)" in content[last_image - 1]["text"]
    assert last_image == len(content) - 2, "the trigger frame is the final image"
    assert all(
        "(trigger frame)" not in content[i - 1]["text"] for i in image_positions[:-1]
    )


def test_envelope_images_are_base64_data_urls_at_low_detail():
    window = stepped_window()
    esc = escalation(window)

    content = build_envelope(esc, frames_for(window), [], "7d", "p")[1]["content"]
    image = next(item for item in content if item["type"] == "input_image")

    assert image["detail"] == "low"
    prefix = "data:image/jpeg;base64,"
    assert image["image_url"].startswith(prefix)
    assert base64.b64decode(image["image_url"][len(prefix) :]).startswith(b"jpeg-")


def test_envelope_skips_frames_that_expired_before_the_copy():
    window = stepped_window()
    esc = escalation(window)
    frames = frames_for(window)
    # Drop everything but the trigger frame, as an over-90s lag would.
    frames = {esc.tick.frame_ref: frames[esc.tick.frame_ref]}

    content = build_envelope(esc, frames, [], "7d", "p")[1]["content"]

    images = [item for item in content if item["type"] == "input_image"]
    assert len(images) == 1
    assert "(trigger frame)" in content[content.index(images[0]) - 1]["text"]


def test_envelope_today_block_says_nothing_yet_when_empty():
    window = stepped_window()
    content = build_envelope(
        escalation(window), frames_for(window), [], "7d", "p"
    )[1]["content"]
    assert content[1]["text"] == "Today so far:\nnothing yet"


def test_envelope_today_block_carries_prior_annotations():
    window = stepped_window()
    lines = [
        TodaySummaryLine(t=T0 - 3600, line="coffee at the desk", decision_id="d_0001"),
        TodaySummaryLine(t=T0 - 1800, line="45 min at a screen", decision_id="d_0002"),
    ]

    content = build_envelope(
        escalation(window), frames_for(window), lines, "7d", "p"
    )[1]["content"]

    assert "coffee at the desk" in content[1]["text"]
    assert "45 min at a screen" in content[1]["text"]


def test_envelope_system_message_carries_persona_and_seven_day():
    window = stepped_window()
    messages = build_envelope(
        escalation(window), frames_for(window), [], "SEVEN-DAY-MARKER", "PERSONA-MARKER"
    )

    system = messages[0]["content"][0]["text"]
    assert "PERSONA-MARKER" in system
    assert "SEVEN-DAY-MARKER" in system
    assert "annotate" in system and "log_insight" in system and "watch" in system


def test_envelope_renders_extra_text_after_the_table_and_before_the_frames():
    """SPEC §14.3: the wearable HR line rides along with the tick table."""

    window = stepped_window()
    esc = escalation(window, trigger="biometric_anomaly")
    esc.extra_text = [
        "Heart rate (wearable, bpm) over the last 20s, resting 58: "
        "t-20s 96, t-15s 101, t-10s 104, t-5s 103, t-0s 105",
        "",  # empty lines are dropped, not rendered as blank items
    ]

    content = build_envelope(esc, frames_for(window), [], "7d", "p")[1]["content"]
    texts = [item["text"] for item in content if item["type"] == "input_text"]
    assert "" not in texts

    extra_index = next(
        i for i, item in enumerate(content)
        if item["type"] == "input_text" and item["text"].startswith("Heart rate")
    )
    table_index = next(
        i for i, item in enumerate(content)
        if item["type"] == "input_text" and item["text"].startswith("Tick table")
    )
    first_image = next(
        i for i, item in enumerate(content) if item["type"] == "input_image"
    )
    assert table_index < extra_index < first_image


def test_envelope_without_extra_text_is_unchanged():
    window = stepped_window()
    plain = build_envelope(escalation(window), frames_for(window), [], "7d", "p")
    esc = escalation(window)
    esc.extra_text = []
    assert build_envelope(esc, frames_for(window), [], "7d", "p") == plain
