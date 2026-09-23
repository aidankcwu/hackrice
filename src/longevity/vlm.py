"""The T0 VLM call (PERSON_A.md A10) — fired, never awaited.

This module exists to keep one promise: **the 1 Hz tick stream does not care how slow
the VLM is.** Gemini Flash-Lite typically returns in 300-600 ms, but SPEC §2.4 warns
"the p99 tail exceeds 2 s. The drop rule exists specifically to absorb that tail."

The rules, all of them load-bearing (CLAUDE.md invariant 3, SPEC §2.4, §5, §11.7):

1. **The tick never waits.** `offer()` and `take()` are synchronous and never touch
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
import json
import logging
import os
import time
from typing import Any, Callable, Protocol

from .ai_fields import PROMPT, coerce, response_schema
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


class VLMClient(Protocol):
    """Anything that can turn a JPEG into a raw §9 field dict."""

    async def tag(self, jpeg: bytes) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


# --- Newest-wins mailbox ------------------------------------------------------


class _Slot:
    """A one-item mailbox where a new frame overwrites an unread one.

    This is invariant 2 in miniature — "drop, never queue". If the tagger is busy
    when three frames arrive, the two older ones are discarded unread rather than
    forming a backlog, because by the time the tagger is free they describe a world
    that no longer exists.
    """

    __slots__ = ("_item", "_event", "_dropped")

    def __init__(self) -> None:
        self._item: tuple[float, bytes] | None = None
        self._event = asyncio.Event()
        self._dropped = 0

    def put(self, item: tuple[float, bytes]) -> None:
        if self._item is not None:
            self._dropped += 1
        self._item = item
        self._event.set()

    async def get(self) -> tuple[float, bytes]:
        await self._event.wait()
        self._event.clear()
        item, self._item = self._item, None
        assert item is not None
        return item

    @property
    def dropped(self) -> int:
        return self._dropped


# --- The tagger ---------------------------------------------------------------


class T0Tagger:
    """Overlapping, self-scheduling VLM tagger. Both public entry points are non-blocking.

    The T0 loop calls `offer()` with each captured frame and `take()` when assembling
    the tick. Neither ever awaits the network, so a slow API cannot stall capture.

    Results are **consume-once**: `take()` returns a result to exactly one tick and
    then forgets it. That is what keeps "absent, never stale" true by construction
    now that results may land after the tick budget.

    `budget_s` is the on-time line: calls slower than it are counted as overruns in
    the stats, but no longer cancelled. `ceiling_s` is the hard cancel. `max_age_s`
    is how old a frame may be when its result is attached to a tick.
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
    ) -> None:
        self._client = client
        self._budget = budget_s
        # Never below the budget: a ceiling under the budget would silently restore
        # the old cancel-at-budget behaviour.
        self._ceiling = max(budget_s, DEFAULT_CEILING_S if ceiling_s is None else ceiling_s)
        self._max_age = max_age_s
        self._max_in_flight = max(1, max_in_flight)
        self._log_every = log_every
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
        # Every finished call's latency, overruns included at their real latency and
        # ceiling timeouts at the ceiling (a lower bound). Survivor-only percentiles
        # hid a median sitting on the budget line; these do not.
        self._latencies: deque[float] = deque(maxlen=200)
        self._outcomes: deque[str] = deque(maxlen=50)

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

    def offer(self, frame_t: float, jpeg: bytes) -> None:
        """Hand the tagger the newest frame. Returns immediately.

        If both call slots are busy this frame waits in the single slot, replacing
        whatever was there. It is never queued: "New frames never queue behind an
        in-flight call — a stale frame has negative value" (§2.4).
        """
        if self._client is not None:
            self._slot.put((frame_t, jpeg))

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
        """
        result, self._pending = self._pending, None
        if result is None:
            return None
        if now is not None and now - result[1] > self._max_age:
            self.discarded += 1
            self._outcomes.append("expired")
            return None
        self.ticks_served += 1
        return result

    # -- internals --

    async def _loop(self) -> None:
        """Dispatch the newest frame whenever a call slot is free. Never on a timer.

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
                frame_t, jpeg = await self._slot.get()
            except asyncio.CancelledError:
                self._capacity.release()
                return
            task = asyncio.create_task(self._call(frame_t, jpeg), name="t0-vlm-call")
            self._calls_in_flight.add(task)
            task.add_done_callback(self._calls_in_flight.discard)

    async def _call(self, frame_t: float, jpeg: bytes) -> None:
        assert self._client is not None and self._capacity is not None
        started = time.perf_counter()
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight_seen = max(self.max_in_flight_seen, self.in_flight)
        try:
            raw = await asyncio.wait_for(self._client.tag(jpeg), self._ceiling)
        except (asyncio.TimeoutError, TimeoutError):
            # Past the ceiling nothing it could say would still be fresh by the time
            # a tick could carry it. The latency is recorded at the ceiling -- a lower
            # bound, but in the distribution rather than silently missing from it.
            self.timeouts += 1
            self.overruns += 1
            self._latencies.append(self._ceiling)
            self._outcomes.append("timeout")
            return
        except Exception as exc:  # noqa: BLE001 - a bad call must not stop the clock
            self.errors += 1
            self._outcomes.append("error")
            log.warning("T0 VLM call failed: %s", exc)
            return
        finally:
            self.in_flight -= 1
            self._capacity.release()

        latency = time.perf_counter() - started
        self._latencies.append(latency)
        self.returned += 1
        if latency > self._budget:
            self.overruns += 1
            self.late += 1
            self._outcomes.append("late")
        else:
            self._outcomes.append("returned")
        self._land(coerce(raw), frame_t)

    def _land(self, fields: dict[str, Any], frame_t: float) -> None:
        if frame_t <= self._newest_frame_t:
            # A newer frame's answer already landed; this one would step the tick
            # stream back in time.
            self.discarded += 1
            return
        if self._pending is not None:
            # Two results inside one tick: the newer observation wins.
            self.discarded += 1
        self._newest_frame_t = frame_t
        self._pending = (fields, frame_t)
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
        return (
            f"coverage={self.coverage:6.1%} "
            f"ticks={self.ticks_served}/{self.ticks_asked} "
            f"calls={self.calls} ok={self.returned} "
            f"overrun={self.overruns} late={self.late} timeout={self.timeouts} "
            f"discarded={self.discarded} err={self.errors} "
            f"p50={self._percentile(0.50):.0f}ms p90={self._percentile(0.90):.0f}ms "
            f"frames_dropped={self._slot.dropped}{tok}"
        )

    def stats(self) -> dict[str, int | float]:
        """Structured live-demo health for the most recent calls."""
        finished = self.returned + self.timeouts
        return {
            "calls": self.calls,
            "returned": self.returned,
            "overruns": self.overruns,
            "late": self.late,
            "timeouts": self.timeouts,
            "discarded": self.discarded,
            "errors": sum(outcome == "error" for outcome in self._outcomes),
            "in_flight": self.in_flight,
            "max_in_flight": self._max_in_flight,
            "latency_p50_ms": round(self._percentile(0.50, 50), 1),
            "latency_p90_ms": round(self._percentile(0.90, 50), 1),
            "latency_p95_ms": round(self._percentile(0.95, 50), 1),
            "over_budget_rate": round(self.overruns / finished, 3) if finished else 0.0,
            "budget_s": self._budget,
            "ceiling_s": self._ceiling,
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
    field itself (PERSON_A.md A2 — one place, one edit).
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
        #: (prompt_tokens, output_tokens) per call, newest last. Output tokens
        #: are the lever on Gemini latency (~8.7 ms each), so schema trims are
        #: measured here rather than guessed.
        self.usage: deque[tuple[int, int]] = deque(maxlen=200)

    async def tag(self, jpeg: bytes) -> dict[str, Any]:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=self._schema,
            # Flash-Lite will happily spend the whole budget thinking. We have 1 second
            # and the task is "report what is plainly visible", so buy latency instead.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            # A full answer is ~150-200 tokens. The cap only matters when the model
            # runs away on a caption, and then a truncated call that errors fast is
            # better than one that decodes for seconds and holds a call slot.
            max_output_tokens=512,
            temperature=0.0,
        )
        resp = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                PROMPT,
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
    """

    def __init__(self, latency: float | Any = 0.4, fields: dict[str, Any] | None = None) -> None:
        self._latency = latency
        self._fields = dict(fields) if fields else dict(DEFAULT_FAKE_FIELDS)
        self.calls = 0
        self.cancelled = 0
        # `max_in_flight` is the direct evidence of self-scheduling (§5.3). A fixed
        # timer firing every second into a slow API stacks up concurrent calls; the
        # tagger holds this at its `max_in_flight` (2) no matter how slow the API is.
        self.in_flight = 0
        self.max_in_flight = 0

    async def tag(self, jpeg: bytes) -> dict[str, Any]:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        delay = self._latency() if callable(self._latency) else self._latency
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.in_flight -= 1
        return dict(self._fields)

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
