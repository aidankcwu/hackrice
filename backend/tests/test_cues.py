"""Persona cues: one fresh tick, one hand-off per moment, straight to the mouth.

The gate half of the fast path. A rice krispy treat, a cucumber, a coffee
milkshake, a phone at the laptop, a crowd: the persona scripts a reaction to each,
and the wearer expects it in the same breath. These tests pin the three things
that make that work without making the glasses talk more:

* a cue fires on the first fresh tick that shows it -- including the first one
  after a blind gap -- and never on a stale or missing ``ai`` block;
* a moment is handed off at most once, however many ticks keep showing it and
  however the tagger relabels it, but a *different* prop is a new moment;
* the documented indoor label flicker no longer wakes the clerk at all.
"""

from __future__ import annotations

from pipeline.config import Timings
from pipeline.db import Database
from pipeline.episodes import EpisodeBuilder
from pipeline.gate import Trigger, TriggerGate, default_triggers
from pipeline.gate.gate import CUE_COVER_S, HANDED_OFF, NO_AGENT, REPEAT
from pipeline.gate.triggers import (
    CUE_HAND_GAP_S,
    CueMoments,
    change_trigger,
    cue_trigger,
    same_item,
    tick_cues,
)
from pipeline.models import AiBlock, Escalation, SensorBlock, Tick

TIMINGS = Timings.demo(tick_interval_s=1.5)
MAX_AGE = TIMINGS.ai_max_age_ms

#: What Person A's tagger sends on every tick now: the hand fields are always
#: present, null when the hands are empty or unseen.
DESK = dict(scene="office", activity="computer_use", objects=["laptop"],
            caption="laptop on a desk", food_present=False, food_type="none",
            drink="none", in_hand=None, phone_in_hand=None, people_count="1-2")


def tick(seq: int, *, t: float | None = None, ai: bool = True, age_ms: int = 0,
         **fields: object) -> Tick:
    """A 1.5 s-cadence tick. ``ai=False`` is a blind tick (the tagger missed)."""

    block = None
    if ai:
        block = AiBlock.model_validate({"age_ms": age_ms, **DESK, **fields})
    return Tick(
        tick_id=f"t_{seq}", t=seq * 1.5 if t is None else t, seq=seq,
        sensor=SensorBlock(frame_delta=0.1, phash=f"{seq:016x}"),
        ai=block, frame_ref=f"f_{seq}",
    )


TREAT = dict(in_hand="rice krispies treat", food_present=True,
             food_type="baked_goods", caption="holding a rice krispies treat",
             objects=["rice krispies treat", "laptop"])
CUCUMBER = dict(in_hand="cucumber", food_present=True, food_type="vegetables",
                caption="holding a cucumber", objects=["cucumber", "laptop"])
MILKSHAKE = dict(in_hand="coffee milkshake", drink="coffee", caffeine_visible=True,
                 caption="holding a coffee milkshake", objects=["coffee milkshake"])
PHONE = dict(in_hand="iphone", phone_in_hand=True, activity="phone_use",
             caption="holding a phone at a laptop", objects=["phone", "laptop"])


def kinds(t: Tick) -> list[tuple[str, str]]:
    return [(c.kind, c.key) for c in tick_cues(t, MAX_AGE)]


# -- what counts as a cue -------------------------------------------------------


def test_each_persona_prop_is_recognised_from_the_in_hand_field() -> None:
    assert kinds(tick(0, **TREAT)) == [("food", "food:treat")]
    assert kinds(tick(0, **CUCUMBER)) == [("food", "food:healthy")]
    assert kinds(tick(0, **MILKSHAKE)) == [("caffeine", "caffeine")]
    assert kinds(tick(0, **PHONE)) == [("phone", "phone")]
    assert kinds(tick(0, people_count="6+")) == [("crowd", "crowd")]
    # "3-5" is a table of teammates, true on nearly every hackathon tick; only
    # "6+" is the persona's room full of people.
    assert kinds(tick(0, people_count="3-5")) == []
    # Held item first, room second, and the item itself is kept for the topic.
    both = tick_cues(tick(0, people_count="6+", **TREAT), MAX_AGE)
    assert [c.kind for c in both] == ["food", "crowd"]
    assert both[0].item == "rice krispies treat" and both[0].label == "junk food"


def test_things_that_are_not_cues() -> None:
    assert kinds(tick(0)) == [], "an empty desk"
    assert kinds(tick(0, in_hand="water bottle", drink="water")) == []
    # The food flag is about the frame; a pen in the hand is still a pen.
    assert kinds(tick(0, in_hand="pen", food_present=True, food_type="sandwich")) == []
    assert kinds(tick(0, in_hand="backpack", food_present=True, food_type="snack")) == []
    # ... but the item's own name wins over the not-food list.
    assert kinds(tick(0, in_hand="chips bag", food_type="chips")) == [("food", "food:treat")]
    # Alcohol is `alcohol_seen`'s job (it already fires on one tick and asks).
    assert kinds(tick(0, in_hand="beer can", drink="beer", alcohol_visible=True)) == []
    # A phone with no laptop or screen anywhere is not "phone at the laptop".
    assert kinds(tick(0, in_hand="phone", phone_in_hand=True, objects=["phone"],
                      activity="walking", caption="holding a phone outside",
                      scene="street")) == []
    # A real head count below the crowd buckets is an answer, not unknown.
    assert kinds(tick(0, people_count="1-2", caption="crowded room")) == []
    assert kinds(tick(0, people_count="3-5", caption="a table of three")) == []


def test_a_3_to_5_count_with_crowd_language_is_still_a_crowd() -> None:
    """A distant or blurred audience on a 288x512 frame: Gemini can count only
    a few faces and says "3-5", but the caption still says "audience". The
    count must not silence the persona's crowd line; a 3-person table never
    gets crowd language, so the table false positive does not come back."""

    assert kinds(tick(0, people_count="3-5", caption="crowded room")) == [("crowd", "crowd")]
    assert kinds(tick(0, people_count="3-5", caption="desk",
                      objects=["audience", "stage"])) == [("crowd", "crowd")]
    assert kinds(tick(0, people_count="3-5",
                      caption="a group of people around a laptop")) == []
    # Below 3-5, crowd words are still overruled by the count.
    assert kinds(tick(0, people_count="1-2", caption="crowded room")) == []


def test_a_crowd_needs_six_plus_or_explicit_crowd_language() -> None:
    """The crowd line must not fire at the start of every session: a hackathon
    table is 3-5 people on nearly every tick."""

    assert kinds(tick(0, people_count="6+")) == [("crowd", "crowd")]
    for count in ("0", "1-2", "3-5"):
        assert kinds(tick(0, people_count=count)) == [], count
    # No count reported: only explicit crowd words count, not "a group".
    assert kinds(tick(0, people_count="unknown",
                      caption="a group of people around a laptop")) == []
    assert kinds(tick(0, people_count="unknown",
                      caption="a room full of people at a hackathon")) == [("crowd", "crowd")]
    assert kinds(tick(0, people_count="unknown", caption="desk",
                      objects=["audience", "stage"])) == [("crowd", "crowd")]
    # A whole session of a 3-5 table never fires the crowd trigger.
    trigger = cue_trigger(TIMINGS, CueMoments())
    assert run(trigger, [tick(i, people_count="3-5") for i in range(20)]) == []


def test_drinks_need_a_name_or_a_real_drink_tag() -> None:
    """A bare cup says nothing; a cup of water must never get a line."""

    assert kinds(tick(0, in_hand="cup", drink="water")) == []
    assert kinds(tick(0, in_hand="water bottle", drink="none", caffeine_visible=True)) == []
    assert kinds(tick(0, in_hand="cup", drink="none")) == []
    assert kinds(tick(0, in_hand="cup", drink="juice")) == [("drink", "drink")]
    assert kinds(tick(0, in_hand="strawberry smoothie")) == [("drink", "drink")]
    # A container with caffeine in the frame is the caffeinated drink.
    assert kinds(tick(0, in_hand="can", caffeine_visible=True,
                      drink="energy_drink")) == [("caffeine", "caffeine")]
    # A food label on a drink-shaped thing does not make it food.
    assert kinds(tick(0, in_hand="cup", food_present=True, food_type="snack",
                      drink="none")) == []


def test_a_stale_or_missing_ai_block_shows_no_cue() -> None:
    assert kinds(tick(0, ai=False)) == []
    assert kinds(tick(0, age_ms=MAX_AGE + 1, **TREAT)) == []


def test_unknown_is_never_read_as_a_negative() -> None:
    """Tri-state fields: null / "unknown" fall back to other evidence, and never
    stand in for "no phone" or "no crowd"."""

    # phone_in_hand unknown, but the tagger's own activity says phone use.
    assert kinds(tick(0, phone_in_hand=None, activity="phone_use",
                      screen_present=None, objects=["laptop"])) == [("phone", "phone")]
    # people_count unknown: the caption still counts.
    assert kinds(tick(0, people_count="unknown",
                      caption="a crowded hackathon hall")) == [("crowd", "crowd")]
    # screen_present unknown is not "no screen": the laptop in objects counts.
    assert kinds(tick(0, screen_present=None, **PHONE)) == [("phone", "phone")]


def test_old_producers_without_in_hand_fall_back_to_the_caption() -> None:
    """The sim and old replays never send `in_hand`; the caption heuristic keeps
    them working. A producer that *does* send it is trusted when it says null."""

    def legacy(**fields: object) -> Tick:
        return Tick(tick_id="t_0", t=0.0, seq=0, sensor=SensorBlock(),
                    ai=AiBlock(age_ms=0, **fields), frame_ref="f_0")

    assert kinds(legacy(caption="holding a snack bar", food_present=True,
                        food_type="snack")) == [("food", "food:treat")]
    assert kinds(legacy(caption="coffee cup in hand", caffeine_visible=True,
                        drink="coffee")) == [("caffeine", "caffeine")]
    # "handle" is not "hand".
    assert kinds(legacy(caption="mug handle by a laptop", food_present=True)) == []
    # New producer: hands reported empty, so the caption's "holding" is not used.
    assert kinds(tick(0, in_hand=None, caption="holding a snack bar",
                      food_present=True, food_type="snack")) == []


def test_same_item_survives_relabelling_but_not_a_new_prop() -> None:
    assert same_item("rice krispies treat", "rice krispie treat")
    assert same_item("rice krispie treat", "krispie treat")
    assert not same_item("rice krispies treat", "cucumber")
    assert not same_item("coffee milkshake", "energy drink can")
    assert same_item("", "anything"), "an unnamed side cannot be told apart"


# -- one fresh tick, one hand-off per moment ------------------------------------


def run(trigger: Trigger, ticks: list[Tick], accept: bool = True) -> list[str]:
    """Feed ticks through one trigger the way the gate does; return fired keys."""

    fired: list[str] = []
    window: list[Tick] = []
    for current in ticks:
        window.append(current)
        if trigger.predicate(window):
            key = trigger.cue(window)[0]  # type: ignore[misc]
            if accept:
                trigger.on_fired(current.t)  # type: ignore[misc]
                fired.append(key)
    return fired


def test_a_cue_fires_on_its_first_fresh_tick() -> None:
    trigger = cue_trigger(TIMINGS)
    window = [tick(0), tick(1), tick(2, **TREAT)]
    assert not trigger.predicate(window[:2])
    assert trigger.predicate(window), "one tick, not two"
    key, item, topic, mode = trigger.cue(window)  # type: ignore[misc]
    assert (key, item, mode) == ("food:treat", "rice krispies treat", "statement")
    assert topic.startswith("rice krispies treat in hand (junk food)")
    assert "holding a rice krispies treat" in topic
    reason, extra = trigger.enrich(window)  # type: ignore[misc]
    assert reason == "junk food: rice krispies treat"
    assert extra and extra[0].startswith("Persona cue (food):")
    assert trigger.bypass_gap and trigger.cooldown_s == 0.0


def test_caffeine_is_handed_off_as_a_question() -> None:
    trigger = cue_trigger(TIMINGS)
    window = [tick(0, **MILKSHAKE)]
    assert trigger.predicate(window)
    assert trigger.cue(window)[3] == "question"  # type: ignore[index,misc]


def test_a_held_prop_is_one_moment_across_ticks_blind_gaps_and_relabels() -> None:
    trigger = cue_trigger(TIMINGS)
    ticks = [
        tick(0), tick(1, **TREAT),
        tick(2, **{**TREAT, "food_type": "snack"}),       # relabelled
        tick(3, ai=False), tick(4, ai=False),             # blind
        tick(5, **{**TREAT, "in_hand": "rice krispie treat", "food_type": "dessert"}),
        tick(6, in_hand=None), tick(7, **TREAT),          # hand out of view
    ]
    assert run(trigger, ticks) == ["food:treat"]


def test_a_different_prop_straight_after_is_a_new_moment() -> None:
    trigger = cue_trigger(TIMINGS)
    ticks = [tick(0, **TREAT), tick(1, **TREAT), tick(2, **CUCUMBER),
             tick(3, **CUCUMBER), tick(4, **MILKSHAKE), tick(5, **PHONE)]
    assert run(trigger, ticks) == ["food:treat", "food:healthy", "caffeine", "phone"]


def test_a_cue_after_a_blind_gap_still_fires() -> None:
    """Session start and long tagger dropouts: no 'before' is needed."""

    trigger = cue_trigger(TIMINGS)
    ticks = [tick(i, ai=False) for i in range(8)] + [tick(8, **TREAT)]
    assert run(trigger, ticks) == ["food:treat"]

    # And a moment that ended long ago is a new one when the prop returns.
    later = tick(8 + int(CUE_HAND_GAP_S / 1.5) + 2, **TREAT)
    assert run(trigger, [*ticks, later]) == ["food:treat"]


def test_a_refused_hand_off_is_not_spent_and_tries_again_next_tick() -> None:
    """Drop, never queue: nothing is buffered, but the moment is not lost either
    -- the next fresh tick that still shows it is a fresh look."""

    trigger = cue_trigger(TIMINGS)
    window = [tick(0, **TREAT)]
    assert trigger.predicate(window)  # ... and the voice agent was busy: no on_fired
    window.append(tick(1, **TREAT))
    assert trigger.predicate(window)
    trigger.on_fired(1.5)  # type: ignore[misc]
    window.append(tick(2, **TREAT))
    assert not trigger.predicate(window)


def test_a_crowd_fires_once_on_onset_and_not_every_tick() -> None:
    trigger = cue_trigger(TIMINGS)
    ticks = [tick(0), tick(1, people_count="6+"), tick(2, people_count="1-2"),
             *(tick(i, people_count="3-5") for i in range(3, 12))]
    assert run(trigger, ticks) == ["crowd"]


def test_reset_forgets_the_moments_for_the_next_wearer() -> None:
    trigger = cue_trigger(TIMINGS)
    assert run(trigger, [tick(0, people_count="6+")]) == ["crowd"]
    trigger.reset()  # type: ignore[misc]
    assert run(trigger, [tick(1, people_count="6+")]) == ["crowd"]


# -- the change trigger keeps two ticks and stops flickering ---------------------


def test_indoor_scene_and_desk_posture_flicker_does_not_fire() -> None:
    """Live wake-ups: office -> indoor_other, office -> classroom, classroom ->
    indoor_other, seated -> computer_use -> standing, all at one desk."""

    trigger = change_trigger(TIMINGS)
    flicker = [("office", "seated"), ("office", "seated"),
               ("indoor_other", "computer_use"), ("indoor_other", "computer_use"),
               ("classroom", "standing"), ("classroom", "standing"),
               ("home", "reading"), ("home", "reading")]
    window: list[Tick] = []
    for i, (scene, activity) in enumerate(flicker):
        window.append(tick(i, scene=scene, activity=activity))
        assert not trigger.predicate(window), (scene, activity)


def test_a_real_scene_or_activity_change_still_needs_two_ticks_and_fires() -> None:
    trigger = change_trigger(TIMINGS)
    window = [tick(0), tick(1), tick(2, scene="street", activity="walking")]
    assert not trigger.predicate(window), "one tick is not enough for a scene"
    window.append(tick(3, scene="street", activity="walking"))
    assert trigger.predicate(window)
    reason, _ = trigger.enrich(window)  # type: ignore[misc]
    assert "scene office -> street" in reason
    assert "activity computer_use -> walking" in reason


def test_change_leaves_the_hand_to_a_live_cue() -> None:
    moments = CueMoments()
    cue, change = cue_trigger(TIMINGS, moments), change_trigger(TIMINGS, moments)
    window = [tick(0), tick(1)]
    for i in range(2, 6):
        window.append(tick(i, **TREAT))
        cue.predicate(window)
        assert not change.predicate(window), "food/objects belong to the cue"
    # Put down and gone for longer than the moment gap: food changes are back.
    base = 6 + int(CUE_HAND_GAP_S / 1.5) + 1
    for i in range(6, base):
        window.append(tick(i))
        cue.predicate(window)
    window += [tick(base, food_present=True, food_type="salad", objects=["salad bowl", "laptop"]),
               tick(base + 1, food_present=True, food_type="salad", objects=["salad bowl", "laptop"])]
    cue.predicate(window)
    assert change.predicate(window)
    assert "food: salad" in change.enrich(window)[0]  # type: ignore[misc]


# -- the gate: fast path, gap exemption, cover ----------------------------------


class Mouth:
    """Stands in for ``Reasoner.fast_path``: records hand-offs, answers as told."""

    def __init__(self, answers: list[str] | None = None) -> None:
        self.answers = answers or []
        self.calls: list[Escalation] = []

    def __call__(self, esc: Escalation) -> str:
        self.calls.append(esc)
        outcome = self.answers.pop(0) if self.answers else f"{HANDED_OFF}c_{len(self.calls)}"
        if outcome.startswith(HANDED_OFF):
            esc.handed_off = outcome.split(":", 1)[1]
        return outcome


def gate_for(tmp_path, mouth=None):
    db = Database(tmp_path / "cues.db").connect().init_schema()
    episodes = EpisodeBuilder(db, TIMINGS)
    clerk: list[Escalation] = []
    gate = TriggerGate(default_triggers(TIMINGS, True), TIMINGS, db, episodes,
                       lambda e: clerk.append(e) is None, True, fast_path=mouth)
    return db, episodes, gate, clerk


def feed(gate, episodes, ticks):
    out = []
    for current in ticks:
        episodes.on_tick(current)
        out.append(gate.on_tick(current))
    return out


def test_the_gate_hands_a_cue_to_the_mouth_on_its_first_tick(tmp_path) -> None:
    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        results = feed(gate, episodes, [tick(0), tick(1), tick(2, **TREAT),
                                        tick(3, **TREAT), tick(4, **TREAT)])
        assert results[2] is not None and results[2].trigger == "cue"
        (esc,) = mouth.calls
        assert esc.tick.tick_id == "t_2", "the tick the treat appeared on"
        assert esc.cue == "food:treat" and esc.cue_item == "rice krispies treat"
        assert esc.cue_topic.startswith("rice krispies treat in hand")
        assert esc.cue_mode == "statement"
        assert gate.fast_pathed == 1 and gate.fired["cue"] == 1
        assert clerk == [], "the clerk half is the fast path's job, not the gate's"
        assert gate.stats()["fast_pathed"] == 1
    finally:
        db.close()


def test_a_cue_skips_the_global_gap(tmp_path) -> None:
    """A scene change woke the clerk one tick earlier; the coffee is still
    answered on the tick it appears."""

    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        feed(gate, episodes, [tick(0), tick(1), tick(2, scene="street")])
        assert [e.t for e in clerk] == [3.0], "a wake-up at t=3.0 opens the gap"
        # t=4.5 is inside the 2 s gap: anything but a cue would be suppressed.
        feed(gate, episodes, [tick(3, scene="street", **MILKSHAKE)])
        assert gate.last_escalation_t == 4.5
        assert [e.cue for e in mouth.calls] == ["caffeine"]
        assert mouth.calls[0].cue_mode == "question"
        assert "cue" not in gate.suppressed
    finally:
        db.close()


def test_a_busy_mouth_drops_the_cue_without_spending_the_moment(tmp_path) -> None:
    mouth = Mouth(["conversation_active", "conversation_active"])
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        results = feed(gate, episodes, [tick(0, **TREAT), tick(1, **TREAT),
                                        tick(2, **TREAT), tick(3, **TREAT)])
        assert len(mouth.calls) == 3, "tried on every tick until it landed"
        assert results[:2] == [None, None] and results[2] is not None
        assert gate.fast_dropped["conversation_active"] == 2
        assert gate.fast_pathed == 1
        assert clerk == [], "nothing was queued for the clerk meanwhile"
    finally:
        db.close()


def test_a_repeat_spends_the_moment_without_waking_anyone(tmp_path) -> None:
    mouth = Mouth([REPEAT])
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        feed(gate, episodes, [tick(0, **TREAT), tick(1, **TREAT), tick(2, **TREAT)])
        assert len(mouth.calls) == 1 and clerk == []
        assert gate.fast_dropped[REPEAT] == 1 and gate.fired["cue"] == 0
    finally:
        db.close()


def test_without_a_voice_agent_a_cue_goes_to_the_clerk(tmp_path) -> None:
    for mouth in (None, Mouth([NO_AGENT])):
        db, episodes, gate, clerk = gate_for(tmp_path, mouth)
        try:
            feed(gate, episodes, [tick(0), tick(1, **TREAT), tick(2, **TREAT)])
            assert [e.trigger for e in clerk] == ["cue"]
            assert clerk[0].cue == "food:treat" and clerk[0].handed_off is None
        finally:
            db.close()
        (tmp_path / "cues.db").unlink()


def test_no_phone_to_speak_through_sends_the_cue_to_the_clerk(tmp_path) -> None:
    """Nothing can be said, but the moment is still written down -- and the
    gate does not retry into a mouth that is not there on every tick."""

    mouth = Mouth(["no_transport"])
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        feed(gate, episodes, [tick(0, **TREAT), tick(1, **TREAT), tick(2, **TREAT)])
        assert len(mouth.calls) == 1
        assert [e.trigger for e in clerk] == ["cue"] and clerk[0].handed_off is None
        assert gate.fired["cue"] == 1
    finally:
        db.close()


def test_a_handed_off_caffeine_cue_covers_caffeine_seen(tmp_path) -> None:
    """The coffee is not handed off twice: `caffeine_seen` would otherwise fire
    the moment the global gap passed and the clerk would ask about it again."""

    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        ticks = [tick(i, **MILKSHAKE) for i in range(0, 12)]
        feed(gate, episodes, ticks)
        assert [e.cue for e in mouth.calls] == ["caffeine"]
        assert "caffeine_seen" not in [e.trigger for e in clerk]
        assert gate.suppressed["caffeine_seen"] > 0
        # The cue escalation is bound to the caffeine episode it belongs to,
        # so the clerk's memory line labels that episode.
        open_caffeine = episodes.open_episodes().get("caffeine_sighting")
        assert open_caffeine is not None
        assert mouth.calls[0].episode_id == open_caffeine.id
        assert CUE_COVER_S >= 20
    finally:
        db.close()


def test_other_escalations_are_stamped_with_the_cue_their_tick_shows(tmp_path) -> None:
    """So the voice agent can refuse a second line about the same coffee from a
    clerk wake-up that was about something else."""

    db, episodes, gate, clerk = gate_for(tmp_path, mouth=None)
    try:
        gate.triggers = [t for t in gate.triggers if t.name == "change"]
        feed(gate, episodes, [tick(0), tick(1), tick(2, scene="street", **MILKSHAKE),
                              tick(3, scene="street", **MILKSHAKE)])
        (esc,) = clerk
        assert esc.trigger == "change"
        assert (esc.cue, esc.cue_item) == ("caffeine", "coffee milkshake")
        assert esc.cue_topic == "" and esc.handed_off is None
    finally:
        db.close()


def test_scene_flicker_alone_wakes_nothing_through_the_gate(tmp_path) -> None:
    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        scenes = ["office", "office", "indoor_other", "indoor_other", "classroom",
                  "classroom", "office", "office", "home", "home"]
        feed(gate, episodes, [tick(i, scene=s) for i, s in enumerate(scenes)])
        assert clerk == [] and mouth.calls == []
    finally:
        db.close()


def test_a_tick_re_sent_with_its_ai_replaces_the_blind_copy(tmp_path) -> None:
    """Publish-on-landing (longevity.loop) re-sends the newest tick, same id and
    time, once Gemini lands. The gate must see one tick -- and the cue on it."""

    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        blind = tick(3, ai=False)
        gate.on_tick(tick(2))
        assert gate.on_tick(blind) is None
        landed = tick(3, **TREAT)
        assert gate.on_tick(landed) is not None
        assert [t.tick_id for t in gate.window] == ["t_2", "t_3"]
        assert gate.window[-1].ai is not None
        assert [e.tick.tick_id for e in mouth.calls] == ["t_3"]
    finally:
        db.close()


def test_production_keeps_its_global_gap_for_cues_too() -> None:
    """The demo exemption is the demo's: the production preset stays
    conservative, and a cue there waits out the 60 s gap like anything else."""

    demo = default_triggers(TIMINGS, True)[0]
    production = default_triggers(Timings.production(tick_interval_s=1.5), False)[0]
    assert demo.name == production.name == "cue"
    assert demo.bypass_gap and not production.bypass_gap
    assert Timings.production().global_escalation_min_gap == 60.0
    assert Timings.production().conversation_cooldown_s == 60.0


def test_gate_reset_lets_the_next_wearer_trigger_the_same_cue(tmp_path) -> None:
    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        feed(gate, episodes, [tick(0, people_count="6+"), tick(1, people_count="6+")])
        gate.reset()
        feed(gate, episodes, [tick(2, people_count="6+")])
        assert [e.cue for e in mouth.calls] == ["crowd", "crowd"]
    finally:
        db.close()


# -- review fixes: what the hand is, which moment it is, what the stamp means ---


def test_a_prop_in_the_hand_is_not_food_because_food_is_on_the_desk() -> None:
    """A cucumber put down on the desk (live, 09:59 and 10:13) made a shaker or
    the HackRice sign in the hand "healthy food". Only the item's own words
    make the hand a food cue."""

    desk_cucumber = dict(food_present=True, food_type="vegetables",
                         caption="cucumber on the table")
    assert kinds(tick(0, in_hand="shaker", **desk_cucumber)) == []
    assert kinds(tick(0, in_hand="hackrice sign", **desk_cucumber)) == []
    # Visibly eating something no regex knows is still food.
    assert kinds(tick(0, in_hand="burrito", activity="eating", food_present=True,
                      food_type="mixed")) == [("food", "food:healthy")]


def test_the_items_own_words_pick_the_food_family_not_the_churning_food_type() -> None:
    """One relabelled tick used to move the treat to ``food:healthy`` -- a second
    moment, a second line, and the treat called healthy."""

    for food_type in ("grains", "mixed", "baked_goods", "processed"):
        (cue,) = tick_cues(tick(0, **{**TREAT, "food_type": food_type}), MAX_AGE)
        assert cue.key == "food:treat", food_type
        assert cue.label != "healthy food"
    (cue,) = tick_cues(tick(0, **{**CUCUMBER, "food_type": "processed"}), MAX_AGE)
    assert (cue.key, cue.label) == ("food:healthy", "healthy food")


def test_a_phone_is_only_at_the_laptop_when_a_laptop_is_in_view() -> None:
    """A held phone's display is a screen; Gemini says so. Walking on stage
    checking the phone is not "phone at the laptop"."""

    on_stage = dict(in_hand="iphone", phone_in_hand=True, activity="phone_use",
                    objects=["phone"], screen_present=True,
                    caption="holding a phone showing its screen")
    assert kinds(tick(0, **on_stage)) == []
    assert kinds(tick(0, **{**on_stage, "objects": ["phone", "laptop"]})) == [("phone", "phone")]
    assert kinds(tick(0, **{**on_stage, "activity": "computer_use"})) == [("phone", "phone")]


def test_a_different_named_item_under_the_same_key_is_a_new_moment() -> None:
    """Treat then chips (both ``food:treat``), coffee then tea (both
    ``caffeine``): no 12 s gap between them, so keyed on the key alone the
    second was never spoken. Two ticks of the new item, so one odd spelling
    of the treat is not a new moment."""

    chips = dict(in_hand="bag of chips", food_present=True, food_type="chips",
                 caption="holding a bag of chips", objects=["chips", "laptop"])
    trigger = cue_trigger(TIMINGS)
    ticks = [tick(0, **TREAT), tick(1, **TREAT), tick(2, **chips), tick(3, **chips),
             tick(4, **chips)]
    assert run(trigger, ticks) == ["food:treat", "food:treat"]

    tea = dict(in_hand="cup of tea", drink="tea", caffeine_visible=True,
               caption="holding a cup of tea")
    trigger = cue_trigger(TIMINGS)
    assert run(trigger, [tick(0, **MILKSHAKE), tick(1, **tea), tick(2, **tea)]) == [
        "caffeine", "caffeine"]


def test_one_odd_spelling_of_the_same_treat_is_not_a_new_moment() -> None:
    trigger = cue_trigger(TIMINGS)
    ticks = [tick(0, **TREAT), tick(1, **{**TREAT, "in_hand": "granola bar"}),
             tick(2, **TREAT), tick(3, **{**TREAT, "in_hand": "krispie treat"})]
    assert run(trigger, ticks) == ["food:treat"]


def test_a_caption_path_relabel_never_splits_a_moment() -> None:
    """Without ``in_hand`` the item is just ``food_type``, which churns."""

    old = dict(in_hand=None, caption="hand holding a snack", food_present=True)
    for field in ("in_hand", "phone_in_hand"):
        old.pop(field, None)
    moments = CueMoments()
    trigger = cue_trigger(TIMINGS, moments)
    ticks = []
    for i, food_type in enumerate(["baked_goods", "snack", "snack", "dessert", "dessert"]):
        current = tick(i, **{**old, "food_type": food_type})
        current.ai.__pydantic_fields_set__.discard("in_hand")
        ticks.append(current)
    assert run(trigger, ticks) == ["food:treat"]


def test_live_spent_is_the_prop_still_in_the_hand() -> None:
    moments = CueMoments()
    trigger = cue_trigger(TIMINGS, moments)
    assert run(trigger, [tick(0, **TREAT)]) == ["food:treat"]
    assert moments.live_spent(1.5) == ("food:treat", "rice krispies treat")
    assert moments.live_spent(CUE_HAND_GAP_S + 1) is None, "the moment ended"


def test_a_clerk_wake_up_on_a_blind_hand_tick_is_stamped_with_the_live_prop(tmp_path) -> None:
    """``in_hand`` null is unknown, not "put down". The treat just spoken is
    still the treat, so the stamp comes from the live moment and says so."""

    mouth = Mouth()
    db, episodes, gate, clerk = gate_for(tmp_path, mouth)
    try:
        feed(gate, episodes, [tick(0, **TREAT)])
        assert [e.cue for e in mouth.calls] == ["food:treat"]
        hands_unseen = tick(1, scene="street", in_hand=None, food_present=None)
        esc = Escalation(trigger="screen_sustained", t=hands_unseen.t, tick=hands_unseen)
        gate._stamp_cue(esc)
        assert (esc.cue, esc.cue_item, esc.cue_spent) == (
            "food:treat", "rice krispies treat", True)
        # A crowd on the tick does not beat the prop: the prop is the one the
        # clerk might repeat.
        crowded = tick(2, in_hand=None, people_count="6+")
        esc = Escalation(trigger="screen_sustained", t=crowded.t, tick=crowded)
        gate._stamp_cue(esc)
        assert esc.cue == "food:treat" and esc.cue_spent
        # Nothing held and nothing live: the crowd, not spent.
        later = tick(30, people_count="6+")
        esc = Escalation(trigger="screen_sustained", t=later.t, tick=later)
        gate._stamp_cue(esc)
        assert (esc.cue, esc.cue_spent) == ("crowd", False)
    finally:
        db.close()


def test_topic_about_cue_reads_the_topic_not_the_view() -> None:
    from pipeline.gate.triggers import topic_about_cue

    assert topic_about_cue("rice krispie treat, put it down", "food:treat",
                           "rice krispies treat")
    assert topic_about_cue("snack in hand, junk food", "food:treat", "rice krispies treat")
    assert topic_about_cue("crowd in view (6+ people)", "crowd", "crowd")
    assert not topic_about_cue("20 minutes at the screen, stand up and look away",
                               "crowd", "crowd")
    assert not topic_about_cue("is that a beer?", "crowd", "crowd")
    assert not topic_about_cue("20 minutes at the screen, stand up", "food:treat",
                               "rice krispies treat")
    assert not topic_about_cue("anything", None, "")
