import asyncio
import io
import time

from PIL import Image

from pipeline.api.wiring import build_pipeline
from pipeline.config import Settings


async def _run_to(pipeline, count: int) -> None:
    await pipeline.start()
    for _ in range(1_000):
        if pipeline.db.stats()["tick_count"] >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"pipeline did not reach {count} ticks")


async def test_pipeline_wires_all_components(tmp_path):
    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "pipeline.db"),
        source="sim", reasoner_mode="fake", speed=200,
    )
    await _run_to(pipeline, 250)
    try:
        decisions = pipeline.db.list_decisions()
        assert pipeline.db.stats()["tick_count"] >= 200
        assert len({row.trigger for row in decisions}) >= 3
        assert any(not row.dropped and
                   any(action["type"] == "annotate" for action in row.actions)
                   for row in decisions)
        day = pipeline.scorer.local_day(pipeline.last_tick.t)
        assert pipeline.db.today_summary_lines(day)
        assert pipeline.reasoner.evidence.count() > 0
        assert pipeline.db.list_episodes(day)
        pipeline.scorer.score_all(day, pipeline.scorer.week_days(day))
        assert pipeline.db.list_scores("daily")
        assert pipeline.db.list_scores("weekly")
        expected = {
            "demo_mode", "source", "uptime_s", "tick_count", "ai_coverage",
            "t1_busy", "dropped_escalations", "last_tick_t", "tick_interval_s",
        }
        status = pipeline.status()
        assert expected <= status.keys()
        assert status["tick_interval_s"] == 1.5
    finally:
        await pipeline.stop()


async def test_pipeline_wires_replay_capture_end_to_end(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    base = int(time.time() * 1000)
    for i in range(6):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (i * 30, 60, 100)).save(buf, "JPEG")
        (corpus / f"frame_{base + i * 1000}.jpg").write_bytes(buf.getvalue())

    pipeline = build_pipeline(
        Settings(db_path=tmp_path / "replay.db", tick_interval_s=1.0),
        source="replay", dir=str(corpus), loop=True, vlm="fake",
        reasoner_mode="fake", speed=50,
    )
    await _run_to(pipeline, 20)
    try:
        assert pipeline.db.stats()["tick_count"] >= 15
        assert pipeline.status()["source"] == "replay"
        assert "capture" in pipeline.status()
    finally:
        await pipeline.stop()


def test_the_source_runs_at_the_configured_cadence(tmp_path):
    """SPEC §2.1 says 1 Hz; the glasses emit every 1.5 s and the sim follows."""

    default = build_pipeline(Settings(db_path=tmp_path / "cadence.db"),
                             source="sim", reasoner_mode="fake", speed=1)
    try:
        assert default.settings.tick_interval_s == 1.5
        assert default.source.interval_s == 1.5
        assert default.gate.timings.tick_interval_s == 1.5
        assert default.episodes.params.ai_max_age_ms == 3750
        a, b = default.source.next_tick(), default.source.next_tick()
        assert b.t - a.t == 1.5
    finally:
        default.db.close()

    fast = build_pipeline(Settings(db_path=tmp_path / "fast_tick.db",
                                   tick_interval_s=1.0),
                          source="sim", reasoner_mode="fake", speed=1)
    try:
        assert fast.source.interval_s == 1.0
        assert fast.episodes.params.ai_max_age_ms == 3000
    finally:
        fast.db.close()


def test_clock_is_identity_at_speed_one_and_scales_at_speed_ten(tmp_path):
    one = build_pipeline(Settings(db_path=tmp_path / "one.db"), source="sim",
                         reasoner_mode="fake", speed=1)
    try:
        wall = one.clock.wall_start + 30
        assert one.clock.wall_to_tick(wall) == wall
        assert one.clock.tick_to_wall(wall) == wall
    finally:
        one.db.close()

    fast = build_pipeline(Settings(db_path=tmp_path / "fast.db"), source="sim",
                          reasoner_mode="fake", speed=10)
    try:
        last_tick_t = fast.clock.wall_to_tick(fast.clock.wall_start + 60)
        sample_t = fast.clock.wall_to_tick(fast.clock.wall_start + 30)
        assert abs((last_tick_t - sample_t) - 300) <= 1
    finally:
        fast.db.close()


def test_the_gate_hands_persona_cues_to_the_reasoners_fast_path(tmp_path):
    pipeline = build_pipeline(Settings(db_path=tmp_path / "fast.db"), source="sim",
                              reasoner_mode="fake", speed=1, seed_db=False)
    try:
        assert pipeline.gate.fast_path == pipeline.reasoner.fast_path
        assert pipeline.reasoner.conversation is pipeline.conversation
        assert pipeline.gate.triggers[0].name == "cue", "cues are looked at first"
        assert pipeline.gate.triggers[0].bypass_gap
        # The clerk's deadline follows the settings: 20 s with the session
        # thread on (the default), 9 s with REASONER_THREAD=0, as before.
        assert pipeline.reasoner.t1_deadline_s == 20.0
        assert Settings(db_path=tmp_path / "x.db", reasoner_thread=False).clerk_deadline_s == 9.0
        assert pipeline.reasoner.thread is not None
    finally:
        pipeline.db.close()


async def test_speech_is_warmed_at_start_only_on_the_real_glasses(tmp_path):
    calls = []

    async def warm() -> bool:
        calls.append("warm")
        return True

    for name, expected in (("sim", []), ("glasses", ["warm"])):
        pipeline = build_pipeline(Settings(db_path=tmp_path / f"warm_{name}.db"),
                                  source="sim", reasoner_mode="fake", speed=1,
                                  seed_db=False)
        pipeline._speech_warm = warm
        pipeline.source_name = name
        calls.clear()
        await pipeline.start()
        try:
            for _ in range(20):
                await asyncio.sleep(0.005)
            assert calls == expected, name
        finally:
            await pipeline.stop()


async def test_a_re_sent_tick_counts_once_in_the_coverage_window(tmp_path):
    """Publish-on-landing re-sends the newest tick with its ai attached; the 60 s
    coverage figure must see one tick that has ai, not a miss and a hit."""

    from pipeline.models import AiBlock, SensorBlock, Tick

    pipeline = build_pipeline(Settings(db_path=tmp_path / "resend.db", auto_session=False),
                              source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    t = time.time()

    def make(ai: bool) -> Tick:
        return Tick(tick_id="t_resend", t=t, seq=0, sensor=SensorBlock(),
                    ai=AiBlock(age_ms=0, scene="office") if ai else None,
                    frame_ref="f_resend")

    class Twice:
        def __aiter__(self):
            return self._run()

        async def _run(self):
            yield make(False)
            yield make(True)

    pipeline.source = Twice()
    await pipeline.start()
    try:
        for _ in range(200):
            if pipeline.last_tick is not None and pipeline.last_tick.ai is not None:
                break
            await asyncio.sleep(0.005)
        assert list(pipeline._ai_window) == [(t, True)]
        assert [x.tick_id for x in pipeline.gate.window] == ["t_resend"]
    finally:
        await pipeline.stop()


async def test_a_landed_cue_is_handed_off_once_on_its_re_sent_tick(tmp_path):
    """The cue arrives on the re-send, not a tick later; the blind copy before
    it hands nothing off and the re-send is not a second tick anywhere."""

    from pipeline.models import AiBlock, SensorBlock, Tick

    pipeline = build_pipeline(Settings(db_path=tmp_path / "landed.db", auto_session=False),
                              source="sim", reasoner_mode="fake", speed=1, seed_db=False)
    handed = []

    def mouth(esc):
        handed.append(esc)
        esc.handed_off = "c_1"
        return "handed_off:c_1"

    pipeline.gate.fast_path = mouth
    t = time.time()
    treat = AiBlock(age_ms=0, in_hand="rice krispies treat", food_present=True,
                    food_type="baked_goods", caption="holding a rice krispies treat")

    def make(ai):
        return Tick(tick_id="t_landed", t=t, seq=0, sensor=SensorBlock(), ai=ai,
                    frame_ref="f_landed")

    class Twice:
        def __aiter__(self):
            return self._run()

        async def _run(self):
            yield make(None)
            await asyncio.sleep(0.02)  # let downstream take the blind copy first
            yield make(treat)

    pipeline.source = Twice()
    await pipeline.start()
    try:
        for _ in range(200):
            if handed:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        assert [(e.tick.tick_id, e.cue) for e in handed] == [("t_landed", "food:treat")]
        assert pipeline.db.stats()["tick_count"] == 1
        assert pipeline.episodes._states["caffeine_sighting"].candidate_ticks == 0
        assert pipeline.episodes._states["food_sighting"].candidate_ticks == 1
    finally:
        await pipeline.stop()


def _clear_decider_env(monkeypatch) -> None:
    from pipeline.reasoner.decider_settings import DeciderSettings

    for name in list(DeciderSettings.model_fields) + ["T1_MODEL"]:
        for key in (name.upper(), name):
            monkeypatch.delenv(key, raising=False)


def test_decider_clerk_wires_a_reasoner_without_a_decider(tmp_path, monkeypatch):
    _clear_decider_env(monkeypatch)
    monkeypatch.setenv("DECIDER", "clerk")
    pipeline = build_pipeline(Settings(db_path=tmp_path / "clerk.db"),
                              source="sim", reasoner_mode="fake", speed=1)
    try:
        assert pipeline.reasoner.decider is None
        assert pipeline.reasoner.writers is None
        assert pipeline.reasoner.decider_settings.decider == "clerk"
    finally:
        pipeline.db.close()


def test_decider_jev_wires_jev_with_no_writers_on_the_fake_client(tmp_path, monkeypatch):
    """DECIDER=jev reaches the Reasoner through build_pipeline alone. The Jev
    client is only constructed here, never called, so this makes no request."""

    from pipeline.reasoner.decider import JevDecider

    _clear_decider_env(monkeypatch)
    monkeypatch.setenv("DECIDER", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-dummy")
    settings = Settings(db_path=tmp_path / "jev.db")
    pipeline = build_pipeline(settings, source="sim", reasoner_mode="fake", speed=1)
    try:
        assert isinstance(pipeline.reasoner.decider, JevDecider)
        assert pipeline.reasoner.writers is None
        assert pipeline.reasoner.decider_settings is settings.decider
    finally:
        pipeline.db.close()


def _screen_sustained(pipeline):
    return next(t for t in pipeline.gate.triggers if t.name == "screen_sustained")


def test_gate_reads_watch_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GATE_READS_WATCH", raising=False)
    pipeline = build_pipeline(Settings(db_path=tmp_path / "off.db"), source="sim",
                              reasoner_mode="fake", speed=1, seed_db=False)
    try:
        assert pipeline.episodes.params.reads_watch is False
        assert "_flag_hits" in _screen_sustained(pipeline).predicate.__qualname__
        assert pipeline.status()["gate_reads_watch"] is False
    finally:
        pipeline.db.close()


def test_gate_reads_watch_reaches_the_gate_and_the_episode_builder(tmp_path, monkeypatch):
    monkeypatch.setenv("GATE_READS_WATCH", "1")
    pipeline = build_pipeline(Settings(db_path=tmp_path / "on.db"), source="sim",
                              reasoner_mode="fake", speed=1, seed_db=False)
    try:
        assert pipeline.episodes.params.reads_watch is True
        assert "_watch_persisted" in _screen_sustained(pipeline).predicate.__qualname__
        assert pipeline.status()["gate_reads_watch"] is True
    finally:
        pipeline.db.close()
