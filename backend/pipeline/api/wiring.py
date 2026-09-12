"""Construct and run the complete simulated pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Literal

from ..actions.speech import SpeechLimiter, default_speak_fn, set_speak_fn, spoken
from ..bus import TickBus
from ..config import Settings
from ..db import Database, day_key
from ..episodes.builder import EpisodeBuilder
from ..frames import FrameStore, InMemoryFrameStore
from ..gate.gate import TriggerGate
from ..gate.triggers import CallableBiometricFeed, default_triggers
from ..models import Tick
from ..reasoner.client import make_client
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
                 source: SimSource | None, capture=None, clock: Clock | None = None) -> None:
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
                self.db.insert_tick(tick)
                self.episodes.on_tick(tick)
                self.gate.on_tick(tick)
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
        if self.capture is not None:
            await self.capture.start()
        else:
            self._tasks.insert(0, asyncio.create_task(pump(), name="pipeline-pump"))

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._stopping = True
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
            "speech_spoken": len(spoken),
        }
        if self.capture is not None:
            result["capture"] = self.capture.stats()
        return result


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
        frame_store = RingFrameStore(capture.ring)
        set_speak_fn(make_speak_fn(capture.link, settings))
    end_day = day_key(time.time())
    if seed_db:
        seed_database(db, end_day=end_day)
    scorer = Scorer(db)
    timings = settings.timings
    speech = SpeechLimiter(timings.speech_min_gap, timings.speech_max_per_hour)
    client = make_client(settings, reasoner_mode)
    reasoner = Reasoner(db, frame_store, client, speech, settings,
                        seven_day_summary=lambda: seven_day_summary(db, end_day))
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
                    source=sim_source, capture=capture, clock=clock)
