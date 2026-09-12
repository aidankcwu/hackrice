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
    assert capture.converted == 6
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
