"""The T0 VLM call (PERSON_A.md A10) — fired, never awaited.

This module exists to keep one promise: **the 1 Hz tick stream does not care how slow
the VLM is.** Gemini Flash-Lite typically returns in 300-600 ms, but SPEC §2.4 warns
"the p99 tail exceeds 2 s. The drop rule exists specifically to absorb that tail."

Three rules, all of them load-bearing (CLAUDE.md invariant 3, SPEC §2.4, §5, §11.7):

1. **Budget is 1 second.** A call that has not returned by then is abandoned.
2. **On overrun the result is discarded** and the tick is written with the `ai` block
   *absent* — never a stale one. §2.4 floats carrying last-known values forward with a
   bumped `age_ms`; §11.7 and CLAUDE.md invariant 3 both overrule it. Absent wins.
3. **Self-scheduling, not a fixed timer.** "Fire the next call when the previous
   returns or its budget expires, using the newest available frame" (§5.3). A timer
   accumulates backlog under API slowness; self-scheduling degrades gracefully.

Consumers must tolerate gaps: expect 50-80% of ticks to carry an `ai` block (§12.2).

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
from typing import Any, Protocol

from .ai_fields import PROMPT, coerce, response_schema
from .tick import VLM_BUDGET_S

log = logging.getLogger(__name__)

# Pinned deliberately. `gemini-flash-lite-latest` is a moving alias that currently
# rejects `thinking_budget=0` with a hard 400 on every call — see FINDINGS.md. Measured
# latency across flash-lite variants sits inside network noise, so there is nothing to
# win by chasing the alias and a whole tick stream to lose.
DEFAULT_MODEL = os.environ.get("T0_VLM_MODEL", "gemini-2.5-flash-lite")


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
    """Self-scheduling VLM tagger. Both of its public methods are non-blocking.

    The T0 loop calls `offer()` with each captured frame and `take()` when assembling
    the tick. Neither ever awaits the network, so a slow API cannot stall capture.

    Results are **consume-once**: `take()` returns a result to exactly one tick and
    then forgets it. That is what makes "absent, never stale" true by construction
    rather than by remembering to check an age somewhere.
    """

    def __init__(
        self,
        client: VLMClient | None,
        *,
        budget_s: float = VLM_BUDGET_S,
        log_every: int = 60,
    ) -> None:
        self._client = client
        self._budget = budget_s
        self._log_every = log_every
        self._slot = _Slot()
        self._task: asyncio.Task[None] | None = None
        self._running = False

        # A landed-but-unconsumed result: (coerced fields, frame capture time).
        self._pending: tuple[dict[str, Any], float] | None = None

        # Counters. `ticks_served / ticks_asked` is the coverage number §2.4 predicts
        # will sit between 0.5 and 0.8, and PERSON_A.md wants logged.
        self.calls = 0
        self.returned = 0
        self.overruns = 0
        self.errors = 0
        self.ticks_asked = 0
        self.ticks_served = 0
        self._latencies: deque[float] = deque(maxlen=200)
        self._outcomes: deque[str] = deque(maxlen=50)

    # -- lifecycle --

    async def start(self) -> None:
        if self._client is None:
            log.warning("T0 VLM disabled (no client); every tick will omit the ai block")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="t0-vlm")

    async def aclose(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()

    # -- the T0 loop's two entry points, both non-blocking --

    def offer(self, frame_t: float, jpeg: bytes) -> None:
        """Hand the tagger the newest frame. Returns immediately.

        If a call is in flight this frame simply waits in the single slot, replacing
        whatever was there. It is never queued behind the in-flight call: "New frames
        never queue behind an in-flight call — a stale frame has negative value" (§2.4).
        """
        if self._client is not None:
            self._slot.put((frame_t, jpeg))

    def take(self) -> tuple[dict[str, Any], float] | None:
        """Claim a landed result for the tick being assembled, or None.

        None means this tick gets no `ai` block. That is a normal, expected outcome
        20-50% of the time, not an error.
        """
        self.ticks_asked += 1
        result, self._pending = self._pending, None
        if result is not None:
            self.ticks_served += 1
        if self._log_every and self.ticks_asked % self._log_every == 0:
            log.info("T0 VLM %s", self.stats_line())
        return result

    # -- internals --

    async def _loop(self) -> None:
        """Fire, wait up to the budget, fire again. Never on a timer."""
        while self._running:
            try:
                frame_t, jpeg = await self._slot.get()
            except asyncio.CancelledError:
                return

            started = time.perf_counter()
            self.calls += 1
            try:
                raw = await asyncio.wait_for(self._client.tag(jpeg), self._budget)
            except (asyncio.TimeoutError, TimeoutError):
                # wait_for has already cancelled the call. Abandon the result even if
                # it lands a millisecond later — by then the tick it belonged to is
                # written and the next call is more valuable than this one.
                self.overruns += 1
                self._outcomes.append("overrun")
                continue
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - a bad call must not stop the clock
                self.errors += 1
                self._outcomes.append("error")
                log.warning("T0 VLM call failed: %s", exc)
                continue

            self._latencies.append(time.perf_counter() - started)
            self._outcomes.append("returned")
            self.returned += 1
            # Overwrite any previous unconsumed result: the newer observation wins.
            self._pending = (coerce(raw), frame_t)

    # -- observability --

    @property
    def coverage(self) -> float:
        """Fraction of ticks that carried an `ai` block. §2.4 expects 0.5-0.8."""
        return self.ticks_served / self.ticks_asked if self.ticks_asked else 0.0

    def stats_line(self) -> str:
        lat = sorted(self._latencies)
        p50 = lat[len(lat) // 2] if lat else 0.0
        return (
            f"coverage={self.coverage:6.1%} "
            f"ticks={self.ticks_served}/{self.ticks_asked} "
            f"calls={self.calls} ok={self.returned} "
            f"overrun={self.overruns} err={self.errors} "
            f"p50={p50 * 1000:.0f}ms frames_dropped={self._slot.dropped}"
        )

    def stats(self) -> dict[str, int | float]:
        """Structured live-demo health for the most recent calls."""
        lat = sorted(list(self._latencies)[-50:])

        def percentile(fraction: float) -> float:
            if not lat:
                return 0.0
            return lat[min(len(lat) - 1, int((len(lat) - 1) * fraction))] * 1000

        return {
            "calls": self.calls,
            "returned": self.returned,
            "overruns": self.overruns,
            "errors": sum(outcome == "error" for outcome in self._outcomes),
            "latency_p50_ms": round(percentile(0.50), 1),
            "latency_p90_ms": round(percentile(0.90), 1),
            "budget_s": self._budget,
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

    async def tag(self, jpeg: bytes) -> dict[str, Any]:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=self._schema,
            # Flash-Lite will happily spend the whole budget thinking. We have 1 second
            # and the task is "report what is plainly visible", so buy latency instead.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            max_output_tokens=32000,
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
        return json.loads(resp.text or "{}")

    async def aclose(self) -> None:
        return None


DEFAULT_FAKE_FIELDS: dict[str, Any] = {"scene": "office", "activity": "seated", "conf": 0.9}


class FakeClient:
    """A latency simulator for development and for proving the drop rule.

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
        # timer firing every second into a slow API stacks up concurrent calls; a
        # self-scheduling tagger holds this at exactly 1 no matter how slow the API is.
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
