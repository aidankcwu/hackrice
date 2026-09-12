import asyncio

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
            "t1_busy", "dropped_escalations", "last_tick_t",
        }
        assert expected <= pipeline.status().keys()
    finally:
        await pipeline.stop()
