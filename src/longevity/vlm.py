"""The T0 VLM call (docs/PERSON_A.md A10) — fired, never awaited.

This module exists to keep one promise: **the 1 Hz tick stream does not care how slow
the VLM is.** Gemini Flash-Lite typically returns in 300-600 ms, but SPEC §2.4 warns
"the p99 tail exceeds 2 s. The drop rule exists specifically to absorb that tail."

The rules, all of them load-bearing (CLAUDE.md invariant 3, SPEC §2.4, §5, §11.7):

1. **The tick never waits.** `request()` and `take()` are synchronous and never touch
   the network, so tick cadence is independent of Gemini however slow it gets.
2. **Up to two calls in flight, none cancelled at the tick budget.** The original
   rule -- one call, abandoned at the budget -- threw away 38% of calls on the
   13 Sep run (unconditional p50 ~1.33 s against a 1.4 s budget), and every
   cancellation also closed the HTTP/1.1 keep-alive connection, so the *next* call
   paid a fresh TLS handshake and overruns clustered. A call that runs past the
   budget now finishes and its answer is used if it is still fresh; only a hard
   ceiling (`ceiling_s`, ~3 s) cancels, and that exists to bound in-flight work,
   not to keep a clock. Two slots is what lets the next frame start while a slow
   call finishes, so an overrun costs one tick's freshness instead of one tick's
   coverage.
3. **Absent, never stale -- stale now means "older than `max_age_s`".** A late
   result is attached to the next tick assembled after it lands, which is the
   newest tick that has no `ai` yet, *if* its frame is younger than the freshness
   window B already applies (`Timings.ai_max_age_ms`). An older frame's result never
   follows a newer frame's (newest-frame wins), and each result is consume-once.
   §11.7 forbids carrying values forward across ticks; a late-but-fresh block with an
   honest `age_ms` is not that.
4. **Newest frame wins, never a queue** (§5.3): frames that arrive while both slots
   are busy overwrite each other in a one-item mailbox.

Consumers must tolerate gaps: coverage is no longer capped by the budget, but a
call past the ceiling or an API error still leaves a tick without an `ai` block.

## Request kinds (docs/PERCEPTION.md "Labeler")

The labeler no longer runs on every tick. Four reasons start a call, and they carry a
priority through the one-slot mailbox: ``wake`` (3, the watcher flagged something),
``look`` (2, the clerk asked one question of the current frame), ``hot`` (1, a concept
is in transition, steady or cooling -- today's cadence, or one call per `steady_s`)
and ``heartbeat`` (0, one call per minute when idle). A pending request is replaced
only by an equal-or-higher priority one; a lower-priority arrival is dropped and
counted. `offer()` is kept as the ``hot`` alias so the loop and every test still work.

`LabelerScheduler` decides *when* the loop should make a ``hot`` or ``heartbeat``
request; it is pure logic on a caller-supplied clock. The tagger owns one, feeds it
every started call, error and success, and enforces the hourly cap on ``hot``
requests. Wake-ups and heartbeats go through the cap; the cap is a cost ceiling on
the mode that can run away, not a switch that blinds the system.

A ``look`` result carries its ``answer`` alongside the §9 fields under
`look_answer_key` (``_look_answer``). `coerce()` never sees it, and the loop must
strip it with `split_look_answer()` before writing the tick's `ai` block, which
therefore stays byte-identical to today for every kind.

## A deliberate deviation from §12's wording, because Person B reads this field

§12 describes `as_of` as when the VLM *result* was produced. We instead set it to the
capture timestamp of the frame that was tagged. §12.2 tells B to "treat confidence as
decaying with age; a 4 s-old food_present is weaker evidence than a 200 ms-old one" —
that is a statement about how stale the *observation of the world* is, and the model's
own latency is not part of that. Timestamping from the result would understate
staleness by exactly the inference time, which is the error that actually matters.
Frame time is the honest, conservative number.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any, Callable, Literal, Protocol

from .ai_fields import LOOK_ANSWER_MAX_CHARS, PROMPT, coerce, look_suffix, response_schema
from .tick import VLM_BUDGET_S

log = logging.getLogger(__name__)

# Pinned deliberately. `gemini-flash-lite-latest` is a moving alias that currently
# rejects `thinking_budget=0` with a hard 400 on every call — see FINDINGS.md. Measured
# latency across flash-lite variants sits inside network noise, so there is nothing to
# win by chasing the alias and a whole tick stream to lose.
DEFAULT_MODEL = os.environ.get("T0_VLM_MODEL", "gemini-2.5-flash-lite")

# Two calls in flight covers the measured distribution: with a ~1.1-1.4 s call and a
# 1.5 s tick the average is ~1 in flight, and the second slot absorbs the tail. A
# third would only add quota pressure for results that land too old to use.
#
# VLM_MAX_IN_FLIGHT (alias T0_MAX_IN_FLIGHT) is the stage kill switch: 1 restores
# the serial tagger with a restart, no code edit. Read here, like T0_VLM_MODEL, so
# the standalone `t0` CLI honours it too; the backend bridge passes
# `Settings.vlm_max_in_flight` explicitly, which reads the same variables.
def _env_max_in_flight(env: dict[str, str] | None = None) -> int:
    env = env if env is not None else os.environ  # type: ignore[assignment]
    raw = (env.get("VLM_MAX_IN_FLIGHT") or env.get("T0_MAX_IN_FLIGHT") or "").strip()
    if not raw:
        return 2
    try:
        return min(2, max(1, int(raw)))
    except ValueError:
        log.warning("VLM_MAX_IN_FLIGHT=%r is not a number; using 2", raw)
        return 2


DEFAULT_MAX_IN_FLIGHT = _env_max_in_flight()

# The hard ceiling. At 1.5 s ticks a result landing by 3.0 s is claimed at most two
# ticks after its frame (age <= 3.0 s), inside the 3.75 s freshness window; one that
# lands later would be too old to use at the next tick anyway, so waiting longer
# only holds a slot. This is the one place a call is still cancelled.
DEFAULT_CEILING_S = 3.0

# Freshness window for attaching a late result, matching the floor of B's
# `Timings.ai_max_age_ms` (3 s at 1 Hz). The bridge passes the real value.
DEFAULT_MAX_AGE_S = 3.0

# --- Request kinds ------------------------------------------------------------

Kind = Literal["wake", "look", "hot", "heartbeat"]

#: Every request kind, highest priority first.
KINDS: tuple[str, ...] = ("wake", "look", "hot", "heartbeat")

#: Mailbox priority. A pending request yields only to an equal-or-higher one.
PRIORITY: dict[str, int] = {"wake": 3, "look": 2, "hot": 1, "heartbeat": 0}

#: Where a look's `answer` rides alongside the §9 fields until the loop strips it.
LOOK_ANSWER_KEY = "_look_answer"

#: Per-kind counter names, in the order `stats()["by_kind"]` reports them.
KIND_COUNTERS: tuple[str, ...] = ("requested", "started", "returned", "landed", "dropped")


def split_look_answer(
    fields: dict[str, Any], key: str = LOOK_ANSWER_KEY,
) -> tuple[dict[str, Any], str | None]:
    """``(fields without the look key, answer or None)``. Never mutates its input.

    The loop calls this on every claimed result before writing the tick's `ai`
    block, so the block carries exactly the §9 fields whatever kind of call made
    it. For a non-look result the answer is None and the fields come back as-is.
    """
    if key not in fields:
        return fields, None
    rest = dict(fields)
    answer = rest.pop(key)
    return rest, None if answer is None else str(answer)


class VLMClient(Protocol):
    """Anything that can turn a JPEG into a raw §9 field dict.

    `question` is the targeted-look extra (docs/PERCEPTION.md "Labeler" 4): when
    given, the response also carries a short string ``answer``. The tagger only
    passes it for ``look`` requests, so a client without the keyword still serves
    every other kind.
    """

    async def tag(self, jpeg: bytes, *, question: str | None = None) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


# --- Priority mailbox ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Request:
    kind: str
    frame_t: float
    jpeg: bytes
    question: str | None = None


class _Slot:
    """A one-item mailbox where a new request overwrites an unread one of equal or
    lower priority, and is itself dropped when a higher one is waiting.

    This is invariant 2 in miniature — "drop, never queue". If the tagger is busy
    when three frames arrive, the two older ones are discarded unread rather than
    forming a backlog, because by the time the tagger is free they describe a world
    that no longer exists. Priority only decides *which* single request survives:
    a wake-up must not be overwritten by the hot-mode frame that follows it.
    """

    __slots__ = ("_item", "_event", "_dropped")

    def __init__(self) -> None:
        self._item: _Request | None = None
        self._event = asyncio.Event()
        self._dropped = 0

    def put(self, req: _Request) -> _Request | None:
        """Offer `req`. Returns the request that was dropped, if any: the one it
        displaced, or `req` itself when a higher-priority request is pending."""
        held = self._item
        if held is not None and PRIORITY[req.kind] < PRIORITY[held.kind]:
            self._dropped += 1
            return req
        self._item = req
        self._event.set()
        if held is not None:
            self._dropped += 1
        return held

    async def get(self) -> _Request:
        await self._event.wait()
        self._event.clear()
        item, self._item = self._item, None
        assert item is not None
        return item

    @property
    def pending_kind(self) -> str | None:
        return None if self._item is None else self._item.kind

    @property
    def dropped(self) -> int:
        return self._dropped


# --- The scheduler ------------------------------------------------------------


class LabelerScheduler:
    """When the loop should ask for a ``hot`` or ``heartbeat`` call. Pure logic.

    Every method takes its time from the caller, so a test drives it with a fake
    clock and a replay source can run frame time faster than wall time. The cadence
    follows the concept state (docs/PERCEPTION.md "Gate and actions"):

    - transition: the first `transition_s` after any concept goes hot -> every tick;
    - cooling: `cooling_s` after a concept leaves hot -> every tick;
    - steady: a non-point concept hot past its transition -> every `steady_s`;
    - spent: only point concepts hot, past their transition -> heartbeat cadence
      (a coffee cup on the desk all afternoon costs one transition, then nothing);
    - cold: nothing hot or cooling -> every `heartbeat_s`;
    - dormant: cold with flat novelty for `dormant_after_s` -> every
      `dormant_heartbeat_s`; any novelty ends it.

    The hourly cap (`max_per_hour`, counted over every started call) turns hot mode
    off: while capped a hot-mode tick falls back to the heartbeat cadence, so the
    system keeps proving health at the idle rate. After `error_backoff_n`
    consecutive errors the heartbeat backs off to `dormant_heartbeat_s` until a
    call succeeds. Wake-ups are the loop's business and never gated here.
    """

    #: Novelty under this counts as flat for dormancy.
    NOVELTY_EPS = 0.01
    WINDOW_S = 3600.0

    def __init__(
        self,
        *,
        tick_interval_s: float,
        heartbeat_s: float = 60.0,
        transition_s: float = 60.0,
        steady_s: float = 10.0,
        cooling_s: float = 30.0,
        dormant_after_s: float = 300.0,
        dormant_heartbeat_s: float = 300.0,
        max_per_hour: int = 600,
        error_backoff_n: int = 5,
        point_concepts: frozenset[str] = frozenset(),
    ) -> None:
        self.tick_interval_s = tick_interval_s
        self.heartbeat_s = heartbeat_s
        self.transition_s = transition_s
        self.steady_s = steady_s
        self.cooling_s = cooling_s
        self.dormant_after_s = dormant_after_s
        self.dormant_heartbeat_s = dormant_heartbeat_s
        self.max_per_hour = max(0, int(max_per_hour))
        self.error_backoff_n = max(1, int(error_backoff_n))
        self._point = frozenset(point_concepts)

        self._hot_since: dict[str, float] = {}      # concept -> when it went hot
        self._cooling_since: dict[str, float] = {}  # concept -> when it started cooling
        self._flat_since: float | None = None       # cold with flat novelty since
        self._last_request: float | None = None     # last tick that asked for a call
        self._started: deque[float] = deque()       # rolling window of started calls
        self._errors_in_a_row = 0
        self._mode = "cold"
        self._now: float | None = None

    # -- the per-tick decision --

    def on_tick(
        self,
        now: float,
        hot: frozenset[str],
        cooling: frozenset[str],
        novelty: float,
        clock: float | None = None,
    ) -> str | None:
        """The kind to request this tick: ``"hot"``, ``"heartbeat"`` or None.

        `now` is the tick clock every cadence is measured in. `clock` is the wall
        time for the hourly cap when it differs (a replay runs frame time faster
        than real time and the cap is a cost per real hour); None means `now`.
        """
        self._now = now
        wall = now if clock is None else clock
        hot = frozenset(hot) - frozenset(cooling)
        self._track(now, hot, frozenset(cooling), novelty)
        mode = self._mode = self._mode_at(now)
        capped = self.capped(wall)

        if mode in ("transition", "cooling") and not capped:
            kind: str | None = "hot"
        elif mode == "steady" and not capped:
            kind = "hot" if self._due(now, self.steady_s) else None
        else:
            interval = self.dormant_heartbeat_s if mode == "dormant" else self.heartbeat_interval()
            kind = "heartbeat" if self._due(now, interval) else None
        if kind is not None:
            self._last_request = now
        return kind

    def _due(self, now: float, interval: float) -> bool:
        return self._last_request is None or now - self._last_request >= interval

    def _track(self, now: float, hot: frozenset[str], cooling: frozenset[str], novelty: float) -> None:
        for c in hot:
            self._hot_since.setdefault(c, now)
        for c in [c for c in self._hot_since if c not in hot]:
            del self._hot_since[c]
        for c in cooling:
            self._cooling_since.setdefault(c, now)
        for c in [c for c in self._cooling_since if c not in cooling]:
            del self._cooling_since[c]
        if not hot and not cooling and novelty < self.NOVELTY_EPS:
            if self._flat_since is None:
                self._flat_since = now
        else:
            self._flat_since = None

    def _mode_at(self, now: float) -> str:
        if any(now - t0 < self.transition_s for t0 in self._hot_since.values()):
            return "transition"
        if any(now - t0 < self.cooling_s for t0 in self._cooling_since.values()):
            return "cooling"
        if any(c not in self._point for c in self._hot_since):
            return "steady"
        if self._hot_since:
            return "spent"
        if self._flat_since is not None and now - self._flat_since >= self.dormant_after_s:
            return "dormant"
        return "cold"

    # -- what the tagger reports back --

    def note_started(self, kind: str, now: float) -> None:
        """A call of `kind` started at `now`; every kind counts toward the cap."""
        self._now = now if self._now is None else max(self._now, now)
        self._prune(now)
        self._started.append(now)

    def note_error(self) -> None:
        self._errors_in_a_row += 1

    def note_success(self) -> None:
        self._errors_in_a_row = 0

    # -- queries --

    def capped(self, now: float) -> bool:
        """True once `max_per_hour` calls have started in the trailing hour."""
        self._prune(now)
        return len(self._started) >= self.max_per_hour

    def heartbeat_interval(self) -> float:
        """`heartbeat_s`, or `dormant_heartbeat_s` while backed off on errors."""
        if self._errors_in_a_row >= self.error_backoff_n:
            return self.dormant_heartbeat_s
        return self.heartbeat_s

    def calls_last_hour(self, now: float | None = None) -> int:
        if now is not None:
            self._prune(now)
        return len(self._started)

    def _prune(self, now: float) -> None:
        cutoff = now - self.WINDOW_S
        while self._started and self._started[0] <= cutoff:
            self._started.popleft()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def errors_in_a_row(self) -> int:
        return self._errors_in_a_row

    def stats(self) -> dict[str, Any]:
        now = self._now
        return {
            "mode": self._mode,
            "capped": self.capped(now) if now is not None else False,
            "errors_in_a_row": self._errors_in_a_row,
            "calls_last_hour": self.calls_last_hour(now),
            "max_per_hour": self.max_per_hour,
            "heartbeat_s": self.heartbeat_interval(),
        }


# --- The tagger ---------------------------------------------------------------


class T0Tagger:
    """Overlapping, self-scheduling VLM tagger. Both public entry points are non-blocking.

    The T0 loop calls `request()` (or its ``hot`` alias `offer()`) with a frame and
    `take()` when assembling the tick. Neither ever awaits the network, so a slow
    API cannot stall capture.

    Results are **consume-once**: `take()` returns a result to exactly one tick and
    then forgets it. That is what keeps "absent, never stale" true by construction
    now that results may land after the tick budget.

    `budget_s` is the on-time line: calls slower than it are counted as overruns in
    the stats, but no longer cancelled. `ceiling_s` is the hard cancel. `max_age_s`
    is how old a frame may be when its result is attached to a tick.

    The tagger owns a `LabelerScheduler` (`tagger.scheduler`), built from the
    ``labeler_*`` values the bridge passes, or handed in ready-made. It reports every
    started call, error and success to it and enforces the hourly cap on ``hot``
    requests; the loop asks `scheduler.on_tick()` what to request. `clock` is the
    wall clock the cap is measured on (tests pass a fake).
    """

    def __init__(
        self,
        client: VLMClient | None,
        *,
        budget_s: float = VLM_BUDGET_S,
        ceiling_s: float | None = None,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        max_in_flight: int = DEFAULT_MAX_IN_FLIGHT,
        log_every: int = 60,
        scheduler: LabelerScheduler | None = None,
        look_answer_key: str = LOOK_ANSWER_KEY,
        clock: Callable[[], float] = time.time,
        tick_interval_s: float | None = None,
        heartbeat_s: float = 60.0,
        transition_s: float = 60.0,
        steady_s: float = 10.0,
        cooling_s: float = 30.0,
        dormant_after_s: float = 300.0,
        dormant_heartbeat_s: float = 300.0,
        max_per_hour: int = 600,
        error_backoff_n: int = 5,
        point_concepts: frozenset[str] = frozenset(),
    ) -> None:
        self._client = client
        self._budget = budget_s
        # Never below the budget: a ceiling under the budget would silently restore
        # the old cancel-at-budget behaviour.
        self._ceiling = max(budget_s, DEFAULT_CEILING_S if ceiling_s is None else ceiling_s)
        self._max_age = max_age_s
        self._max_in_flight = max(1, max_in_flight)
        self._log_every = log_every
        self._look_answer_key = look_answer_key
        self._clock = clock
        self.scheduler = scheduler if scheduler is not None else LabelerScheduler(
            tick_interval_s=budget_s if tick_interval_s is None else tick_interval_s,
            heartbeat_s=heartbeat_s, transition_s=transition_s, steady_s=steady_s,
            cooling_s=cooling_s, dormant_after_s=dormant_after_s,
            dormant_heartbeat_s=dormant_heartbeat_s, max_per_hour=max_per_hour,
            error_backoff_n=error_backoff_n, point_concepts=point_concepts,
        )
        self._slot = _Slot()
        self._task: asyncio.Task[None] | None = None
        self._calls_in_flight: set[asyncio.Task[None]] = set()
        self._capacity: asyncio.Semaphore | None = None
        self._running = False

        # A landed-but-unconsumed result: (coerced fields, frame capture time).
        self._pending: tuple[dict[str, Any], float] | None = None
        # Capture time of the newest frame whose result has landed. A slower call
        # for an *older* frame that lands after it is dropped: the tick stream must
        # never step back in time, or the gate reads the regression as a change.
        self._newest_frame_t = float("-inf")
        #: Called (synchronously, from the tagger task) whenever a result becomes
        #: pending. `T0Loop` uses it to publish a result without waiting a tick.
        self.on_result: Callable[[], None] | None = None

        # Counters. `ticks_served / ticks_asked` is the coverage number.
        self.calls = 0
        self.returned = 0      # came back with a result, on time or late
        self.overruns = 0      # took longer than the budget (late + timeouts)
        self.late = 0          # overran the budget but landed under the ceiling
        self.timeouts = 0      # hit the hard ceiling and were cancelled
        self.discarded = 0     # landed but never reached a tick (superseded / too old)
        self.errors = 0
        self.ticks_asked = 0
        self.ticks_served = 0
        self.in_flight = 0
        self.max_in_flight_seen = 0
        #: Per request kind: requested, started, returned, landed, dropped.
        self.by_kind: dict[str, dict[str, int]] = {
            k: {c: 0 for c in KIND_COUNTERS} for k in KINDS
        }
        #: ``hot`` requests refused because the scheduler was at its hourly cap.
        self.dropped_capped = 0
        # Every finished call's latency, overruns included at their real latency and
        # ceiling timeouts at the ceiling (a lower bound). Survivor-only percentiles
        # hid a median sitting on the budget line; these do not.
        self._latencies: deque[float] = deque(maxlen=200)
        # (outcome, kind) per finished call, newest last.
        self._outcomes: deque[tuple[str, str]] = deque(maxlen=50)

    # -- lifecycle --

    async def start(self) -> None:
        if self._client is None:
            log.warning("T0 VLM disabled (no client); every tick will omit the ai block")
            return
        self._running = True
        self._capacity = asyncio.Semaphore(self._max_in_flight)
        self._task = asyncio.create_task(self._loop(), name="t0-vlm")

    async def aclose(self) -> None:
        self._running = False
        tasks = [t for t in (self._task, *self._calls_in_flight) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._calls_in_flight.clear()
        if self._client is not None:
            await self._client.aclose()

    # -- the T0 loop's entry points, all non-blocking --

    def request(
        self,
        kind: Kind,
        frame_t: float,
        jpeg: bytes,
        *,
        question: str | None = None,
    ) -> None:
        """Ask for one call of `kind` on this frame. Returns immediately.

        If both call slots are busy the request waits in the single mailbox slot,
        replacing a pending request of equal or lower priority and being dropped
        under a higher one. It is never queued: "New frames never queue behind an
        in-flight call — a stale frame has negative value" (§2.4). A ``hot``
        request is refused outright while the scheduler is at its hourly cap;
        ``wake``, ``look`` and ``heartbeat`` always go through.
        """
        if kind not in PRIORITY:
            raise ValueError(f"unknown labeler request kind {kind!r}")
        if question is not None and kind != "look":
            raise ValueError("a question belongs to a 'look' request")
        if self._client is None:
            return
        counters = self.by_kind[kind]
        counters["requested"] += 1
        if kind == "hot" and self.scheduler.capped(self._clock()):
            self.dropped_capped += 1
            counters["dropped"] += 1
            return
        dropped = self._slot.put(_Request(kind, frame_t, jpeg, question))
        if dropped is not None:
            self.by_kind[dropped.kind]["dropped"] += 1

    def offer(self, frame_t: float, jpeg: bytes) -> None:
        """Hand the tagger the newest frame as a ``hot`` request (today's cadence)."""
        self.request("hot", frame_t, jpeg)

    def take(self, now: float | None = None) -> tuple[dict[str, Any], float] | None:
        """Claim a landed result for the tick being assembled at `now`, or None.

        None means this tick gets no `ai` block. `now` is the tick's (frame) time;
        a pending result whose frame is older than `max_age_s` relative to it is
        dropped rather than attached. Without `now` there is no age check.
        """
        self.ticks_asked += 1
        result = self.claim(now)
        if self._log_every and self.ticks_asked % self._log_every == 0:
            log.info("T0 VLM %s", self.stats_line())
        return result

    def claim(self, now: float | None = None) -> tuple[dict[str, Any], float] | None:
        """Consume the pending result for a tick that was already counted.

        `take()` is this plus the per-tick bookkeeping. The loop calls `claim`
        directly when it attaches a just-landed result to a tick it has already
        emitted, so that tick is not asked for twice.

        A look's answer rides in the fields under `look_answer_key`; the caller
        strips it with `split_look_answer()` before the fields become an `ai` block.
        """
        result, self._pending = self._pending, None
        if result is None:
            return None
        if now is not None and now - result[1] > self._max_age:
            self.discarded += 1
            self._outcomes.append(("expired", "?"))
            return None
        self.ticks_served += 1
        return result

    # -- internals --

    async def _loop(self) -> None:
        """Dispatch the pending request whenever a call slot is free. Never on a timer.

        The slot is acquired *before* reading the mailbox, so a frame that arrives
        while both calls are busy keeps being overwritten by newer ones and the call
        that eventually starts is always on the freshest frame.
        """
        assert self._capacity is not None
        while self._running:
            try:
                await self._capacity.acquire()
            except asyncio.CancelledError:
                return
            try:
                req = await self._slot.get()
            except asyncio.CancelledError:
                self._capacity.release()
                return
            self.by_kind[req.kind]["started"] += 1
            self.scheduler.note_started(req.kind, self._clock())
            task = asyncio.create_task(self._call(req), name="t0-vlm-call")
            self._calls_in_flight.add(task)
            task.add_done_callback(self._calls_in_flight.discard)

    async def _call(self, req: _Request) -> None:
        assert self._client is not None and self._capacity is not None
        started = time.perf_counter()
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight_seen = max(self.max_in_flight_seen, self.in_flight)
        # Only a look passes the keyword, so a client without it serves every
        # other kind unchanged (the overlap tests' gated client has none).
        if req.question is not None:
            call = self._client.tag(req.jpeg, question=req.question)
        else:
            call = self._client.tag(req.jpeg)
        try:
            raw = await asyncio.wait_for(call, self._ceiling)
        except (asyncio.TimeoutError, TimeoutError):
            # Past the ceiling nothing it could say would still be fresh by the time
            # a tick could carry it. The latency is recorded at the ceiling -- a lower
            # bound, but in the distribution rather than silently missing from it.
            self.timeouts += 1
            self.overruns += 1
            self._latencies.append(self._ceiling)
            self._outcomes.append(("timeout", req.kind))
            self.scheduler.note_error()
            return
        except Exception as exc:  # noqa: BLE001 - a bad call must not stop the clock
            self.errors += 1
            self._outcomes.append(("error", req.kind))
            self.scheduler.note_error()
            log.warning("T0 VLM %s call failed: %s", req.kind, exc)
            return
        finally:
            self.in_flight -= 1
            self._capacity.release()

        latency = time.perf_counter() - started
        self._latencies.append(latency)
        self.returned += 1
        self.by_kind[req.kind]["returned"] += 1
        self.scheduler.note_success()
        if latency > self._budget:
            self.overruns += 1
            self.late += 1
            self._outcomes.append(("late", req.kind))
        else:
            self._outcomes.append(("returned", req.kind))
        self._land(raw, req.frame_t, req.kind)

    def _land(self, raw: dict[str, Any] | None, frame_t: float, kind: str = "hot") -> None:
        """Coerce a raw response and make it the pending result, newest frame wins.

        A look's ``answer`` is popped *before* `coerce()` -- which never sees it --
        and rides alongside the §9 fields under `look_answer_key`, bounded to
        `LOOK_ANSWER_MAX_CHARS`. Every other key goes through `coerce()` exactly
        as before, so the fields are byte-identical to today for every kind.
        """
        raw = dict(raw) if isinstance(raw, dict) else {}
        answer = raw.pop("answer", None)
        fields = coerce(raw)
        if answer is not None:
            fields[self._look_answer_key] = str(answer)[:LOOK_ANSWER_MAX_CHARS]
        if frame_t <= self._newest_frame_t and answer is None:
            # A newer frame's answer already landed; this one would step the tick
            # stream back in time. A look is exempt: its frame is usually the one
            # a wake or hot call just labelled, and its value is the answer, which
            # the loop strips before any tick is written. It never moves the
            # newest-frame mark backwards.
            self.discarded += 1
            return
        if self._pending is not None:
            # Two results inside one tick: the newer observation wins.
            self.discarded += 1
        self._newest_frame_t = max(self._newest_frame_t, frame_t)
        self._pending = (fields, frame_t)
        self.by_kind.setdefault(kind, {c: 0 for c in KIND_COUNTERS})["landed"] += 1
        if self.on_result is not None:
            try:
                self.on_result()
            except Exception:  # noqa: BLE001 - a listener must not kill the tagger
                log.exception("T0 VLM on_result listener failed")

    # -- observability --

    @property
    def coverage(self) -> float:
        """Fraction of ticks that carried an `ai` block."""
        return self.ticks_served / self.ticks_asked if self.ticks_asked else 0.0

    def _percentile(self, fraction: float, window: int | None = None) -> float:
        """Latency percentile in ms over every finished call (overruns included)."""
        lat = list(self._latencies)
        lat = sorted(lat[-window:] if window else lat)
        if not lat:
            return 0.0
        return lat[min(len(lat) - 1, int((len(lat) - 1) * fraction))] * 1000

    def _token_stats(self) -> dict[str, int]:
        """Token percentiles the client recorded, or zeros. Never raises: the
        numbers are a measurement, and a client without them (the fake, an
        SDK without usage metadata) must not cost a stats read."""
        try:
            return token_stats(getattr(self._client, "usage", None))
        except Exception:  # noqa: BLE001
            return token_stats(None)

    def stats_line(self) -> str:
        tokens = self._token_stats()
        tok = (
            f" tok_in_p50={tokens['prompt_tokens_p50']} tok_out_p50={tokens['output_tokens_p50']}"
            if tokens["token_calls"] else ""
        )
        kinds = " ".join(f"{k}={self.by_kind[k]['started']}" for k in KINDS)
        sched = self.scheduler.stats()
        return (
            f"coverage={self.coverage:6.1%} "
            f"ticks={self.ticks_served}/{self.ticks_asked} "
            f"calls={self.calls} ok={self.returned} "
            f"overrun={self.overruns} late={self.late} timeout={self.timeouts} "
            f"discarded={self.discarded} err={self.errors} "
            f"p50={self._percentile(0.50):.0f}ms p90={self._percentile(0.90):.0f}ms "
            f"frames_dropped={self._slot.dropped}{tok} "
            f"mode={sched['mode']} {kinds} capped={int(sched['capped'])}"
        )

    def stats(self) -> dict[str, Any]:
        """Structured live-demo health for the most recent calls."""
        finished = self.returned + self.timeouts
        return {
            "calls": self.calls,
            "returned": self.returned,
            "overruns": self.overruns,
            "late": self.late,
            "timeouts": self.timeouts,
            "discarded": self.discarded,
            "errors": sum(outcome == "error" for outcome, _ in self._outcomes),
            "in_flight": self.in_flight,
            "max_in_flight": self._max_in_flight,
            "latency_p50_ms": round(self._percentile(0.50, 50), 1),
            "latency_p90_ms": round(self._percentile(0.90, 50), 1),
            "latency_p95_ms": round(self._percentile(0.95, 50), 1),
            "over_budget_rate": round(self.overruns / finished, 3) if finished else 0.0,
            "budget_s": self._budget,
            "ceiling_s": self._ceiling,
            "frames_dropped": self._slot.dropped,
            "dropped_capped": self.dropped_capped,
            "by_kind": {k: dict(v) for k, v in self.by_kind.items()},
            "scheduler": self.scheduler.stats(),
            **self._token_stats(),
        }


# --- Token usage --------------------------------------------------------------


def read_usage(resp: Any) -> tuple[int, int] | None:
    """``(prompt_tokens, output_tokens)`` from a Gemini response, or None.

    Read with getattr throughout: usage metadata is optional in the SDK and the
    counts inside it may be None. This is measurement only, so it never raises.
    """
    try:
        meta = getattr(resp, "usage_metadata", None)
        if meta is None:
            return None
        prompt = getattr(meta, "prompt_token_count", None)
        output = getattr(meta, "candidates_token_count", None)
        if prompt is None and output is None:
            return None
        return int(prompt or 0), int(output or 0)
    except Exception:  # noqa: BLE001
        return None


def token_stats(usage: Any) -> dict[str, int]:
    """p50 prompt/output tokens over the recorded calls (zeros when none)."""
    rows = list(usage or ())
    if not rows:
        return {"token_calls": 0, "prompt_tokens_p50": 0, "output_tokens_p50": 0}

    def p50(values: list[int]) -> int:
        ordered = sorted(values)
        return ordered[(len(ordered) - 1) // 2]

    return {
        "token_calls": len(rows),
        "prompt_tokens_p50": p50([r[0] for r in rows]),
        "output_tokens_p50": p50([r[1] for r in rows]),
    }


# --- Clients ------------------------------------------------------------------


class GeminiClient:
    """Gemini Flash-Lite with structured output over the §9 field set.

    The field list and schema come from `ai_fields`; this class must never name a §9
    field itself (docs/PERSON_A.md A2 — one place, one edit). A targeted look (`question`)
    uses the same prompt plus `ai_fields.look_suffix()` and the schema variant that
    adds the one ``answer`` string; nothing else about the call changes.
    """

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        from google import genai  # imported lazily so tests need no SDK or key

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("set GEMINI_API_KEY to enable the T0 VLM call")
        self._genai = genai
        self._client = genai.Client(api_key=key)
        self._model = model
        self._schema = response_schema()
        self._look_schema = response_schema(extra_answer=True)
        #: (prompt_tokens, output_tokens) per call, newest last. Output tokens
        #: are the lever on Gemini latency (~8.7 ms each), so schema trims are
        #: measured here rather than guessed.
        self.usage: deque[tuple[int, int]] = deque(maxlen=200)

    async def tag(self, jpeg: bytes, *, question: str | None = None) -> dict[str, Any]:
        from google.genai import types

        look = question is not None
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=self._look_schema if look else self._schema,
            # Flash-Lite will happily spend the whole budget thinking. We have 1 second
            # and the task is "report what is plainly visible", so buy latency instead.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            # A full answer is ~150-200 tokens. The cap only matters when the model
            # runs away on a caption, and then a truncated call that errors fast is
            # better than one that decodes for seconds and holds a call slot. A look
            # adds one bounded string, so it gets a little more room.
            max_output_tokens=640 if look else 512,
            temperature=0.0,
        )
        resp = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                PROMPT + look_suffix(question) if look else PROMPT,
            ],
            config=config,
        )
        self._record_usage(resp)
        return json.loads(resp.text or "{}")

    def _record_usage(self, resp: Any) -> None:
        usage = read_usage(resp)
        if usage is None:
            return
        self.usage.append(usage)
        log.debug("T0 VLM tokens prompt=%d out=%d", *usage)

    async def aclose(self) -> None:
        return None


DEFAULT_FAKE_FIELDS: dict[str, Any] = {"scene": "office", "activity": "seated", "conf": 0.9}


class FakeClient:
    """A latency simulator for development and for proving the overlap rules.

    `latency` may be a constant or a zero-arg callable, which is how the check script
    reproduces §2.4's "typically 300-600 ms, but the p99 tail exceeds 2 s".

    Every tick carries the same constant `fields`. `build_client("fake")` merges
    ``T0_FAKE_FIELDS`` (a JSON object) over the defaults, which is how a run with
    no API keys drives the *downstream* pipeline — the gate only wakes on what T0
    reports, so without an override a fake run can never produce an alcohol
    sighting, an escalation, or a question (ASK_DESIGN §8.10)::

        T0_FAKE_FIELDS='{"alcohol_visible": true, "scene": "restaurant"}' \
            uv run t0 --vlm fake

    The values are passed through `ai_fields.coerce` downstream like any other
    tagger output, so a bogus enum member is dropped there rather than here.

    A look (`question`) is recorded in `questions` and answered with `answer`,
    so the plumbing from the clerk's question to the tick can be proven offline.
    """

    def __init__(
        self,
        latency: float | Any = 0.4,
        fields: dict[str, Any] | None = None,
        answer: str = "not visible in this frame",
    ) -> None:
        self._latency = latency
        self._fields = dict(fields) if fields else dict(DEFAULT_FAKE_FIELDS)
        self._answer = answer
        self.calls = 0
        self.cancelled = 0
        self.questions: list[str] = []
        # `max_in_flight` is the direct evidence of self-scheduling (§5.3). A fixed
        # timer firing every second into a slow API stacks up concurrent calls; the
        # tagger holds this at its `max_in_flight` (2) no matter how slow the API is.
        self.in_flight = 0
        self.max_in_flight = 0

    async def tag(self, jpeg: bytes, *, question: str | None = None) -> dict[str, Any]:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if question is not None:
            self.questions.append(question)
        delay = self._latency() if callable(self._latency) else self._latency
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1
        out = dict(self._fields)
        if question is not None:
            out["answer"] = self._answer
        return out

    async def aclose(self) -> None:
        return None


def fake_fields(env: dict[str, str] | None = None) -> dict[str, Any]:
    """`DEFAULT_FAKE_FIELDS` with ``T0_FAKE_FIELDS`` merged over it (§8.10).

    Invalid JSON, or JSON that is not an object, warns once and yields the
    defaults: a typo in a demo environment variable must not take down the run it was meant
    to configure.
    """
    raw = (env if env is not None else os.environ).get("T0_FAKE_FIELDS", "").strip()
    fields = dict(DEFAULT_FAKE_FIELDS)
    if not raw:
        return fields
    try:
        override = json.loads(raw)
    except ValueError as exc:
        log.warning("T0_FAKE_FIELDS is not valid JSON (%s); using the defaults", exc)
        return fields
    if not isinstance(override, dict):
        log.warning("T0_FAKE_FIELDS must be a JSON object, got %s; using the defaults",
                    type(override).__name__)
        return fields
    fields.update(override)
    log.info("fake tagger fields: %s", fields)
    return fields


def build_client(kind: str = "gemini") -> VLMClient | None:
    """Resolve `--vlm {gemini,fake,off}` into a client."""
    if kind == "off":
        return None
    if kind == "fake":
        return FakeClient(fields=fake_fields())
    return GeminiClient()
