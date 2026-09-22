from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest
from pydantic import AliasChoices

from pipeline.config import Settings, Timings


@pytest.fixture(autouse=True)
def _no_ambient_settings_env(monkeypatch) -> None:
    """``Settings(_env_file=None)`` here means the shipped defaults. Earlier tests
    can leave the developer's real backend/.env in os.environ (the capture
    bridge calls load_dotenv, which nothing undoes), so drop every key Settings
    reads; a test that wants one sets it with monkeypatch."""

    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
        for env in {name, *(c for c in choices if isinstance(c, str))}:
            monkeypatch.delenv(env.upper(), raising=False)


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
    assert settings.keyword_triggers == []
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
                "change_max_per_min",
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


def test_demo_timings_are_tuned_for_reactive_glasses() -> None:
    """The fast-path timings: a 2 s global gap (was 5, i.e. 6 at 1.5 s ticks),
    8 s between changes (props go through the cue trigger now, so `change`
    only wakes the clerk on scene flips), no quiet window after a conversation
    (relies on the mouth-busy guard), and a 6 s listen (was 8; 5 left only
    ~0.4 s over the one real "yes" on record)."""

    demo = Timings.demo(tick_interval_s=1.5)
    assert demo.global_escalation_min_gap == 2.0
    assert demo.change_cooldown_s == 8.0
    assert demo.conversation_cooldown_s == 0.0
    assert demo.ask_listen_s == 6.0


def test_the_demo_preset_is_frozen() -> None:
    """Every latency number in the plan was measured against these values.
    Changing one is a deliberate act: update this test with the reason."""

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.demo_mode is True and s.tick_interval_s == 1.5
    assert s.t1_model == "gpt-5.4-mini"
    assert s.keyword_triggers == []
    demo = s.timings
    assert demo.tick_interval_s == 1.5 and demo.ai_max_age_ms == 3750
    assert (demo.trigger_cooldown_default, demo.global_escalation_min_gap,
            demo.change_cooldown_s, demo.change_max_per_min) == (20.0, 2.0, 8.0, 6)
    assert (demo.ask_listen_s, demo.ask_expire_s, demo.conversation_cooldown_s,
            demo.conversation_lifetime_s, demo.conversation_max_questions) == (
                6.0, 25.0, 0.0, 60.0, 2)
    assert demo.t1_max_concurrent == 1


BOOL_SWITCHES = ("FAST_PATH", "CUE_TRIGGER", "PUBLISH_ON_LANDING",
                 "MOUTH_BUSY_GUARD", "VOICE_OPEN_SCHEMA")


def test_kill_switches_default_on(monkeypatch) -> None:
    for name in (*BOOL_SWITCHES, "VLM_MAX_IN_FLIGHT", "T0_MAX_IN_FLIGHT"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.fast_path, s.cue_trigger, s.vlm_max_in_flight) == (True, True, 2)
    assert (s.publish_on_landing, s.mouth_busy_guard, s.voice_open_schema) == (True, True, True)
    assert s.switches_line() == (
        "FAST_PATH=1 CUE_TRIGGER=1 VLM_MAX_IN_FLIGHT=2 "
        "PUBLISH_ON_LANDING=1 MOUTH_BUSY_GUARD=1 VOICE_OPEN_SCHEMA=1")


def test_kill_switches_turn_off_from_the_environment(monkeypatch) -> None:
    for name in BOOL_SWITCHES:
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("VLM_MAX_IN_FLIGHT", "1")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.fast_path, s.cue_trigger, s.vlm_max_in_flight) == (False, False, 1)
    assert (s.publish_on_landing, s.mouth_busy_guard, s.voice_open_schema) == (False, False, False)
    assert s.switches_line() == (
        "FAST_PATH=0 CUE_TRIGGER=0 VLM_MAX_IN_FLIGHT=1 "
        "PUBLISH_ON_LANDING=0 MOUTH_BUSY_GUARD=0 VOICE_OPEN_SCHEMA=0")


def test_a_switch_set_in_dotenv_reaches_settings_without_load_dotenv(tmp_path, monkeypatch) -> None:
    """MOUTH_BUSY_GUARD and VOICE_OPEN_SCHEMA used to be read from os.environ,
    so a value in .env only worked if load_dotenv had already run (not in sim
    mode). As Settings fields they are read from .env directly."""

    for name in BOOL_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("MOUTH_BUSY_GUARD=0\nVOICE_OPEN_SCHEMA=off\nPUBLISH_ON_LANDING=no\n")
    s = Settings(_env_file=env)  # type: ignore[call-arg]
    assert (s.mouth_busy_guard, s.voice_open_schema, s.publish_on_landing) == (False, False, False)


def test_a_typo_in_a_boolean_switch_falls_back_to_on(monkeypatch, caplog) -> None:
    """FAST_PATH=of used to raise a ValidationError and stop the backend. Like
    VLM_MAX_IN_FLIGHT, a venue typo now falls back to the rehearsed default."""

    for value, expected in (("of", True), ("nope", True), ("O", True), ("flase", True),
                            ("off", False), ("No", False), ("FALSE", False), (" 0 ", False),
                            ("yes", True), ("ON", True), ("1", True)):
        monkeypatch.setenv("FAST_PATH", value)
        monkeypatch.setenv("CUE_TRIGGER", value)
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert (s.fast_path, s.cue_trigger) == (expected, expected), value
    monkeypatch.setenv("FAST_PATH", "of")
    monkeypatch.setenv("CUE_TRIGGER", "nope")
    with caplog.at_level("WARNING"):
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "'of'" in caplog.text and "'nope'" in caplog.text


def test_vlm_max_in_flight_alias_and_lenient_parse(monkeypatch, caplog) -> None:
    """The audit's T0_MAX_IN_FLIGHT spelling works too, and a typo at the venue
    falls back to a sane value instead of stopping the backend."""

    monkeypatch.delenv("VLM_MAX_IN_FLIGHT", raising=False)
    monkeypatch.setenv("T0_MAX_IN_FLIGHT", "1")
    assert Settings(_env_file=None).vlm_max_in_flight == 1  # type: ignore[call-arg]
    monkeypatch.setenv("T0_MAX_IN_FLIGHT", "5")
    assert Settings(_env_file=None).vlm_max_in_flight == 2  # type: ignore[call-arg]
    monkeypatch.setenv("T0_MAX_IN_FLIGHT", "two")
    with caplog.at_level("WARNING"):
        assert Settings(_env_file=None).vlm_max_in_flight == 2  # type: ignore[call-arg]
    assert "VLM_MAX_IN_FLIGHT" in caplog.text


def test_the_production_preset_stays_conservative() -> None:
    production = Timings.production(tick_interval_s=1.5)
    assert production.global_escalation_min_gap == 60.0
    assert production.change_cooldown_s == 20.0
    assert production.conversation_cooldown_s == 60.0
    assert production.ask_listen_s == 8.0
