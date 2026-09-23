import asyncio
import io
import json
import time

import pytest
from PIL import Image
from longevity.ring import DEFAULT_MAX_FRAMES

from pipeline.bus import TickBus
from pipeline.capture.bridge import LongevityCapture
from pipeline.capture.frames import RingFrameStore
from pipeline.capture.speak import make_speak_fn
from pipeline.config import Settings


@pytest.fixture(autouse=True)
def _watcher_off_unless_asked(monkeypatch):
    """The watcher defaults on with MobileCLIP; these tests opt in with the fake
    model explicitly, so a dev machine with the extra installed never loads it."""
    monkeypatch.setenv("WATCHER", "0")
    monkeypatch.setenv("WATCHER_MODEL", "fake")


def _corpus(path, count=6):
    base = int(time.time() * 1000)
    for i in range(count):
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (20 * i, 40, 80)).save(buf, "JPEG")
        (path / f"frame_{base + i * 1000}.jpg").write_bytes(buf.getvalue())


async def test_replay_bridge_converts_ticks_and_exposes_frames(tmp_path):
    _corpus(tmp_path)
    bus = TickBus()
    sub = bus.subscribe("test")
    capture = LongevityCapture(
        Settings(db_path=tmp_path / "unused.db", tick_interval_s=1.0),
        source="replay", our_bus=bus, dir=str(tmp_path), speed=50, loop=False,
        camera=0, vlm="fake", flow=None,
    )
    await capture.start()
    ticks = [await asyncio.wait_for(sub.get(), 2) for _ in range(6)]
    await capture.stop()

    assert all(t.sensor.phash and t.frame_ref for t in ticks)
    store = RingFrameStore(capture.ring)
    assert store.get([ticks[-1].frame_ref, "unknown"]).keys() == {ticks[-1].frame_ref}
    # >= because publish-on-landing re-sends a tick through the same `forward`
    # when the fake VLM lands between two frames.
    assert capture.converted >= 6
    assert capture.dropped == 0

    capture.his_bus.publish({"malformed": True})
    assert capture.dropped == 1


async def test_speak_schedules_phone_message():
    class Link:
        clients = {object()}
        messages = []

        async def send_text(self, message):
            self.messages.append(message)
            return 1

    link = Link()
    make_speak_fn(link)("Take a walk", "high")
    await asyncio.sleep(0)
    assert json.loads(link.messages[0]) == {
        "v": 1, "type": "speak", "text": "Take a walk", "urgency": "high"
    }


def test_the_bridge_publishes_a_landed_result_without_waiting_a_tick(tmp_path):
    """T0Loop's publish-on-landing is opt-in; the bridge opts in through the
    same `forward` every tick takes, so a landed Gemini result reaches the
    gate at once instead of on the next frame."""

    _corpus(tmp_path, count=1)
    capture = LongevityCapture(
        Settings(db_path=tmp_path / "unused.db", tick_interval_s=1.0),
        source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=50, loop=False,
        camera=0, vlm="fake", flow=None,
    )
    assert capture.tagger.on_result is not None
    assert capture.loop._on_ai_update is not None


def test_vlm_max_in_flight_switch_reaches_the_tagger(tmp_path):
    """VLM_MAX_IN_FLIGHT=1 is the stage switch back to the serial tagger; the
    default keeps two calls in flight."""

    _corpus(tmp_path, count=1)
    for n in (1, 2):
        capture = LongevityCapture(
            Settings(_env_file=None, db_path=tmp_path / "unused.db",  # type: ignore[call-arg]
                     tick_interval_s=1.0, vlm_max_in_flight=n),
            source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=50, loop=False,
            camera=0, vlm="fake", flow=None,
        )
        assert capture.tagger.stats()["max_in_flight"] == n


def test_publish_on_landing_can_be_turned_off(tmp_path):
    """PUBLISH_ON_LANDING=0: the loop is built without on_ai_update, so the
    tagger has no landing listener and a landed result rides the next frame."""

    _corpus(tmp_path, count=1)
    capture = LongevityCapture(
        Settings(_env_file=None, db_path=tmp_path / "unused.db",  # type: ignore[call-arg]
                 tick_interval_s=1.0, publish_on_landing=False),
        source="replay", our_bus=TickBus(), dir=str(tmp_path), speed=50, loop=False,
        camera=0, vlm="fake", flow=None,
    )
    assert capture.tagger.on_result is None
    assert capture.loop._on_ai_update is None


async def test_with_publish_on_landing_off_no_tick_is_published_twice(tmp_path):
    """Real time, one frame a second, the fake VLM landing 0.4 s after each
    frame. On: the landing re-sends the same tick_id at once. Off: the result
    rides the next frame and every tick_id goes out exactly once. Every publish
    is recorded (the real bus is a size-1 mailbox and would hide a duplicate)."""

    class Recorder:
        def __init__(self) -> None:
            self.ticks: list = []

        def publish(self, tick) -> None:
            self.ticks.append(tick)

    async def run(on: bool) -> list[str]:
        corpus = tmp_path / ("on" if on else "off")
        corpus.mkdir()
        _corpus(corpus, count=2)
        bus = Recorder()
        capture = LongevityCapture(
            Settings(_env_file=None, db_path=corpus / "unused.db",  # type: ignore[call-arg]
                     tick_interval_s=1.0, publish_on_landing=on),
            source="replay", our_bus=bus, dir=str(corpus), speed=1, loop=False,
            camera=0, vlm="fake", flow=None,
        )
        await capture.start()
        await asyncio.sleep(1.3)  # frame 1, its landing at ~0.4 s, frame 2
        await capture.stop()
        assert capture.converted == len(bus.ticks)
        return [t.tick_id for t in bus.ticks]

    on = await run(True)
    assert len(on) > len(set(on)), f"the switch-on control never re-sent: {on}"
    off = await run(False)
    assert len(off) >= 2
    assert len(off) == len(set(off)), off


# --- watcher wiring (docs/PERCEPTION.md "Watcher", "Labeler") ------------------


def _bridge(tmp_path, bus=None, **settings):
    return LongevityCapture(
        Settings(_env_file=None, db_path=tmp_path / "unused.db",  # type: ignore[call-arg]
                 tick_interval_s=1.0, **settings),
        source="replay", our_bus=bus or TickBus(), dir=str(tmp_path), speed=50,
        loop=False, camera=0, vlm="fake", flow=None,
    )


async def _raw_ticks(capture, n):
    raw: list[dict] = []
    capture.his_bus.subscribe(raw.append)
    await capture.start()
    deadline = time.monotonic() + 3
    while len(raw) < n and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    await capture.stop()
    assert len(raw) >= n
    return raw


async def test_watcher_off_is_todays_path(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER", "0")
    _corpus(tmp_path, count=4)
    capture = _bridge(tmp_path)
    assert capture.watcher is None and capture.watcher_error is None
    assert capture.loop._watcher is None
    assert capture.ring.max_frames == DEFAULT_MAX_FRAMES
    raw = await _raw_ticks(capture, 4)
    assert all("watch" not in t for t in raw)
    stats = capture.stats()
    assert stats["watcher"] is None and stats["watcher_error"] is None


async def test_watcher_on_with_the_fake_model(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER", "1")
    monkeypatch.setenv("WATCHER_MODEL", "fake")
    monkeypatch.setenv("WATCHER_FPS_MAX", "7")
    _corpus(tmp_path, count=4)
    bus = TickBus()
    sub = bus.subscribe("test")
    capture = _bridge(tmp_path, bus=bus)
    assert capture.watcher is not None and capture.watcher_error is None
    assert capture.loop._watcher is capture.watcher
    assert capture.ring.max_frames >= 630
    raw = await _raw_ticks(capture, 4)
    assert all("watch" in t for t in raw)
    assert (await asyncio.wait_for(sub.get(), 1)).watch is not None

    stats = capture.stats()
    assert isinstance(stats["watcher"], dict) and stats["watcher"]["model"] == "fake"
    assert stats["watcher_error"] is None
    labeler = stats["labeler"]
    assert set(labeler) == {
        "calls_per_hour", "capped", "last_heartbeat_age_s", "last_wake_latency_ms",
        "frames_sent_per_hour", "mode", "steady_s",
    }
    # With a watcher the first tick is a heartbeat, not a hot call.
    assert labeler["calls_per_hour"]["heartbeat"] >= 1
    assert labeler["frames_sent_per_hour"] == sum(labeler["calls_per_hour"].values())
    assert labeler["last_heartbeat_age_s"] is not None
    assert labeler["capped"] is False


def test_an_unbuildable_watcher_model_does_not_crash_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER", "1")
    monkeypatch.setenv("WATCHER_MODEL", "nope")
    _corpus(tmp_path, count=1)
    capture = _bridge(tmp_path)
    assert capture.watcher is None and capture.loop._watcher is None
    assert "nope" in capture.watcher_error
    assert capture.ring.max_frames == DEFAULT_MAX_FRAMES
    assert capture.stats()["watcher_error"] == capture.watcher_error


def test_scheduler_values_come_from_capture_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER", "1")
    monkeypatch.setenv("LABELER_HEARTBEAT_S", "42")
    monkeypatch.setenv("LABELER_MAX_PER_HOUR", "7")
    monkeypatch.setenv("LABELER_STEADY_S", "11")
    _corpus(tmp_path, count=1)
    sched = _bridge(tmp_path).tagger.scheduler
    assert (sched.heartbeat_s, sched.max_per_hour, sched.steady_s) == (42.0, 7, 11.0)
    assert sched.stats()["max_per_hour"] == 7


@pytest.mark.parametrize("gate_reads_watch, steady_s", [("1", 30.0), ("0", 10.0)])
def test_steady_cadence_follows_the_gate_reading_watch(
    tmp_path, monkeypatch, gate_reads_watch, steady_s
):
    monkeypatch.setenv("WATCHER", "1")
    monkeypatch.delenv("LABELER_STEADY_S", raising=False)
    monkeypatch.setenv("GATE_READS_WATCH", gate_reads_watch)
    _corpus(tmp_path, count=1)
    capture = _bridge(tmp_path)
    assert capture.tagger.scheduler.steady_s == steady_s
    assert capture.stats()["labeler"]["steady_s"] == steady_s


@pytest.mark.parametrize("env", [{"PUBLISH_ON_LANDING": "0"}, {"VLM_MAX_IN_FLIGHT": "1"}])
def test_stage_switches_still_construct_with_the_watcher(tmp_path, monkeypatch, env):
    monkeypatch.setenv("WATCHER", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    _corpus(tmp_path, count=1)
    capture = _bridge(tmp_path)
    assert capture.watcher is not None
    if "PUBLISH_ON_LANDING" in env:
        assert capture.loop._on_ai_update is None
    else:
        assert capture.tagger.stats()["max_in_flight"] == 1


async def test_wake_calls_are_metered(tmp_path):
    """Per-kind calls over the hour and the last wake call's latency come from the
    bridge's meter, since the tagger keeps neither."""
    _corpus(tmp_path, count=1)
    capture = _bridge(tmp_path)
    await capture.tagger.start()
    capture.tagger.request("wake", time.time(), b"jpeg")
    deadline = time.monotonic() + 2
    while capture.tagger.stats()["by_kind"]["wake"]["returned"] < 1:
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
    await capture.tagger.aclose()
    labeler = capture.stats()["labeler"]
    assert labeler["calls_per_hour"]["wake"] == 1
    assert labeler["last_wake_latency_ms"] >= 300  # the fake client sleeps 0.4 s
    assert labeler["last_heartbeat_age_s"] is None


def test_take_look_answer_is_forwarded_from_the_loop(tmp_path):
    _corpus(tmp_path, count=1)
    capture = _bridge(tmp_path)
    assert capture.take_look_answer() is None
    capture.loop.last_look_answer = (1.0, "a salad")
    assert capture.take_look_answer() == (1.0, "a salad")
    assert capture.take_look_answer() is None
