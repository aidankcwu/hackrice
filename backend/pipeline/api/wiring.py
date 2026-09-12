"""Construct and run the complete simulated pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from ..actions.speech import SpeechLimiter, default_speak_fn, set_speak_fn, spoken
from ..actions.questions import QuestionManager
from ..bus import TickBus
from ..config import Settings
from ..db import Database, day_key
from ..episodes.builder import EpisodeBuilder
from ..frames import FrameStore, InMemoryFrameStore
from ..gate.gate import TriggerGate
from ..gate.triggers import CallableBiometricFeed, default_triggers
from ..models import Tick
from ..reasoner.client import make_answer_parser, make_client
from ..reasoner.reasoner import Reasoner
from ..scoring.scorer import Scorer
from ..seed.generate import resting_hr_for, seed_database, seven_day_summary
from ..seed.biometrics import seed_biometric_series
from ..sim.scenario import DEFAULT_SCENARIO, Scenario
from ..sim.source import SimSource

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Clock:
    """Translate timestamps between the real device and simulation clocks."""

    wall_start: float
    sim_start_t: float
    speed: float

    def wall_to_tick(self, wall_t: float) -> float:
        return self.sim_start_t + (wall_t - self.wall_start) * self.speed

    def tick_to_wall(self, tick_t: float) -> float:
        return self.wall_start + (tick_t - self.sim_start_t) / self.speed


class Pipeline:
    """The live objects and lifecycle of one pipeline instance."""

    def __init__(self, *, settings: Settings, source_name: str, reasoner_mode: str,
                 speed: float, db: Database, frame_store: FrameStore,
                 bus: TickBus, scorer: Scorer, speech: SpeechLimiter,
                 reasoner: Reasoner, episodes: EpisodeBuilder, gate: TriggerGate,
                 questions: QuestionManager, source: SimSource | None,
                 capture=None, clock: Clock | None = None) -> None:
        self.settings = settings
        self.source_name = source_name
        self.reasoner_mode = reasoner_mode
        self.speed = speed
        self.db = db
        self.frame_store = frame_store
        self.bus = bus
        self.scorer = scorer
        self.speech = speech
        self.reasoner = reasoner
        self.episodes = episodes
        self.gate = gate
        self.questions = questions
        self.source = source
        self.capture = capture
        # Live capture uses wall time unchanged; simulation scales its own clock.
        if clock is None:
            assert source is not None
            clock = Clock(wall_start=source.start_t, sim_start_t=source.start_t,
                          speed=speed)
        self.clock = clock
        self.biometrics_start_t = clock.sim_start_t
        self.started_at: float | None = None
        self.last_tick: Tick | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._score_event = asyncio.Event()
        self._last_score_t: float | None = None
        self._stopping = False
        # Rolling (tick_t, has_ai) samples for the 60 s coverage figure. Bounded so a
        # burst of out-of-order timestamps cannot grow it; expired on read as well as
        # on write so it goes stale honestly when ticks stop.
        self._ai_window: deque[tuple[float, bool]] = deque(maxlen=400)
        self._last_tick_wall: float | None = None
        self._t1_error_snapshot = (0, 0)
        self._t1_snapshot_at = time.monotonic()

    async def start(self) -> None:
        if self._tasks:
            return
        self.started_at = time.time()
        self._stopping = False
        downstream_sub = self.bus.subscribe("downstream")

        async def pump() -> None:
            assert self.source is not None
            async for tick in self.source:
                self.bus.publish(tick)
                self.frame_store.expire(tick.t)

        async def downstream() -> None:
            async for tick in downstream_sub:
                self._ai_window.append((tick.t, tick.ai is not None))
                self._last_tick_wall = time.time()
                while self._ai_window and tick.t - self._ai_window[0][0] > 60.0:
                    self._ai_window.popleft()
                self.db.insert_tick(tick)
                self.episodes.on_tick(tick)
                self.gate.on_tick(tick)
                self.questions.expire(tick.t)
                self.last_tick = tick
                self._score_event.set()
                if (tick.seq + 1) % 30 == 0:
                    stats = self.db.stats()
                    log.info("status: ticks=%d decisions=%d ai_coverage=%.2f",
                             stats["tick_count"], stats["decision_count"],
                             stats["ai_tick_count"] / stats["tick_count"]
                             if stats["tick_count"] else 0.0)

        async def score_periodically() -> None:
            while True:
                await self._score_event.wait()
                self._score_event.clear()
                if self._stopping:
                    return
                tick = self.last_tick
                if tick is None:
                    continue
                if self._last_score_t is None or tick.t - self._last_score_t >= 15.0:
                    self._last_score_t = tick.t
                    today = day_key(tick.t)
                    await asyncio.to_thread(self.scorer.score_all, today,
                                            self.scorer.week_days(today))

        self._tasks = [
            asyncio.create_task(downstream(), name="pipeline-downstream"),
            asyncio.create_task(score_periodically(), name="pipeline-scorer"),
        ]
        self.questions.start()
        if self.capture is not None:
            await self.capture.start()
        else:
            self._tasks.insert(0, asyncio.create_task(pump(), name="pipeline-pump"))

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._stopping = True
        await self.questions.stop()
        self._score_event.set()
        if self.capture is not None:
            await self.capture.stop()
        self.bus.close()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for _ in range(100):
            if not self.reasoner.busy:
                break
            await asyncio.sleep(0)
        if self.last_tick is not None:
            today = day_key(self.last_tick.t)
            with contextlib.suppress(Exception):
                self.scorer.score_all(today, self.scorer.week_days(today))
        self.db.close()

    def status(self) -> dict[str, object]:
        stats = self.db.stats()
        tick_count = stats["tick_count"]
        result: dict[str, object] = {
            "demo_mode": self.settings.demo_mode,
            "source": self.source_name,
            "uptime_s": max(0.0, time.time() - self.started_at)
            if self.started_at is not None else 0.0,
            "tick_count": tick_count,
            "ai_coverage": stats["ai_tick_count"] / tick_count if tick_count else 0.0,
            "t1_busy": self.reasoner.busy,
            "dropped_escalations": self.reasoner.dropped_busy + self.gate.dropped,
            "last_tick_t": self.last_tick.t if self.last_tick is not None else None,
            "reasoner_mode": self.reasoner_mode,
            "speed": self.speed,
            # The stream's cadence, so the dashboard can label the tick strip
            # without assuming 1 Hz (SPEC §2.1 vs the glasses' 1.5 s).
            "tick_interval_s": self.settings.tick_interval_s,
            "gate": self.gate.stats(),
            "questions": self.questions.stats(),
            "speech_spoken": len(spoken),
            "health": self._health(),
        }
        if self.capture is not None:
            result["capture"] = self.capture.stats()
        return result

    def _health(self) -> dict[str, object]:
        phone = self.capture.link.stats() if self.capture is not None else None
        # Ticks stopped -> the window is history, not "now". Wall clock on purpose:
        # this is about whether the process is alive, not about the tick clock.
        # Fall back to start time, not just the last tick: a T0 that never produced a
        # single tick leaves _last_tick_wall at None, and "dead since boot" is exactly
        # the case the alarm is for.
        reference = (self._last_tick_wall if self._last_tick_wall is not None
                     else self.started_at)
        ticks_stale = reference is not None and time.time() - reference > 60.0
        window = [] if ticks_stale else list(self._ai_window)
        coverage = (sum(has_ai for _, has_ai in window) / len(window)) if window else 0.0
        tagger = self.capture.tagger.stats() if self.capture is not None else None
        t0 = {"ai_coverage_60s": coverage, "tagger": tagger}
        t1 = self.reasoner.stats()
        if self.capture is None:
            speech: dict[str, object] = {"mode": "console"}
        else:
            speech = self.capture.speech_stats()
        problems = health_problems(
            source=self.source_name, phone=phone, ai_coverage=coverage,
            ai_ticks=len(window), tagger=tagger, t1=t1,
            t1_error_snapshot=self._t1_error_snapshot, speech=speech,
            ticks_stale=ticks_stale,
        )
        now = time.monotonic()
        if now - self._t1_snapshot_at >= 300:
            self._t1_error_snapshot = (int(t1["dropped_error"]), int(t1["dropped_timeout"]))
            self._t1_snapshot_at = now
        return {"phone": phone, "t0": t0, "t1": t1, "speech": speech,
                "ok": not problems, "problems": problems}


def health_problems(*, source: str, phone, ai_coverage: float, ai_ticks: int,
                    tagger, t1, t1_error_snapshot, speech,
                    ticks_stale: bool = False) -> list[str]:
    """Apply the documented demo rules; each problem name maps to one visible remedy.

    phone_disconnected  glasses source and no phone socket        -> tap Connect / check IP
    no_packets_10s      phone socket up but no frame in 10 s      -> glasses not streaming
    no_ticks_60s        T0 has not produced a tick in 60 s        -> capture source is dead
    ai_coverage_low     <50% of the last 60 s of ticks carry `ai` -> Gemini slow / key
    vlm_errors          >5 VLM errors in the last 50 calls        -> Gemini key / quota
    t1_errors           reasoner errors or timeouts since snapshot -> OpenAI key / network
    tts_failing         last ElevenLabs call failed               -> key / voice / quota
    """
    problems: list[str] = []
    if source == "glasses" and (phone is None or phone["connected"] == 0):
        problems.append("phone_disconnected")
    if source == "glasses" and phone and phone["connected"]:
        age = phone.get("latest_age_s")
        since = phone.get("connected_for_s")
        # Either the last frame is old, or there has never been one and the socket has
        # been up long enough that pings alone are keeping it "connected".
        if (age is not None and age > 10) or (age is None and since is not None and since > 10):
            problems.append("no_packets_10s")
    if ticks_stale:
        problems.append("no_ticks_60s")
    if ai_ticks >= 20 and ai_coverage < 0.5:
        problems.append("ai_coverage_low")
    if tagger is not None and tagger["errors"] > 5:
        problems.append("vlm_errors")
    if (t1["dropped_error"], t1["dropped_timeout"]) > tuple(t1_error_snapshot):
        problems.append("t1_errors")
    if speech.get("last_error"):
        problems.append("tts_failing")
    return problems


def build_pipeline(settings: Settings, *,
                   source: Literal["sim", "glasses", "webcam", "replay"],
                   reasoner_mode: Literal["openai", "fake"], speed: float,
                   seed_db: bool = True, scenario: Scenario | None = None,
                   dir: str | None = None, loop: bool = False, camera: int = 0,
                   vlm: Literal["gemini", "fake", "off"] = "gemini",
                   flow: str | None = None) -> Pipeline:
    """Build the graph in dependency order without starting any tasks."""
    db = Database(settings.db_path).connect().init_schema()
    bus = TickBus()
    capture = None
    sim_source = None
    if source == "sim":
        frame_store: FrameStore = InMemoryFrameStore(ttl_s=settings.frame_ttl_s)
    else:
        from ..capture.bridge import LongevityCapture
        from ..capture.frames import RingFrameStore
        from ..capture.speak import make_speak_fn
        capture = LongevityCapture(
            settings, source=source, our_bus=bus, dir=dir, speed=speed, loop=loop,
            camera=camera, vlm=vlm, flow=flow,
        )
        missing = []
        if not callable(getattr(capture, "send_question", None)):
            missing.append("capture.send_question")
        if not callable(getattr(capture.link, "supports", None)):
            missing.append("capture.link.supports")
        if not hasattr(capture.link, "on_answer"):
            missing.append("capture.link.on_answer")
        if missing:
            db.close()
            raise RuntimeError(
                "capture bridge lacks the ask/answer interface: " + ", ".join(missing)
            )
        frame_store = RingFrameStore(capture.ring)
        speak_fn = make_speak_fn(capture.link, settings)
        capture._speech_stats = speak_fn.stats  # type: ignore[attr-defined]
        set_speak_fn(speak_fn)
    end_day = day_key(time.time())
    if seed_db:
        seed_database(db, end_day=end_day)
    scorer = Scorer(db)
    timings = settings.timings
    speech = SpeechLimiter(timings.speech_min_gap, timings.speech_max_per_hour)
    client = make_client(settings, reasoner_mode)
    parser = make_answer_parser(settings, reasoner_mode)

    async def console_send(question) -> bool:
        log.info("ASK: %s", question.question)
        return True

    if capture is None:
        send = console_send
        supports_ask = lambda: True
        has_transport = lambda: True
    else:
        async def capture_send(question) -> bool:
            return bool(await capture.send_question(question))
        send = capture_send
        supports_ask = lambda: bool(capture.link.supports("ask"))
        has_transport = lambda: bool(getattr(capture.link, "clients", ()))

    # ``clock`` is assigned below before the manager can be started or queried.
    questions = QuestionManager(
        db, speech, timings, send=send, supports_ask=supports_ask,
        has_transport=has_transport, parser=parser,
        now_fn=lambda: clock.wall_to_tick(time.time()),
    )
    reasoner = Reasoner(db, frame_store, client, speech, settings,
                        seven_day_summary=lambda: seven_day_summary(db, end_day),
                        parser=parser, questions=questions)
    questions.reasoner = reasoner
    if capture is not None:
        setattr(capture.link, "on_answer", questions.on_answer)
    episodes = EpisodeBuilder(db, timings)
    # SPEC §14.3: the biometric_anomaly trigger reads the seeded wearable HR
    # series on the tick clock; the gate never imports the seed modules.
    # A live wearable pushing into /api/wearables/ingest lands in the same
    # table with origin='live' and wins for any window it covers, so the feed
    # needs no branch: seeded until something real shows up.
    feed = CallableBiometricFeed(
        db.biometric_series,
        lambda: resting_hr_for(db, end_day),
        db.latest_biometric,
    )
    gate = TriggerGate(default_triggers(timings, settings.demo_mode, feed=feed,
                                       keyword_triggers=settings.keyword_triggers), timings, db,
                       episodes, reasoner.try_escalate, settings.demo_mode, feed=feed)
    if source == "sim":
        set_speak_fn(default_speak_fn)
        sim_source = SimSource(scenario or DEFAULT_SCENARIO, frame_store, speed=speed,
                               interval_s=settings.tick_interval_s)
        clock = Clock(sim_source.start_t, sim_source.start_t, speed)
        biometric_start = sim_source.start_t
    else:
        now = time.time()
        clock = Clock(now, now, 1.0)
        biometric_start = now
    if seed_db:
        seed_biometric_series(db, biometric_start)
    return Pipeline(settings=settings, source_name=source,
                    reasoner_mode=reasoner_mode, speed=speed, db=db,
                    frame_store=frame_store, bus=bus, scorer=scorer, speech=speech,
                    reasoner=reasoner, episodes=episodes, gate=gate,
                    questions=questions, source=sim_source,
                    capture=capture, clock=clock)
