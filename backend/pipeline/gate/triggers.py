"""Built-in stateful trigger predicates."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re
from typing import Callable, Mapping, Protocol, runtime_checkable

from ..config import DEFAULT_KEYWORD_TRIGGERS, Timings
from ..models import (
    EXERTION_ACTIVITIES,
    HEALTHY_FOOD_TYPES,
    OUTDOOR_SCENES,
    UNHEALTHY_FOOD_TYPES,
    EpisodeKind,
    Tick,
)

__all__ = [
    "BiometricFeed",
    "CallableBiometricFeed",
    "Cue",
    "CueMoments",
    "Trigger",
    "biometric_anomaly_trigger",
    "change_trigger",
    "cue_trigger",
    "default_triggers",
    "keyword_trigger",
    "same_item",
    "tick_cues",
    "topic_about_cue",
    "wearable_now_line",
]

#: Fraction of the window the HR series must actually cover.
_MIN_SPAN_FRACTION = 0.8

#: Samples needed before a series is worth believing.
_MIN_SAMPLES = 5

#: Fraction of those samples that must sit above resting x ratio.
_MIN_ELEVATED_FRACTION = 0.9

#: At most this many points are rendered into the escalation's extra line.
_HR_LINE_POINTS = 12

_BIOMETRIC_REASON = "Heart rate {hr:.0f} vs resting {rest:.0f} while not exercising"

#: ``medication_seen`` cooldown (PLAN 2.2): one dose is one sighting, not one
#: per 20 s while the bottle sits in view.
MEDICATION_COOLDOWN_S = 20 * 60.0

#: With ``reads_watch`` a sustained trigger wants the concept hot on this share
#: of the window's watch-bearing ticks (docs/PERCEPTION.md "Gate and actions",
#: phase 3: "above enter on most ticks in the window").
WATCH_HOT_FRACTION = 0.7

#: Enter threshold used when no ``watch_thresholds`` were handed in; mirrors
#: ``capture.settings.DEFAULT_ENTER`` (not imported: ``pipeline.capture``
#: pulls the whole bridge in, and the gate must stay free of it).
DEFAULT_WATCH_ENTER = 0.60


@runtime_checkable
class BiometricFeed(Protocol):
    """Read-only view of the intraday wearable series (SPEC §14.2).

    Every method is synchronous and cheap -- the gate runs on every tick and
    never awaits. ``series`` returns ``(t, value)`` pairs on the tick clock for
    any metric in :data:`pipeline.wearables.LIVE_METRICS`; ``hr_series`` is the
    heart-rate special case the ``biometric_anomaly`` trigger was built around
    and is kept as its own name because that trigger reads it every tick.

    The feed does not care whether the numbers are seeded or came off a real
    watch -- that is the store's business (live rows win for any window they
    cover), which is what lets a wearable be plugged in mid-run.
    """

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]: ...

    def resting_hr(self) -> float: ...

    def series(self, metric: str, t0: float, t1: float) -> list[tuple[float, float]]: ...

    def latest(self, metric: str) -> tuple[float, float] | None: ...


@dataclass(frozen=True, slots=True)
class CallableBiometricFeed:
    """A :class:`BiometricFeed` over injected callables.

    Keeps the gate free of any dependency on the store: wiring passes
    ``db.biometric_series`` / ``db.latest_biometric``-shaped functions in,
    nothing here imports them. ``resting_fn`` is re-read per call so an
    overnight update is picked up.
    """

    series_fn: Callable[[str, float, float], list[tuple[float, float]]]
    resting_fn: Callable[[], float]
    #: ``db.latest_biometric``-shaped: ``(t, value, source, origin) | None``.
    latest_fn: Callable[[str], tuple[float, float, str, str] | None] | None = None
    metric: str = "heart_rate"

    def series(self, metric: str, t0: float, t1: float) -> list[tuple[float, float]]:
        try:
            return list(self.series_fn(metric, t0, t1))
        except Exception:  # pragma: no cover - a feed read must never break a tick
            return []

    def hr_series(self, t0: float, t1: float) -> list[tuple[float, float]]:
        return self.series(self.metric, t0, t1)

    def resting_hr(self) -> float:
        try:
            return float(self.resting_fn())
        except Exception:  # pragma: no cover - defensive
            return 0.0

    def _latest_row(self, metric: str) -> tuple[float, float, str, str] | None:
        if self.latest_fn is None:
            return None
        try:
            return self.latest_fn(metric)
        except Exception:  # pragma: no cover - defensive
            return None

    def latest(self, metric: str) -> tuple[float, float] | None:
        row = self._latest_row(metric)
        return None if row is None else (row[0], row[1])

    def latest_source(self, metric: str) -> str | None:
        """Which device reported the newest sample, for the "wearable now" line."""

        row = self._latest_row(metric)
        return None if row is None else row[2]


@dataclass(frozen=True, slots=True)
class Trigger:
    name: str
    predicate: Callable[[list[Tick]], bool]
    cooldown_s: float
    episode_kind: EpisodeKind | None
    reason: str
    #: Optional hook run once, at fire time, on the same window the predicate
    #: saw. Returns ``(reason, extra_text)`` -- the rendered reason and any
    #: extra context lines for the envelope (SPEC §14.3). Synchronous, and
    #: allowed to return empties; the gate falls back to ``reason``.
    enrich: Callable[[list[Tick]], tuple[str, list[str]]] | None = None
    #: Optional hook the gate calls with the tick time only once the
    #: escalation was actually accepted (not when T1 was busy or the global
    #: gap rejected it). A trigger with its own budget spends it here.
    on_fired: Callable[[float], None] | None = None
    #: Skip the gate's global escalation gap: a persona cue must not queue
    #: behind a scene-change wake-up it has nothing to do with.
    bypass_gap: bool = False
    #: Set on a persona-cue trigger only. Returns ``(key, item, topic, mode)``
    #: for the moment the predicate just saw, and tells the gate this moment
    #: goes straight to the voice agent (``TriggerGate(fast_path=...)``) with
    #: the clerk running beside it in silence.
    cue: Callable[[list[Tick]], tuple[str, str, str, str] | None] | None = None
    #: Forget per-wearer state on a session start (``TriggerGate.reset``), so
    #: the next wearer's first crowd is a new moment, not the tail of the last.
    reset: Callable[[], None] | None = None
    #: The cue trigger's live moments, so the gate can label a clerk wake-up
    #: with the prop that is still being held even on a tick whose hands are
    #: out of view (``TriggerGate._stamp_cue``).
    moments: "CueMoments | None" = None


def _recent(window: list[Tick], seconds: float) -> list[Tick]:
    if not window:
        return []
    cutoff = window[-1].t - seconds
    return [tick for tick in window if tick.t >= cutoff]


def _flag_hits(
    name: str, seconds: float, minimum: int, max_age_ms: int = 3000
) -> Callable[[list[Tick]], bool]:
    """``minimum`` positive readings of ``name`` inside the last ``seconds``.

    ``minimum`` is a count of *ticks*, so the caller must already have run the
    1 Hz reference number through :meth:`Timings.scaled_hits`; ``max_age_ms``
    is the cadence-aware freshness budget (``Timings.ai_max_age_ms``).
    """

    def predicate(window: list[Tick]) -> bool:
        ticks = _recent(window, seconds)
        known = [
            value
            for value in (tick.flag(name, max_age_ms) for tick in ticks)
            if value is not None
        ]
        return bool(known) and known[-1] is True and sum(value is True for value in known) >= minimum

    return predicate


def _condition_hits(
    condition: Callable[[Tick], bool | None], seconds: float, minimum: int
) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        known = [v for t in _recent(window, seconds) if (v := condition(t)) is not None]
        return bool(known) and known[-1] is True and sum(v is True for v in known) >= minimum

    return predicate


def _watch_persisted(
    concepts: tuple[str, ...],
    enter: Mapping[str, float],
    confirm: Callable[[Tick], bool | None],
    fallback: Callable[[list[Tick]], bool],
    seconds: float,
    min_watched: int = 1,
) -> Callable[[list[Tick]], bool]:
    """The phase-3 sustained rule: the watcher proves how long, the labeler what.

    Fires when the concept (any of ``concepts``) scores at or above its enter
    threshold on at least :data:`WATCH_HOT_FRACTION` of the ticks in the last
    ``seconds`` that carry a ``watch`` block, AND at least one tick in that
    window has ``confirm`` -- the trigger's own fresh ``ai`` reading -- True.
    A tick without ``watch`` counts neither for nor against; a window with fewer
    than ``min_watched`` watch-bearing ticks (the trigger's own hit count, so a
    "sustained" state cannot be proved by the first tick of a session) is judged
    by ``fallback``, today's rule.
    """

    def hot(tick: Tick) -> bool:
        return any(
            (score := tick.watch_score(name)) is not None and score >= enter.get(name, DEFAULT_WATCH_ENTER)
            for name in concepts
        )

    def predicate(window: list[Tick]) -> bool:
        ticks = _recent(window, seconds)
        watched = [tick for tick in ticks if tick.watch is not None]
        if len(watched) < max(1, min_watched):
            return fallback(window)
        if sum(hot(tick) for tick in watched) < WATCH_HOT_FRACTION * len(watched):
            return False
        return any(confirm(tick) is True for tick in ticks)

    return predicate


def keyword_trigger(
    name: str,
    keywords: list[str],
    timings: Timings,
    *,
    min_hits: int = 2,
    window_s: float = 10.0,
    cooldown_s: float,
    reason: str,
    extra_line: str,
    bypass_gap: bool = False,
) -> Trigger:
    """Trigger on fresh, repeated keyword matches in captions or objects."""

    lowered = [(keyword, keyword.casefold()) for keyword in keywords]
    minimum = timings.scaled_hits(min_hits)

    def match(tick: Tick) -> tuple[str, str, str] | None:
        if not tick.ai_fresh(timings.ai_max_age_ms) or tick.ai is None:
            return None
        caption = tick.ai.caption or ""
        objects = tick.ai.objects or []
        fields = [caption, *objects]
        for keyword, folded in lowered:
            if any(folded in field.casefold() for field in fields):
                return keyword, caption, ", ".join(objects)
        return None

    def predicate(window: list[Tick]) -> bool:
        recent = _recent(window, window_s)
        return bool(recent and match(recent[-1])) and sum(match(t) is not None for t in recent) >= minimum

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        for tick in reversed(_recent(window, window_s)):
            hit = match(tick)
            if hit is not None:
                keyword, caption, objects = hit
                return reason.format(kw=keyword), [
                    extra_line.format(caption=caption, objects=objects)
                ]
        return reason, []

    return Trigger(name, predicate, cooldown_s, None, reason, enrich, bypass_gap=bypass_gap)


# -- persona cues ------------------------------------------------------------
#
# The persona scripts a reaction to a handful of things: a rice krispy treat or a
# cucumber in the hand, a coffee milkshake, a phone picked up at the laptop, a
# crowd. Those are *cues*. The wearer expects the reaction in the same breath,
# so a cue fires on ONE fresh tick and goes straight to the voice agent, while
# the clerk wakes beside it only to write the moment down. The two-tick rule
# that `change` keeps was measured costing 1.5-3.5 s per cue, and it failed
# outright on label churn: on demo day the packaged ice cream never got its
# agreeing pair, was first spoken at 10:48:31 off an unrelated "standing ->
# computer_use" wake-up, and was then handed off twice more in eleven seconds.
#
# A cue is a *moment*, not a level. The coffee stays in the hand for thirty
# seconds and must be remarked on once, not every tick, and not again when a
# blind gap hides it for a few ticks. So each cue has a key, the key opens a
# moment on its first positive tick, and the moment stays the same moment while
# positive ticks keep arriving within ``gap_s`` of each other. A moment is
# handed off at most once: the gate spends it only when a hand-off actually
# landed, so a cue that arrives while the wearer is mid-conversation is tried
# again on the next fresh tick instead of being lost -- a fresh look, not a queue.
#
# Unknown is never absence. A tick with no ai, a stale block, ``in_hand`` null
# or ``people_count`` "unknown" says nothing about the cue: only positive ticks
# extend a moment and only the clock ends one.

#: Seconds without a positive fresh tick before a hand-held cue's moment ends.
#: Longer than the 9-12 s blind gaps seen live, so a dropout is not a new moment.
CUE_HAND_GAP_S = 12.0
#: A crowd is a room, not a prop: the head turns to the laptop and the count
#: dips below "6+" for a while without anyone leaving (people were in view on
#: 146 of 153 fresh ticks live). Only a full minute with no crowd ends it.
CUE_CROWD_GAP_S = 60.0

#: Cue kinds in priority order when one tick shows several. The hand beats the
#: room, and caffeine beats food because it is the one the persona asks about.
CUE_KINDS: tuple[str, ...] = ("caffeine", "food", "drink", "phone", "crowd")
#: Kinds that live in the wearer's hand; while one is live the `change` trigger
#: leaves food, drink and object changes to the cue that already has them.
HAND_CUE_KINDS: frozenset[str] = frozenset({"caffeine", "food", "drink", "phone"})

_CAFFEINE_DRINKS = frozenset({"coffee", "tea", "energy_drink"})
_NOT_A_CUE_DRINKS = frozenset({"none", "water", "unknown"})
_ALCOHOL_DRINKS = frozenset({"alcohol", "beer", "wine", "cocktail"})

_WORD = re.compile(r"[a-z0-9]+")
_PHONE_WORDS = frozenset({"phone", "smartphone", "iphone", "cellphone", "android"})
_CAFFEINE_RE = re.compile(
    r"\b(?:coffee|espresso|latte|cappuccino|americano|mocha|cold brew|frappuccino"
    r"|macchiato|matcha|tea|energy drink|red bull|redbull|monster|celsius)\b"
)
_HEALTHY_RE = re.compile(
    r"\b(?:cucumbers?|carrots?|celery|apples?|bananas?|oranges?|grapes|berries"
    r"|fruit|vegetables?|veggies?|salad|broccoli|peppers?|tomato(?:es)?)\b"
)
_FOOD_RE = re.compile(
    r"\b(?:treats?|krisp(?:y|ie|ies)|snacks?|chips|crisps|cookies?|candy|chocolate"
    r"|donuts?|doughnuts?|cake|muffins?|bars?|sandwich|pizza|burger|fries"
    r"|pastry|croissant|bagel|granola|cereal|popcorn|pretzels?)\b"
)
#: Drinks by name. A bare container ("a cup", "a can") is not one of these: on
#: its own it says nothing, and a cup of water must not get a line.
_DRINK_RE = re.compile(
    r"\b(?:milkshake|shake|smoothie|juice|soda|cola|coke|boba|lemonade|kombucha)\b"
)
_CONTAINER_RE = re.compile(r"\b(?:cans?|bottles?|cups?|mugs?|glass|drink|tumbler)\b")
#: Held things that are never food, whatever the frame's food flag says.
_NOT_FOOD_RE = re.compile(
    r"\b(?:pens?|pencils?|markers?|chargers?|cables?|cords?|mouse|keys?|wallet|cards?"
    r"|badges?|lanyard|paper|papers|notebook|book|tablet|ipad|controller|remote"
    r"|headphones|earbuds|airpods|glasses|sticker|laptop|stylus|bags?|backpack"
    r"|purse|umbrella|jacket|hoodie|hat|box|napkins?|tissues?|towel)\b"
)
#: A laptop-class thing in view, for "phone at the laptop". No bare "screen":
#: a held phone's own display is a screen, and Gemini says so ("holding a
#: phone showing its screen"), which made walking across the stage while
#: checking the phone read as sitting at the laptop.
_LAPTOP_RE = re.compile(r"\b(?:laptops?|computers?|monitors?|macbooks?|keyboards?|desktop)\b")
#: Caption words for "the wearer is holding it" on a producer that does not
#: report ``in_hand`` (the sim, old replays). Whole words: "hand" must not match
#: "handle" or "handset".
_HAND_RE = re.compile(r"\b(?:hold(?:s|ing)?|held|hands?)\b")
_PHONE_CAPTION_RE = re.compile(
    r"\b(?:holding|using|looking at|scrolling|checking)\s+(?:a\s+|his\s+|her\s+|their\s+|the\s+)?"
    r"(?:smart)?phone\b|\bphone in (?:the |his |her |their )?hand\b"
)
#: Explicit crowd language only, for a producer that reports no head count.
#: "group of people" is gone: three teammates round a laptop are a group, and
#: the persona's crowd is "a room full of people", not a table.
_CROWD_RE = re.compile(
    r"\b(?:crowd(?:ed|s)?|audience|many people|lots of people"
    r"|full room|busy room|packed room|room full of people)\b"
)
#: Only "6+" is a crowd. At a hackathon "3-5" is true on nearly every tick
#: (people were in view on 146 of 153 fresh ticks live), so counting it fired
#: the crowd line at the start of every session instead of when the wearer
#: actually walks into a full room.
_CROWD_COUNTS = frozenset({"6+"})


@dataclass(frozen=True, slots=True)
class Cue:
    """One persona cue seen on one tick."""

    #: ``caffeine`` / ``food`` / ``drink`` / ``phone`` / ``crowd``.
    kind: str
    #: The moment key: the kind, plus a family for food so a rice krispy then a
    #: cucumber are two moments but ``baked_goods`` <-> ``snack`` relabelling of
    #: one treat is not (consecutive food ticks agreed on food_type 30/43 live).
    key: str
    #: What to call it out loud: ``rice krispies treat``, ``phone``, ``crowd``.
    item: str
    #: Plain-words label for the hand-off: ``junk food``, ``caffeine``, ...
    label: str
    #: The item came from the wearer's own words for the hand (``in_hand``),
    #: not from a frame-wide tag. Only a named item may start a second moment
    #: under the same key (chips straight after the treat): a caption-path
    #: item is just the churning ``food_type`` and must not.
    named: bool = True


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def _screen_in_view(tick: Tick, max_age_ms: int) -> bool:
    """A laptop (or a desk computer) is in the frame, next to the phone.

    Only a laptop-class word or the ``computer_use`` activity counts. The
    ``screen_present`` flag and a bare "screen" are not enough while a phone is
    the thing in the hand: the phone is itself a screen, so both are true of
    someone walking on stage looking at their phone.
    """

    if tick.enum("activity", max_age_ms) == "computer_use":
        return True
    ai = tick.ai
    text = " ".join([(ai.caption or "") if ai else "", *((ai.objects or []) if ai else [])])
    return _LAPTOP_RE.search(text.casefold()) is not None


def _reports_hands(tick: Tick) -> bool:
    """Does this producer report ``in_hand`` at all?

    Person A's tagger always sends the key (null for empty or unseen hands); the
    sim and older replays never do. Only a producer that reports it is trusted
    when it says null -- for the others the caption is all there is.
    """

    ai = tick.ai
    return ai is not None and ("in_hand" in ai.model_fields_set or ai.in_hand is not None)


def _food_label(food_type: str | None, item: str) -> tuple[str, str]:
    """(moment key, label) for a food in the hand.

    Two families, not one per ``food_type``: a treat relabelled ``baked_goods``
    -> ``snack`` -> ``dessert`` between ticks is still one moment, while a
    cucumber after it is a new one.

    The item's own words decide the family, and ``food_type`` only when they
    say nothing. ``food_type`` describes the whole frame and churns (30 of 43
    consecutive food ticks agreed live): letting it lead turned a rice krispy
    treat tagged ``grains`` into "healthy food" under a second key, so the
    same treat got a second line.
    """

    unhealthy = food_type in UNHEALTHY_FOOD_TYPES
    if _FOOD_RE.search(item):
        return "food:treat", "junk food" if unhealthy else "food"
    if _HEALTHY_RE.search(item):
        return "food:healthy", "healthy food"
    if food_type in HEALTHY_FOOD_TYPES:
        return "food:healthy", "healthy food"
    if unhealthy:
        return "food:treat", "junk food"
    return "food:treat", "food"


def _held_cue(tick: Tick, item: str, max_age_ms: int) -> Cue | None:
    """Classify a held item. The item's own words lead; the frame's tags
    (which describe the whole frame, not just the hand) only break ties."""

    text = item.casefold()
    words = _words(text)
    if words & _PHONE_WORDS:
        return (Cue("phone", "phone", "phone", "phone at the laptop")
                if _screen_in_view(tick, max_age_ms) else None)
    if _CAFFEINE_RE.search(text):
        return Cue("caffeine", "caffeine", item, "caffeine")
    looks_like_food = bool(_FOOD_RE.search(text) or _HEALTHY_RE.search(text))
    named_drink = bool(_DRINK_RE.search(text))
    container = bool(_CONTAINER_RE.search(text))
    if not (looks_like_food or named_drink or container) and _NOT_FOOD_RE.search(text):
        # A pen in the hand with lunch on the desk: the food flag is about the
        # desk. Only the item's own name can make the hand a cue.
        return None
    drink = tick.enum("drink", max_age_ms)
    food_type = tick.enum("food_type", max_age_ms)
    if (drink in _ALCOHOL_DRINKS or tick.flag("alcohol_visible", max_age_ms) is True) \
            and not looks_like_food:
        return None  # not a persona cue: `alcohol_seen` fires on one tick and asks
    if "water" in words and not looks_like_food:
        return None  # water is not a cue in any persona, whatever else is on the desk
    # The drink tag names *the* visible drink, so a caffeinated tag makes the
    # held drink caffeine ("milkshake" + drink=coffee). The bare flag may be a
    # mug on the desk, so it only claims a container, not a named smoothie.
    if not looks_like_food and (
        drink in _CAFFEINE_DRINKS
        or (tick.flag("caffeine_visible", max_age_ms) is True and not named_drink)
    ):
        return Cue("caffeine", "caffeine", item, "caffeine")
    # Only the item's own words make the hand a food cue. The food flags are
    # about the whole frame: with a cucumber put down on the desk, a shaker or
    # the HackRice sign in the hand used to become "healthy food". The one
    # exception is the wearer visibly eating something the regexes do not
    # know (a burrito), where the frame's food and the hand agree.
    food = tick.flag("food_present", max_age_ms) is True or food_type not in (None, "none")
    eating = tick.enum("activity", max_age_ms) == "eating"
    if looks_like_food or (eating and food and not (named_drink or container)):
        key, label = _food_label(food_type, text)
        return Cue("food", key, item, label)
    if named_drink or (container and drink is not None and drink not in _NOT_A_CUE_DRINKS):
        return Cue("drink", "drink", item, "drink")
    return None


def _caption_hand_cue(tick: Tick, max_age_ms: int) -> Cue | None:
    """The old heuristic, for producers without ``in_hand``: a hand word in the
    caption (or a hand among the objects) together with a food or drink tag."""

    ai = tick.ai
    if ai is None:
        return None
    caption = (ai.caption or "").casefold()
    objects = [str(o).casefold() for o in (ai.objects or [])]
    if not (_HAND_RE.search(caption) or any(o in ("hand", "hands") for o in objects)):
        return None
    drink = tick.enum("drink", max_age_ms)
    food_type = tick.enum("food_type", max_age_ms)
    names_food = bool(_FOOD_RE.search(caption) or _HEALTHY_RE.search(caption))
    if _CAFFEINE_RE.search(caption) or not names_food and (
        tick.flag("caffeine_visible", max_age_ms) is True or drink in _CAFFEINE_DRINKS
    ):
        return Cue("caffeine", "caffeine", (drink or "caffeine").replace("_", " "), "caffeine",
                   named=False)
    if tick.flag("food_present", max_age_ms) is True or food_type not in (None, "none"):
        item = (food_type or "food").replace("_", " ")
        key, label = _food_label(food_type, caption)
        return Cue("food", key, item, label, named=False)
    if drink is not None and drink not in _NOT_A_CUE_DRINKS | _ALCOHOL_DRINKS:
        return Cue("drink", "drink", drink.replace("_", " "), "drink", named=False)
    return None


def _phone_cue(tick: Tick, max_age_ms: int) -> Cue | None:
    """Phone in the hand while a laptop or screen is in view."""

    phone = tick.flag("phone_in_hand", max_age_ms)
    if phone is None:
        # Not reported (or hands unseen): fall back to what the tagger was
        # told to say for a held phone, then to the caption.
        caption = (tick.ai.caption or "").casefold() if tick.ai else ""
        phone = (tick.enum("activity", max_age_ms) == "phone_use"
                 or _PHONE_CAPTION_RE.search(caption) is not None) or None
    if phone is not True or not _screen_in_view(tick, max_age_ms):
        return None
    return Cue("phone", "phone", "phone", "phone at the laptop")


def _crowd_cue(tick: Tick, max_age_ms: int) -> Cue | None:
    count = tick.enum("people_count", max_age_ms)
    if count in _CROWD_COUNTS:
        return Cue("crowd", "crowd", "crowd", f"{count} people")
    ai = tick.ai
    text = " ".join([(ai.caption or "") if ai else "", *((ai.objects or []) if ai else [])])
    named = _CROWD_RE.search(text.casefold()) is not None
    if count is None:
        # "unknown" came back as None: only explicit crowd language counts.
        return Cue("crowd", "crowd", "crowd", "count not reported") if named else None
    if count == "3-5" and named:
        # On a small, distant frame of an audience Gemini can only count a
        # few faces and says "3-5"; the caption still says "audience". A
        # 3-person table never gets crowd language ("group of people" is out
        # of _CROWD_RE), so this does not bring the table false positive back.
        return Cue("crowd", "crowd", "crowd", "3-5 counted, crowd in caption")
    # Any other real bucket ("0", "1-2", or "3-5" with no crowd words) is an
    # answer: not a crowd.
    return None


def tick_cues(tick: Tick, max_age_ms: int = 3000) -> list[Cue]:
    """Every persona cue on one tick, highest priority first. ``[]`` when the
    tick carries no fresh ai -- an unknown tick shows no cue, and says nothing
    about whether one is still there."""

    if not tick.ai_fresh(max_age_ms) or tick.ai is None:
        return []
    found: list[Cue] = []
    held = tick.held(max_age_ms)
    if held is not None:
        cue = _held_cue(tick, held, max_age_ms)
        if cue is not None:
            found.append(cue)
    elif not _reports_hands(tick):
        cue = _caption_hand_cue(tick, max_age_ms)
        if cue is not None:
            found.append(cue)
    if not any(c.kind == "phone" for c in found):
        cue = _phone_cue(tick, max_age_ms)
        if cue is not None:
            found.append(cue)
    cue = _crowd_cue(tick, max_age_ms)
    if cue is not None:
        found.append(cue)
    found.sort(key=lambda c: CUE_KINDS.index(c.kind))
    return found


def _item_tokens(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _words(text)}


def same_item(a: str, b: str) -> bool:
    """Do two held-item phrases name the same thing?

    Gemini spells one treat three ways in a minute ("rice krispies treat",
    "rice krispie treat", "krispie treat"), so this is token overlap after a
    crude singular, not string equality. An empty side cannot be told apart
    and counts as the same.
    """

    left, right = _item_tokens(a), _item_tokens(b)
    if not left or not right:
        return True
    return left <= right or right <= left or len(left & right) / len(left | right) >= 0.5


#: Words that put a hand-off on a cue kind even when it does not name the item
#: the way T0 did: the clerk calls the rice krispies treat "a snack" or "junk
#: food", the crowd "a lot of people".
_KIND_TOPIC_RE: dict[str, tuple[re.Pattern[str], ...]] = {
    "food": (_FOOD_RE, _HEALTHY_RE,
             re.compile(r"\b(?:food|junk|sugar(?:y)?|eat(?:s|ing)?|meal)\b")),
    "caffeine": (_CAFFEINE_RE, re.compile(r"\bcaffeine\b")),
    "drink": (_DRINK_RE, re.compile(r"\bdrinks?\b")),
    "phone": (re.compile(r"\b(?:" + "|".join(sorted(_PHONE_WORDS)) + r")s?\b"),),
    "crowd": (_CROWD_RE, re.compile(r"\b(?:crowd|people|audience)\b")),
}


def topic_about_cue(topic: str, cue_key: str | None, item: str) -> bool:
    """Is a hand-off with this topic about the cue its escalation was stamped
    with? It names the item (``same_item``-style token overlap on the item's
    distinctive words), or it names the cue's kind in other words.

    The stamp says what was in *view*, not what the clerk is talking about:
    the crowd is in view on nearly every tick of the hackathon room, and a
    screen-time nudge stamped ``crowd`` is not a second crowd line.
    """

    if not cue_key:
        return False
    text = topic.casefold()
    topic_tokens = _item_tokens(text)
    want = _item_tokens(item)
    distinctive = want - _GENERIC_ITEM_WORDS
    if (distinctive or want) & topic_tokens:
        return True
    kind = cue_key.split(":", 1)[0]
    return any(rx.search(text) for rx in _KIND_TOPIC_RE.get(kind, ()))


#: Words too generic to say which item a hand-off is about ("your cup", "the
#: bag"), and the words ``cue_topic`` wraps every item in.
_GENERIC_ITEM_WORDS = frozenset({
    "a", "an", "the", "of", "and", "with", "in", "hand", "cup", "can", "bottle",
    "glass", "mug", "bag", "box", "piece", "some",
})


@dataclass(slots=True)
class _Moment:
    first_t: float
    last_t: float
    fired: bool = False
    #: What the moment is about, in T0's words when it opened.
    item: str = ""
    #: A different named item seen on the previous positive tick, waiting for
    #: a second tick before it may replace this moment (see ``observe``).
    rival: str = ""


class CueMoments:
    """Which cue moments are live, and which were already handed off.

    Shared by the ``cue`` trigger (which opens and spends moments) and the
    ``change`` trigger (which stays out of the hand while one is live).
    """

    def __init__(self, hand_gap_s: float = CUE_HAND_GAP_S,
                 crowd_gap_s: float = CUE_CROWD_GAP_S) -> None:
        self.hand_gap_s = hand_gap_s
        self.crowd_gap_s = crowd_gap_s
        self._moments: dict[str, _Moment] = {}

    def _gap(self, key: str) -> float:
        return self.crowd_gap_s if key == "crowd" else self.hand_gap_s

    def observe(self, cues: list[Cue], t: float) -> None:
        """Extend or open the moment for every cue on a fresh tick. Idempotent
        for the same tick, so a predicate may be evaluated twice.

        A different thing under the same key is a new moment: a bag of chips
        straight after the rice krispy treat (both ``food:treat``), or tea
        after coffee, never has the 12 s gap between positive ticks that would
        end the first moment, so keyed on the key alone the chips were never
        spoken. It must be a *named* item (``in_hand``, not the churning
        ``food_type``), clearly not the same thing (``same_item``), and seen on
        two positive ticks in a row: Gemini spells one treat three ways in a
        minute, and one odd spelling must not buy the same treat a second line.
        """

        for cue in cues:
            moment = self._moments.get(cue.key)
            if moment is not None and 0 <= t - moment.last_t <= self._gap(cue.key):
                if t > moment.last_t and self._new_item(moment, cue):
                    if moment.rival and same_item(cue.item, moment.rival):
                        self._moments[cue.key] = _Moment(first_t=t, last_t=t, item=cue.item)
                        continue
                    moment.rival = cue.item
                elif t > moment.last_t:
                    moment.rival = ""
                moment.last_t = max(moment.last_t, t)
                continue
            if moment is not None and t < moment.last_t:
                continue  # out-of-order tick: never reopen from the past
            self._moments[cue.key] = _Moment(first_t=t, last_t=t, item=cue.item)

    @staticmethod
    def _new_item(moment: _Moment, cue: Cue) -> bool:
        return (cue.named and bool(moment.item) and bool(cue.item)
                and not same_item(cue.item, moment.item))

    def ready(self, cues: list[Cue]) -> Cue | None:
        """The highest-priority cue whose moment has not been handed off yet."""

        return next((c for c in cues if not self._moments.get(c.key, _Moment(0, 0)).fired), None)

    def spend(self, key: str) -> None:
        moment = self._moments.get(key)
        if moment is not None:
            moment.fired = True

    def live_spent(self, t: float) -> tuple[str, str] | None:
        """``(key, item)`` of the newest hand-held moment that was already
        handed off and is still live at ``t``, or ``None``.

        What the gate stamps on a clerk wake-up whose own tick cannot see the
        hands (``in_hand`` null is unknown, not "put down"): the treat that was
        just spoken is still the treat, so the clerk must not hand it off again.
        """

        live = [
            (m.last_t, key, m.item) for key, m in self._moments.items()
            if m.fired and key.split(":", 1)[0] in HAND_CUE_KINDS
            and 0 <= t - m.last_t <= self._gap(key)
        ]
        if not live:
            return None
        _, key, item = max(live)
        return key, item

    def holding(self, t: float) -> bool:
        """Is a hand-held cue moment live at ``t`` (fired or still pending)?"""

        return any(
            key.split(":", 1)[0] in HAND_CUE_KINDS and 0 <= t - m.last_t <= self._gap(key)
            for key, m in self._moments.items()
        )

    def reset(self) -> None:
        self._moments.clear()


def cue_topic(cue: Cue, tick: Tick, max_age_ms: int = 3000) -> str:
    """The hand-off the voice agent gets, written from T0's own words.

    Item and label first, because that is what the persona reacts to; the
    caption and objects after, because they are the only other thing the
    tagger said about this exact frame.
    """

    if cue.kind == "phone":
        head = "phone in hand at the laptop"
    elif cue.kind == "crowd":
        head = f"crowd in view ({cue.label})"
    else:
        head = f"{cue.item} in hand ({cue.label})"
    ai = tick.ai if tick.ai_fresh(max_age_ms) else None
    seen: list[str] = []
    if ai is not None and ai.caption:
        seen.append(ai.caption.strip())
    if ai is not None and ai.objects:
        seen.append("objects: " + ", ".join(ai.objects[:5]))
    topic = head + (" -- T0 saw: " + "; ".join(seen) if seen else "")
    return topic[:220]


def cue_trigger(timings: Timings, moments: CueMoments | None = None, *,
                bypass_gap: bool = True) -> Trigger:
    """Persona cues on ONE fresh tick, each moment handed off once.

    ``bypass_gap``: a cue is exempt from the global escalation gap, because it
    goes to the voice agent rather than into the clerk's single slot, and a
    scene flicker two seconds earlier is no reason to answer a coffee late.
    ``default_triggers`` turns the exemption off outside demo mode, where the
    60 s gap is the whole point of the production preset.
    No blanket cooldown either (``cooldown_s=0``): the moment is the dedupe, so
    a cucumber straight after a rice krispy treat is not held behind it.
    """

    max_age_ms = timings.ai_max_age_ms
    state = moments if moments is not None else CueMoments()
    #: The cue the predicate picked on this tick, for enrich/cue/on_fired, which
    #: the gate calls synchronously on the same window straight after.
    picked: dict[str, tuple[Cue, Tick] | None] = {"cue": None}

    def evaluate(window: list[Tick]) -> tuple[Cue, Tick] | None:
        if not window:
            return None
        tick = window[-1]
        # The cue must be on the tick that just arrived: a fresh block, now.
        cues = tick_cues(tick, max_age_ms)
        state.observe(cues, tick.t)
        cue = state.ready(cues)
        return None if cue is None else (cue, tick)

    def predicate(window: list[Tick]) -> bool:
        picked["cue"] = evaluate(window)
        return picked["cue"] is not None

    def current(window: list[Tick]) -> tuple[Cue, Tick] | None:
        hit = picked["cue"]
        if hit is None or not window or hit[1].tick_id != window[-1].tick_id:
            hit = picked["cue"] = evaluate(window)
        return hit

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        hit = current(window)
        if hit is None:
            return "Persona cue", []
        cue, tick = hit
        line = f"Persona cue ({cue.kind}): {cue_topic(cue, tick, max_age_ms)}"
        return f"{cue.label}: {cue.item}", [line]

    def describe(window: list[Tick]) -> tuple[str, str, str, str] | None:
        hit = current(window)
        if hit is None:
            return None
        cue, tick = hit
        # The persona asks about caffeine ("is that your first one today?")
        # and states everything else.
        mode = "question" if cue.kind == "caffeine" else "statement"
        return cue.key, cue.item, cue_topic(cue, tick, max_age_ms), mode

    def on_fired(t: float) -> None:
        hit = picked["cue"]
        if hit is not None:
            state.spend(hit[0].key)

    return Trigger(
        "cue", predicate, 0.0, None, "Persona cue", enrich,
        on_fired=on_fired, bypass_gap=bypass_gap, cue=describe, reset=state.reset,
        moments=state,
    )


#: Scene families. The tagger flips between indoor labels on one unchanged desk
#: (live wake-ups: "scene office -> indoor_other", "office -> classroom",
#: "classroom -> indoor_other", even "indoor_other -> home", all in the same
#: hackathon room), so a scene change is a change of *family*: going outside,
#: getting in a car, walking into a gym or a sauna. Anything not listed is
#: "indoor".
_SCENE_FAMILIES: dict[str, str] = {
    **{scene: "outdoor" for scene in OUTDOOR_SCENES},
    "car": "transit", "public_transit": "transit", "airport": "transit",
    "gym": "gym",
    "sauna": "recovery", "cold_plunge": "recovery",
}

#: Activity families, for the same reason: seated <-> computer_use <-> standing
#: <-> reading at one desk is posture noise, not an event. ``phone_use`` sits in
#: the desk family because a phone at the laptop is the ``cue`` trigger's job.
_ACTIVITY_FAMILIES: dict[str, str] = {
    **{a: "desk" for a in ("seated", "standing", "computer_use", "reading",
                           "phone_use", "talking", "other")},
    **{a: "meal" for a in ("eating", "drinking", "cooking")},
    **{a: "on_foot" for a in ("walking", "commuting", "shopping")},
    **{a: "exercise" for a in ("running", "cycling", "exercising",
                               "lifting_weights", "stretching")},
    "driving": "driving",
    "lying_down": "rest", "sleeping": "rest",
    "cleaning": "chores", "personal_care": "chores",
}


def _family(name: str, label: str) -> str:
    if name == "scene":
        return _SCENE_FAMILIES.get(label, "indoor")
    return _ACTIVITY_FAMILIES.get(label, label)


def change_trigger(timings: Timings, cues: CueMoments | None = None, *,
                   novelty_enter: float | None = None) -> Trigger:
    """Wake T1 for stable, meaningful changes in fresh visual semantics.

    Two agreeing fresh ticks, as before -- for the things where the risk is
    talking about flicker rather than being late. With ``cues`` (the shared
    moments of the ``cue`` trigger), anything in the wearer's hand is left to
    the cue: while a hand-held cue is live this trigger reports no food, drink
    or new-object change, so the clerk is not woken a second time, 1.5 s later,
    about the rice krispy treat the voice agent is already talking about.

    ``novelty_enter`` (phase 3, ``reads_watch``) adds the watcher's embedding
    novelty as a second input: ``watch.novelty`` at or above it on two
    consecutive ticks is a change too, under the same cooldown and per-minute
    cap, and needs no fresh ``ai`` -- the watcher saw the scene turn before
    the labeler was asked. ``None`` (the default) leaves the trigger as it was.
    """

    required = max(2, timings.scaled_hits(2))
    fired_at: deque[float] = deque()
    unknown = {None, "", "?", "unknown"}

    def fresh(window: list[Tick], seconds: float) -> list[Tick]:
        return [
            tick for tick in _recent(window, seconds)
            if tick.ai_fresh(timings.ai_max_age_ms) and tick.ai is not None
        ]

    def value(tick: Tick, name: str) -> str | None:
        raw = getattr(tick.ai, name, None) if tick.ai is not None else None
        return None if raw in unknown else str(raw)

    def changes(window: list[Tick]) -> tuple[list[str], list[str]]:
        recent = fresh(window, 20.0)
        if len(recent) < required + 1:
            return [], []
        newest = recent[-required:]
        before12 = [t for t in recent[:-required] if t.t >= recent[-1].t - 12.0]
        reasons: list[str] = []
        transitions: list[str] = []

        for name in ("scene", "activity"):
            afters = [value(t, name) for t in newest]
            prior = next((value(t, name) for t in reversed(before12) if value(t, name)), None)
            if (prior and afters[0] and len(set(afters)) == 1
                    and _family(name, prior) != _family(name, afters[0])):
                reasons.append(f"{name} {prior} -> {afters[0]}")
                transitions.append(f"{name}: {prior} -> {afters[0]}")

        if cues is not None and cues.holding(recent[-1].t):
            # The hand belongs to the cue trigger right now (see docstring).
            return reasons, transitions

        for name, label in (("drink", "drink"), ("food_type", "food")):
            afters = [value(t, name) for t in newest]
            raw_prior = next((value(t, name) for t in reversed(before12) if value(t, name)), None)
            valid_prior = raw_prior == "none" if name == "drink" else raw_prior is not None
            if afters[0] and len(set(afters)) == 1 and afters[0] != "none" and valid_prior and raw_prior != afters[0]:
                reasons.append(f"{label}: {afters[0]}")
                transitions.append(f"{name}: {raw_prior} -> {afters[0]}")

        current_objects = [set(t.ai.objects or []) for t in newest]  # type: ignore[union-attr]
        stable_objects = set.intersection(*current_objects) if current_objects else set()
        old_objects = {
            obj.casefold()
            for t in recent[:-required]
            for obj in (t.ai.objects or [])  # type: ignore[union-attr]
        }
        for obj in sorted(stable_objects, key=str.casefold):
            if obj.casefold() not in old_objects:
                reasons.append(f"new object: {obj}")
                transitions.append(f"objects: absent -> {obj}")

        if cues is not None:
            # Food or drink in the hand is a persona cue now, fired on one tick
            # by `cue_trigger`; reporting it here too would wake the clerk twice.
            return reasons, transitions

        def in_hand(tick: Tick, kind: str) -> bool:
            caption = tick.ai.caption.casefold() if tick.ai and tick.ai.caption else ""
            hand = "holding" in caption or "hand" in caption
            if kind == "food":
                return hand and tick.flag("food_present", timings.ai_max_age_ms) is True
            return hand and value(tick, "drink") not in unknown | {"none"}

        for kind in ("food", "drink"):
            if all(in_hand(t, kind) for t in newest) and not any(in_hand(t, kind) for t in before12):
                reasons.append(f"{kind} in hand")
                transitions.append(f"{kind} in hand: no -> yes")
        return reasons, transitions

    def within_limits(now: float) -> bool:
        while fired_at and fired_at[0] <= now - 60.0:
            fired_at.popleft()
        return (not fired_at or now - fired_at[-1] >= timings.change_cooldown_s) and len(fired_at) < timings.change_max_per_min

    def novelty(window: list[Tick]) -> tuple[float, float] | None:
        """The two newest ticks' novelty when both are at or above enter."""

        if novelty_enter is None or len(window) < 2:
            return None
        before, now = window[-2], window[-1]
        if before.watch is None or now.watch is None:
            return None
        if before.watch.novelty >= novelty_enter and now.watch.novelty >= novelty_enter:
            return before.watch.novelty, now.watch.novelty
        return None

    def predicate(window: list[Tick]) -> bool:
        if not window or not within_limits(window[-1].t):
            return False
        if window[-1].ai_fresh(timings.ai_max_age_ms) and changes(window)[0]:
            return True
        return novelty(window) is not None

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        reasons, transitions = changes(window)
        spike = novelty(window)
        if spike is not None:
            reasons.append(f"novelty {spike[1]:.2f}")
            transitions.append(f"novelty: {spike[0]:.2f} -> {spike[1]:.2f}")
        return "; ".join(reasons), ["Visual transition: " + "; ".join(transitions)] if transitions else []

    return Trigger(
        "change", predicate, timings.change_cooldown_s, None, "Meaningful visual change",
        enrich, on_fired=fired_at.append, reset=fired_at.clear,
    )


def _outdoor_seen(tick: Tick, max_age_ms: int) -> bool | None:
    """Tri-state: an outdoor scene or vegetation on this tick's fresh ``ai``."""

    scene = tick.enum("scene", max_age_ms)
    vegetation = tick.flag("vegetation_visible", max_age_ms)
    if scene is None and vegetation is None:
        return None
    return scene in OUTDOOR_SCENES or vegetation is True


def _outdoor_hits(
    seconds: float, minimum: int, max_age_ms: int = 3000
) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        observations: list[bool] = []
        for tick in _recent(window, seconds):
            seen = _outdoor_seen(tick, max_age_ms)
            if seen is None:
                continue
            observations.append(seen)
        return bool(observations) and observations[-1] and sum(observations) >= minimum

    return predicate


def _stillness(seconds: float) -> Callable[[list[Tick]], bool]:
    def predicate(window: list[Tick]) -> bool:
        ticks = _recent(window, seconds)
        if len(ticks) < 2 or ticks[-1].t - ticks[0].t < seconds:
            return False
        measured = [tick.sensor.frame_delta for tick in ticks if tick.sensor.frame_delta is not None]
        return bool(measured) and sum(value < 0.03 for value in measured) / len(measured) >= 0.9

    return predicate


def _hr_stats(
    series: list[tuple[float, float]], rest: float, ratio: float
) -> tuple[float, float, int]:
    """Peak bpm, elevated fraction, and sample count for one series."""

    if not series:
        return 0.0, 0.0, 0
    threshold = rest * ratio
    elevated = sum(1 for _, bpm in series if bpm > threshold)
    return max(bpm for _, bpm in series), elevated / len(series), len(series)


def _subsample(series: list[tuple[float, float]], k: int) -> list[tuple[float, float]]:
    """Evenly spaced ``k`` points, always keeping the first and the last."""

    if len(series) <= k or k <= 1:
        return list(series)
    step = (len(series) - 1) / (k - 1)
    picked = {min(len(series) - 1, round(i * step)) for i in range(k)}
    return [series[i] for i in sorted(picked)]


def hr_context_line(
    series: list[tuple[float, float]], rest: float, origin: float, window_s: float
) -> str:
    """The one extra envelope line carrying the HR series (SPEC §14.3).

    T1 is asked what was happening, not whether HR was high, so the numbers go
    in as a compact series with offsets on the same ``t-Ns`` scale as the tick
    table and the frame labels.
    """

    points = ", ".join(
        f"t-{max(0, int(round(origin - t)))}s {bpm:.0f}"
        for t, bpm in _subsample(sorted(series), _HR_LINE_POINTS)
    )
    return (
        f"Heart rate (wearable, bpm) over the last {window_s:.0f}s, "
        f"resting {rest:.0f}: {points}"
    )


#: A sample older than this is not "now" any more and is left out of the line.
_NOW_WINDOW_S = 30 * 60

#: Steps are a rate, not a level, so they are summed over a short trailing window.
_STEPS_WINDOW_S = 10 * 60


def _fmt_hr(v: float) -> str:
    return f"HR {v:.0f} bpm"


def _fmt_hrv(v: float) -> str:
    return f"HRV {v:.0f} ms"


def _fmt_spo2(v: float) -> str:
    return f"SpO2 {v:.0f}%"


def _fmt_rr(v: float) -> str:
    return f"RR {v:.0f}"


def _fmt_temp(v: float) -> str:
    return f"wrist temp {v:+.1f}\u00b0C"


def _fmt_strain(v: float) -> str:
    return f"strain {v:.1f}"


#: Rendered in this order; anything the feed has no recent sample for is simply
#: skipped, so a wearer with only a watch gets a shorter line, not a line of
#: em-dashes.
_NOW_FIELDS: tuple[tuple[str, Callable[[float], str]], ...] = (
    ("heart_rate", _fmt_hr),
    ("hrv_rmssd", _fmt_hrv),
    ("spo2", _fmt_spo2),
    ("respiratory_rate", _fmt_rr),
    ("wrist_temp_dev", _fmt_temp),
    ("strain", _fmt_strain),
)


def wearable_now_line(feed: BiometricFeed, t: float) -> str | None:
    """One line of "what the wearable says right now", for any escalation.

    Every T1 call gets this, not just ``biometric_anomaly``: the frames show
    what the wearer was looking at and the tick table shows what the phone
    measured, but only the wearable can say whether the body was calm while it
    happened. That context is as useful on a ``food_in_frame`` as on an HR
    spike, and it costs one row read per metric.

    Returns ``None`` when nothing recent is available -- the caller attaches
    nothing rather than a line saying there is nothing.
    """

    parts: list[str] = []
    devices: list[str] = []
    source_of = getattr(feed, "latest_source", None)

    def note(metric: str) -> None:
        if not callable(source_of):
            return
        try:
            device = source_of(metric)
        except Exception:  # pragma: no cover - defensive
            return
        if device and device not in devices:
            devices.append(device)

    def recent(metric: str) -> float | None:
        """The newest value at or before ``t``, within the freshness window.

        The window read comes first and ``latest`` is only the fallback,
        because ``t`` is the *tick* clock: under ``--source sim`` the seeded
        day stretches hours past the current tick, so "the newest row in the
        table" is usually in the future and says nothing about now.
        """

        try:
            window = feed.series(metric, t - _NOW_WINDOW_S, t)
        except Exception:  # pragma: no cover - a feed read never breaks a tick
            window = []
        if window:
            return float(window[-1][1])
        try:
            latest = feed.latest(metric)
        except Exception:  # pragma: no cover - defensive
            return None
        if latest is None or abs(latest[0] - t) > _NOW_WINDOW_S:
            return None
        return float(latest[1])

    for metric, render in _NOW_FIELDS:
        value = recent(metric)
        if value is None:
            continue
        parts.append(render(value))
        note(metric)

    try:
        steps = feed.series("steps_delta", t - _STEPS_WINDOW_S, t)
    except Exception:  # pragma: no cover - defensive
        steps = []
    if steps:
        parts.append(f"steps last 10 min {sum(v for _, v in steps):.0f}")
        note("steps_delta")

    if not parts:
        return None
    who = f" ({'/'.join(devices)})" if devices else ""
    return f"Wearable now{who}: " + ", ".join(parts)


def biometric_anomaly_trigger(timings: Timings, feed: BiometricFeed) -> Trigger:
    """HR above ``resting x ratio`` for a sustained window while not exerting.

    The only trigger that fires on something the camera cannot see, so it is
    also the only one that carries an extra text line into the envelope: the
    wearable supplies the number, the frames supply the cause (SPEC §14.3).
    """

    window_s = timings.biometric_window
    ratio = timings.biometric_hr_ratio
    max_age_ms = timings.ai_max_age_ms

    def _exerting(window: list[Tick], t0: float) -> bool:
        # Unknown activity counts neither way; a window with no known activity
        # at all still fires -- the wearable is saying something is up.
        return any(
            tick.enum("activity", max_age_ms) in EXERTION_ACTIVITIES
            for tick in window
            if tick.t >= t0
        )

    def predicate(window: list[Tick]) -> bool:
        if not window:
            return False
        t1 = window[-1].t
        t0 = t1 - window_s
        series = feed.hr_series(t0, t1)
        if len(series) < _MIN_SAMPLES:
            return False
        stamps = [t for t, _ in series]
        if max(stamps) - min(stamps) < _MIN_SPAN_FRACTION * window_s:
            return False
        _, elevated, _ = _hr_stats(series, feed.resting_hr(), ratio)
        if elevated < _MIN_ELEVATED_FRACTION:
            return False
        return not _exerting(window, t0)

    def enrich(window: list[Tick]) -> tuple[str, list[str]]:
        if not window:
            return _BIOMETRIC_REASON, []
        t1 = window[-1].t
        series = feed.hr_series(t1 - window_s, t1)
        rest = feed.resting_hr()
        peak, _, count = _hr_stats(series, rest, ratio)
        if not count:
            return "", []
        return (
            _BIOMETRIC_REASON.format(hr=peak, rest=rest),
            [hr_context_line(series, rest, t1, window_s)],
        )

    return Trigger(
        "biometric_anomaly",
        predicate,
        timings.biometric_cooldown,
        None,
        _BIOMETRIC_REASON,
        enrich,
    )


def default_triggers(
    timings: Timings, demo_mode: bool, feed: BiometricFeed | None = None,
    keyword_triggers: list[dict] | None = None, *, cues: bool = True,
    cue_bypass_gap: bool = True, reads_watch: bool = False,
    watch_thresholds: Mapping[str, tuple[float, float]] | None = None,
    novelty_enter: float = 0.35,
) -> list[Trigger]:
    """Return shipped triggers in deterministic priority order.

    ``cues=False`` is the CUE_TRIGGER=0 kill switch: no one-tick ``cue``
    trigger, and ``change`` gets no moments, so it goes back to reporting food,
    drink and objects in the hand itself -- the pre-fast-path trigger set.

    ``cue_bypass_gap=False`` is what FAST_PATH=0 passes: the cue then goes
    into the clerk's queue, so it must respect the global escalation gap like
    any other trigger rather than take the one T1 slot 0 s after another.

    ``reads_watch=True`` is GATE_READS_WATCH=1 (docs/PERCEPTION.md "Gate and
    actions", phase 3): the four sustained triggers take persistence from the
    tick's ``watch`` block (``watch_thresholds`` gives each concept's
    ``(enter, exit)``; only enter is read here) and keep one fresh confirming
    ``ai`` reading as the "what", and ``change`` also fires on watcher novelty
    at or above ``novelty_enter`` on two consecutive ticks. Point sightings,
    keyword and cue triggers do not change. Off, every trigger is exactly what
    it was.
    """

    # Kept as a named mapping so deployments can trivially override individual
    # entries while every unspecified trigger uses the configured fallback.
    cooldowns: dict[str, float] = {"medication_seen": MEDICATION_COOLDOWN_S}
    cooldown = lambda name: cooldowns.get(name, timings.trigger_cooldown_default)
    # Every `*_min_hits` on Timings is a 1 Hz reference count; the stream runs
    # at `timings.tick_interval_s`, so a window holds fewer ticks than seconds
    # and the raw counts must be divided down or they become unreachable.
    hits = timings.scaled_hits
    max_age_ms = timings.ai_max_age_ms
    #: Sightings are point observations, not sustained states: two hits at
    #: 1 Hz, and at 1.5 s that floors to one, which is the intent -- a cup seen
    #: once in a 10 s window is a cup. The window itself does not scale.
    sighting_hits = max(1, hits(2))
    def meal(tick: Tick) -> bool | None:
        activity = tick.enum("activity", max_age_ms)
        food = tick.flag("food_present", max_age_ms)
        if activity == "eating":
            return True
        if food is None:
            return None
        caption = tick.ai.caption.casefold() if tick.ai and tick.ai.caption else ""
        return food and re.search(r"\b(?:eat(?:s|ing)?|bit(?:e|ing)|chew(?:s|ing)?)\b", caption) is not None

    def conversation(tick: Tick) -> bool | None:
        people = tick.flag("people_present", max_age_ms)
        if people is None:
            return None
        return people and (
            tick.flag("people_interacting", max_age_ms) is True
            or tick.enum("activity", max_age_ms) == "talking"
        )

    def medication(tick: Tick) -> bool | None:
        return tick.medication_in_view(max_age_ms)

    food_in_frame = _condition_hits(meal, timings.food_window, hits(timings.food_min_hits))
    screen_sustained = _flag_hits("screen_present", timings.screen_sustained_window, hits(timings.screen_sustained_min_hits), max_age_ms)
    people_sustained = _condition_hits(conversation, timings.people_sustained_window, hits(timings.people_sustained_min_hits))
    outdoor_sustained = _outdoor_hits(timings.outdoor_sustained_window, hits(timings.outdoor_min_hits), max_age_ms)
    if reads_watch:
        # The watcher proves how long; the labeler's own per-tick reading (the
        # one the trigger reads today) confirms what, once in the window.
        enter = {name: pair[0] for name, pair in (watch_thresholds or {}).items()}
        food_in_frame = _watch_persisted(
            ("food_present",), enter, meal, food_in_frame, timings.food_window,
            min_watched=hits(timings.food_min_hits))
        screen_sustained = _watch_persisted(
            ("screen_present",), enter, lambda tick: tick.flag("screen_present", max_age_ms),
            screen_sustained, timings.screen_sustained_window,
            min_watched=hits(timings.screen_sustained_min_hits))
        people_sustained = _watch_persisted(
            ("people_present", "people_interacting"), enter, conversation,
            people_sustained, timings.people_sustained_window,
            min_watched=hits(timings.people_sustained_min_hits))
        outdoor_sustained = _watch_persisted(
            ("outdoor_visible", "vegetation_visible"), enter,
            lambda tick: _outdoor_seen(tick, max_age_ms),
            outdoor_sustained, timings.outdoor_sustained_window,
            min_watched=hits(timings.outdoor_min_hits))

    specs = [
        ("food_in_frame", food_in_frame, "meal", "Eating persisted in the recent frame window"),
        ("screen_sustained", screen_sustained, "screen_block", "Screen presence was sustained"),
        ("people_sustained", people_sustained, "conversation", "Social interaction was sustained"),
        ("outdoor_sustained", outdoor_sustained, "outdoor_block", "Outdoor context was sustained"),
        ("caffeine_seen", _flag_hits("caffeine_visible", 10.0, sighting_hits, max_age_ms), "caffeine_sighting", "Caffeine was seen repeatedly"),
        ("alcohol_seen", _flag_hits("alcohol_visible", 10.0, sighting_hits, max_age_ms), "alcohol_sighting", "Alcohol was seen repeatedly"),
        ("medication_seen", _condition_hits(medication, 10.0, sighting_hits), "medication_sighting", "Medication was seen repeatedly"),
        ("stillness", _stillness(timings.stillness_window), None, "Low frame motion was sustained"),
    ]
    # The cue trigger runs first and, in the demo, skips the global gap: a
    # coffee in the hand is answered on the tick it appears, whatever else woke
    # the clerk. It shares its moments with `change`, which then keeps out of
    # the hand.
    novelty = novelty_enter if reads_watch else None
    if cues:
        moments = CueMoments()
        triggers = [cue_trigger(timings, moments,
                                bypass_gap=demo_mode and cue_bypass_gap),
                    change_trigger(timings, moments, novelty_enter=novelty)]
    else:
        triggers = [change_trigger(timings, novelty_enter=novelty)]
    triggers.extend(Trigger(name, predicate, cooldown(name), kind, reason) for name, predicate, kind, reason in specs)  # type: ignore[arg-type]
    entries = DEFAULT_KEYWORD_TRIGGERS if keyword_triggers is None else keyword_triggers
    for entry in entries:
        name = str(entry["name"])
        note = entry.get("note")
        context = f" Context from the wearer: {note}." if note else ""
        line = (
            f'Keyword trigger {name}: the VLM caption was "{{caption}}" with objects '
            f"[{{objects}}].{context} Decide for yourself whether this deserves speech; "
            "if so, say it in your own words, short and in the persona's voice."
        )
        triggers.append(keyword_trigger(
            name, list(entry["keywords"]), timings,
            min_hits=int(entry.get("min_hits", 2)),
            cooldown_s=float(entry.get("cooldown_s", timings.trigger_cooldown_default)),
            reason="Keyword '{kw}' seen in caption/objects", extra_line=line,
        ))
    if feed is not None:
        # Last: a camera trigger that fires on the same tick explains itself,
        # and this one costs a feed read.
        triggers.append(biometric_anomaly_trigger(timings, feed))
    return triggers
