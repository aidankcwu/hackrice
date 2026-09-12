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
