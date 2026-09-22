import asyncio
import io
import json
import time

from PIL import Image

from pipeline.bus import TickBus
from pipeline.capture.bridge import LongevityCapture
from pipeline.capture.frames import RingFrameStore
from pipeline.capture.speak import make_speak_fn
from pipeline.config import Settings


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
