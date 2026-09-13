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
    assert s.tick_interval_s == 1.5  # the glasses emit a tick every 1.5 s


def test_profile_defaults() -> None:
    """The healthspan profile (``scoring/brian_score.Profile``) as shipped."""

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.profile_age, s.profile_sex, s.profile_goal, s.profile_height_m,
            s.profile_cyp1a2_slow) == (20, "M", "average", None, False)


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("T1_MODEL", "gpt-5.4")
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("DB_PATH", "/tmp/x.db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TICK_INTERVAL_S", "2.0")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.t1_model == "gpt-5.4"
    assert s.demo_mode is False
    assert s.db_path == Path("/tmp/x.db")
    assert s.openai_api_key == "sk-test"
    assert s.tick_interval_s == 2.0
    assert s.timings.tick_interval_s == 2.0


def test_keyword_triggers_json_env_and_invalid_fallback(monkeypatch, caplog) -> None:
    monkeypatch.setenv(
        "KEYWORD_TRIGGERS_JSON",
        '[{"name":"custom","keywords":["toast"],"cooldown_s":12}]',
    )
    assert Settings(_env_file=None).keyword_triggers == [  # type: ignore[call-arg]
        {"name": "custom", "keywords": ["toast"], "cooldown_s": 12}
    ]

    monkeypatch.setenv("KEYWORD_TRIGGERS_JSON", "not json")
    with caplog.at_level("WARNING"):
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.keyword_triggers[0]["name"] == "rice_krispy"
    assert "using default" in caplog.text


def test_timings_selected_by_demo_mode() -> None:
    """Both presets, carrying the configured cadence (default 1.5 s)."""

    assert Settings(_env_file=None, demo_mode=True).timings == Timings.demo(1.5)  # type: ignore[call-arg]
    assert Settings(_env_file=None, demo_mode=False).timings == Timings.production(1.5)  # type: ignore[call-arg]
    # The presets themselves still describe a 1 Hz stream, so every fixture
    # and existing test that builds `Timings.demo()` by hand keeps its meaning.
    assert Timings.demo().tick_interval_s == 1.0
    assert Timings.production().tick_interval_s == 1.0


def test_scaled_hits_divides_the_1hz_reference_counts() -> None:
    """A "N hits in W s" threshold is a fraction of the window, not a count.

    At 1.5 s per tick a 20 s window only holds ~13 ticks, so the demo's
    "8 screen hits in 20 s" has to become 5 or it can never be reached.
    """

    one, slow, slower = Timings.demo(1.0), Timings.demo(1.5), Timings.demo(2.0)

    # 1 Hz is the identity.
    for n in (1, 2, 3, 6, 8, 15, 20):
        assert one.scaled_hits(n) == n

    assert slow.scaled_hits(slow.screen_sustained_min_hits) == 2  # 3 / 1.5
    assert slow.scaled_hits(slow.people_sustained_min_hits) == 1  # 2 / 1.5 -> 1
    assert slow.scaled_hits(slow.outdoor_min_hits) == 1  # 2 / 1.5 -> 1
    assert slow.scaled_hits(slow.food_min_hits) == 1  # 1 / 1.5 -> 1

    assert slower.scaled_hits(slower.screen_sustained_min_hits) == 2  # 3 / 2
    assert slower.scaled_hits(slower.people_sustained_min_hits) == 1  # 2 / 2
    assert slower.scaled_hits(slower.outdoor_min_hits) == 1
    assert slower.scaled_hits(slower.food_min_hits) == 1

    # Never zero: a threshold that rounds away would fire on nothing.
    assert Timings.demo(60.0).scaled_hits(2) == 1


def test_ai_max_age_ms_widens_with_the_cadence() -> None:
    """A block older than ~2.5 ticks is unknown, never below the 3 s floor."""

    assert Timings.demo(1.0).ai_max_age_ms == 3000
    assert Timings.demo(1.5).ai_max_age_ms == 3750
    assert Timings.demo(2.0).ai_max_age_ms == 5000
    assert Timings.production(1.5).ai_max_age_ms == 3750
    # A faster stream does not narrow it.
    assert Timings.demo(0.5).ai_max_age_ms == 3000


def test_demo_shortens_every_duration() -> None:
    """SPEC §6: DEMO_MODE shortens every cooldown and rate limit."""

    prod, demo = Timings.production(), Timings.demo()
    for f in fields(Timings):
        p, d = getattr(prod, f.name), getattr(demo, f.name)
        if f.name in (
            "t1_max_concurrent",
            "speech_max_per_hour",
            "ask_max_per_hour",
        ):
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
