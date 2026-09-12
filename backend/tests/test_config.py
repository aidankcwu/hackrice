from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from pipeline.config import Settings, Timings


def test_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.t1_model == "gpt-5.4-mini"
    assert s.demo_mode is True
    assert s.db_path == Path("./data/pipeline.db")
    assert s.frame_ttl_s == 90.0  # SPEC §2.5


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("T1_MODEL", "gpt-5.4")
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("DB_PATH", "/tmp/x.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.t1_model == "gpt-5.4"
    assert s.demo_mode is False
    assert s.db_path == Path("/tmp/x.db")
    assert s.openai_api_key == "sk-test"


def test_timings_selected_by_demo_mode() -> None:
    assert Settings(_env_file=None, demo_mode=True).timings == Timings.demo()  # type: ignore[call-arg]
    assert Settings(_env_file=None, demo_mode=False).timings == Timings.production()  # type: ignore[call-arg]


def test_demo_shortens_every_duration() -> None:
    """SPEC §6: DEMO_MODE shortens every cooldown and rate limit."""

    prod, demo = Timings.production(), Timings.demo()
    for f in fields(Timings):
        p, d = getattr(prod, f.name), getattr(demo, f.name)
        if f.name in ("t1_max_concurrent", "speech_max_per_hour"):
            continue  # a cap, not a duration
        assert d <= p, f"{f.name}: demo {d} should not exceed production {p}"

    # Triggers must be able to fire more than once inside a four-minute demo.
    assert demo.trigger_cooldown_default < 60
    assert demo.global_escalation_min_gap < 30
    # And T1 concurrency stays capped at 1 either way (SPEC §5.4).
    assert prod.t1_max_concurrent == demo.t1_max_concurrent == 1


def test_env_example_matches_settings_fields() -> None:
    example = Path(__file__).resolve().parents[1] / ".env.example"
    keys = {
        line.split("=", 1)[0].strip().lower()
        for line in example.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert keys <= set(Settings.model_fields)
