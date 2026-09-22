"""Construct and run the complete simulated pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

from ..actions.autopilot import Autopilot, config_act_veto
from ..actions.handlers import make_act_sender
from ..actions.speech import SpeechLimiter, default_speak_fn, set_speak_fn, spoken
from ..actions.questions import QuestionManager
from ..bus import TickBus
from ..config import Settings
from ..conversation.agent import ConversationAgent
from ..conversation.client import make_voice_client
from ..db import Database, day_key
from ..episodes.builder import EpisodeBuilder
from ..frames import FrameStore, InMemoryFrameStore
from ..gate.gate import TriggerGate
from ..gate.triggers import CallableBiometricFeed, default_triggers
from ..models import Tick
from ..protocol.adherence import AdherenceMatcher
from ..reasoner.client import make_answer_parser, make_client
from ..reasoner.reasoner import Reasoner
from ..scoring.scorer import Scorer
from ..seed.generate import resting_hr_for, seed_database, seven_day_summary
from ..seed.biometrics import seed_biometric_series
from ..session import SessionManager
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


#: A session opens on the first tick of a stream and closes once the ticks have
#: stopped for this long -- the wearer took the glasses off, or the phone
#: stopped sending. Longer than a dropped frame or two, shorter than a pause
#: anyone would sit through in a demo.
AUTO_SESSION_IDLE_S = 25.0


class Pipeline:
    """The live objects and lifecycle of one pipeline instance."""

    def __init__(self, *, settings: Settings, source_name: str, reasoner_mode: str,
                 speed: float, db: Database, frame_store: FrameStore,
                 bus: TickBus, scorer: Scorer, speech: SpeechLimiter,
                 reasoner: Reasoner, episodes: EpisodeBuilder, gate: TriggerGate,
                 questions: QuestionManager, source: SimSource | None,
                 conversation: ConversationAgent | None = None,
                 capture=None, clock: Clock | None = None,
                 adherence: AdherenceMatcher | None = None,
                 autopilot: Autopilot | None = None) -> None:
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
        #: The voice agent. The clerk hands topics to it; it owns the mouth.
        self.conversation = conversation
        self.source = source
        self.capture = capture
        #: Closes dose windows on the tick clock (PLAN 2.2); its sightings
        #: arrive through ``reasoner.on_evidence``.
        self.adherence = adherence
        #: The two rule-based acts (PLAN 4.1), checked on the tick clock.
        self.autopilot = autopilot
        # Live capture uses wall time unchanged; simulation scales its own clock.
        if clock is None:
            assert source is not None
            clock = Clock(wall_start=source.start_t, sim_start_t=source.start_t,
                          speed=speed)
        self.clock = clock
        self.biometrics_start_t = clock.sim_start_t
        self.started_at: float | None = None
        self.background_tasks: set = set()
        self.last_tick: Tick | None = None
        self.sessions = SessionManager(
            db, gate, speech, episodes,
            self._session_clock,
            reasoner=reasoner,
        )
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
        #: ``speak_fn.warm`` from the capture branch: opens the ElevenLabs
        #: connection at start so the first cue of the demo does not also pay
        #: the TLS handshake. Never raises, spends no credit.
        self._speech_warm = None

    def _session_clock(self) -> float:
        """The tick clock, advanced by real elapsed time when ticks have stalled.

        Sessions are stamped on the tick clock so a recap's window lines up
        with the ticks inside it. But ``last_tick.t`` freezes the instant the
        stream stalls, and a session started and ended across a stall was
        stamped with the same moment twice -- a zero-length window, and a log
        entry covering nothing. Seen live: the phone stopped sending while its
        socket stayed open, and an eight-second session recorded 0.0 s.
        """

        tick = self.last_tick
        if tick is None or self._last_tick_wall is None:
            return self.clock.wall_to_tick(time.time())
        drift = max(0.0, time.time() - self._last_tick_wall)
        return tick.t + drift * self.clock.speed

    def spawn_recap(self, session_id: str) -> None:
        """Generate one session's recap on a task. Never raises into the caller."""

        from ..recap.builder import build_recap  # local: recap imports scoring

        async def generate() -> None:
            try:
                await build_recap(self, session_id=session_id, speak=False)
            except Exception:  # noqa: BLE001 -- a failed recap must not be silent
                log.exception("session %s ended but its recap failed", session_id)

        try:
            task = asyncio.create_task(generate())
        except RuntimeError:  # pragma: no cover - no loop (unit tests)
            log.error("cannot generate a recap for %s: no running loop", session_id)
            return
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    def _auto_session_open(self) -> None:
        """Open a session on the first tick of a stream, if none is open.

        The glasses are the control: a session is exactly the stretch the
        wearer was streaming. Nobody should have to remember a button on a
        dashboard, and in a demo nobody does.
        """

        if not getattr(self.settings, "auto_session", True):
            return
        try:
            if self.sessions.current() is not None:
                return
            session = self.sessions.start(time.strftime("%H:%M", time.localtime()))
            log.info("session: %s auto-started on the first frame", session.id)
        except Exception:  # pragma: no cover - defensive
            log.exception("could not auto-start a session")

    def _auto_session_close(self) -> None:
        """Close the open session once the frames have stopped, and recap it."""

        if not getattr(self.settings, "auto_session", True):
            return
        if self._last_tick_wall is None:
            return
        if time.time() - self._last_tick_wall < AUTO_SESSION_IDLE_S:
            return
        try:
            if self.sessions.current() is None:
                return
            ended = self.sessions.end()
        except Exception:  # pragma: no cover - defensive
            log.exception("could not auto-end the session")
            return
        if ended is None:
            return
        log.info("session: %s auto-ended after %.0f s without a frame",
                 ended.id, AUTO_SESSION_IDLE_S)
        self.spawn_recap(ended.id)

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
                # Publish-on-landing re-sends the newest tick with its ai block
                # once Gemini lands (capture.bridge): same tick, now with eyes.
                resend = self.last_tick is not None and tick.tick_id == self.last_tick.tick_id
                self._last_tick_wall = time.time()
                self.last_tick = tick
                self._auto_session_open()
                if self._ai_window and self._ai_window[-1][0] == tick.t:
                    # A re-send of the newest tick with its ai attached
                    # (publish-on-landing): one tick, not two, in the coverage.
                    self._ai_window[-1] = (tick.t, tick.ai is not None)
                else:
                    self._ai_window.append((tick.t, tick.ai is not None))
                while self._ai_window and tick.t - self._ai_window[0][0] > 60.0:
                    self._ai_window.popleft()
                self.db.insert_tick(tick)  # INSERT OR REPLACE: the re-send wins
                # The episode builder knows a re-send by its tick_id and only
                # adds the new evidence; the gate replaces it in its window.
                self.episodes.on_tick(tick)
                self.gate.on_tick(tick)
                if self.adherence is not None:
                    self.adherence.on_tick(tick.t)
                if self.autopilot is not None:
                    self.autopilot.on_tick(tick.t)
                if not resend:
                    self.questions.expire(tick.t)
                self.last_tick = tick
                self._score_event.set()
                if not resend and (tick.seq + 1) % 30 == 0:
                    stats = self.db.stats()
                    log.info("status: ticks=%d decisions=%d ai_coverage=%.2f",
                             stats["tick_count"], stats["decision_count"],
                             stats["ai_tick_count"] / stats["tick_count"]
                             if stats["tick_count"] else 0.0)

        async def auto_session_watchdog() -> None:
            while True:
                await asyncio.sleep(5.0)
                if self._stopping:
                    return
                self._auto_session_close()

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
            asyncio.create_task(auto_session_watchdog(), name="pipeline-auto-session"),
        ]
        self.questions.start()
        # Only on the real glasses: replay and webcam runs (and their tests)
        # have no phone to speak to and must not reach the network.
        if self._speech_warm is not None and self.source_name == "glasses":
            warm = asyncio.create_task(self._speech_warm(), name="speech-warm")
            self.background_tasks.add(warm)
            warm.add_done_callback(self.background_tasks.discard)
        if self.capture is not None:
            await self.capture.start()
        else:
            self._tasks.insert(0, asyncio.create_task(pump(), name="pipeline-pump"))

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._stopping = True
        await self.questions.stop()
        if self.conversation is not None:
            await self.conversation.stop()
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
        session = self.sessions.current()
        session_status = None
        if session is not None:
            now = self.last_tick.t if self.last_tick is not None else time.time()
            session_status = {
                **session.model_dump(),
                "elapsed_s": max(0.0, now - session.started_t),
            }
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
            "conversation": (None if self.conversation is None
                             else self.conversation.stats()),
            "speech_spoken": len(spoken),
            "health": self._health(),
            "session": session_status,
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
    speech_warm = None
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
        speech_warm = getattr(speak_fn, "warm", None)
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
    adherence = AdherenceMatcher(db, speech)
    reasoner.on_evidence = adherence.on_evidence
    # The third agent (docs/CONVERSATION_DESIGN.md). Built after the reasoner
    # because it borrows `_extend_episode_label`, and attached back onto it so
    # `speak`/`ask` become hand-offs rather than utterances.
    conversation = ConversationAgent(
        db, frame_store, make_voice_client(settings, reasoner_mode), speech,
        settings, questions=questions, reasoner=reasoner,
        now_fn=lambda: clock.wall_to_tick(time.time()),
        mouth_guard=settings.mouth_busy_guard,
    )
    reasoner.conversation = conversation
    questions.conversation = conversation
    questions.reasoner = reasoner
    if capture is not None:
        setattr(capture.link, "on_answer", questions.on_answer)
        # `act` goes down the socket speech uses; `act_result` comes back up it.
        reasoner.handler.send_act = make_act_sender(capture.link)
        setattr(capture.link, "on_act_result", reasoner.handler.on_act_result)
    # What may act is config (AUTOPILOT_ACTS / AUTOPILOT_QUIET_DAYS), not persona.
    reasoner.handler.act_veto = config_act_veto(settings.autopilot_acts,
                                                settings.autopilot_quiet_days)
    autopilot = Autopilot(db, reasoner.handler,
                          outdoor_target_min=settings.outdoor_target_min,
                          wind_down_hhmm=settings.wind_down_hhmm,
                          lat=settings.air_lat, lon=settings.air_lon)
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
    # Persona cues skip the clerk on the way to the mouth: the gate hands them
    # to the voice agent through `fast_path` on the tick they appear, and the
    # clerk wakes beside it only to write the moment down. Everything else
    # still goes gate -> clerk -> (maybe) voice agent.
    # Kill switches (config.Settings): FAST_PATH=0 leaves cues on the clerk
    # path (fast_path=None is the gate's own "no fast path" branch), and
    # CUE_TRIGGER=0 removes the one-tick cue trigger altogether. With
    # FAST_PATH=0 the cue also loses its global-gap exemption: that exemption
    # is justified only because a cue skips the clerk's queue. One line at
    # startup says which were in effect, so a rehearsal log is never ambiguous.
    log.info("switches: %s", settings.switches_line())
    gate = TriggerGate(default_triggers(timings, settings.demo_mode, feed=feed,
                                       keyword_triggers=settings.keyword_triggers,
                                       cues=settings.cue_trigger,
                                       cue_bypass_gap=settings.fast_path), timings, db,
                       episodes, reasoner.try_escalate, settings.demo_mode, feed=feed,
                       fast_path=reasoner.fast_path if settings.fast_path else None)
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
    pipeline = Pipeline(settings=settings, source_name=source,
                        reasoner_mode=reasoner_mode, speed=speed, db=db,
                        frame_store=frame_store, bus=bus, scorer=scorer, speech=speech,
                        reasoner=reasoner, episodes=episodes, gate=gate,
                        questions=questions, conversation=conversation,
                        source=sim_source, capture=capture, clock=clock,
                        adherence=adherence, autopilot=autopilot)
    pipeline._speech_warm = speech_warm
    # Re-warm on every conversation open as well, on the real glasses only
    # (replay and webcam runs have no phone and must not reach the network).
    conversation.speech_warm = speech_warm if source == "glasses" else None
    return pipeline
