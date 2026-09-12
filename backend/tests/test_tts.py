import asyncio
import base64

import httpx
import pytest

from longevity import wire
from pipeline.capture.speak import make_speak_fn
from pipeline.capture.tts import ElevenLabsTTS
from pipeline.config import Settings


async def test_synthesize_streams_and_caches():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"fake-mp3")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tts = ElevenLabsTTS("key", "voice", http=client)
        assert await tts.synthesize("hello") == b"fake-mp3"
        assert await tts.synthesize("hello") == b"fake-mp3"
    assert calls == 1


async def test_synthesize_non_200_includes_body():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, text="bad credentials")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="bad credentials"):
            await ElevenLabsTTS("key", "voice", http=client).synthesize("hello")


class StubLink:
    clients = {object()}

    def __init__(self):
        self.messages = []

    async def send_text(self, message):
        self.messages.append(message)
        return 1


async def _speak(settings, monkeypatch=None, failure=False):
    link = StubLink()
    if monkeypatch is not None:
        async def synthesize(self, text):
            if failure:
                raise RuntimeError("boom")
            return b"mp3-bytes"
        monkeypatch.setattr(ElevenLabsTTS, "synthesize", synthesize)
    make_speak_fn(link, settings)("Take a walk", "high")
    await asyncio.sleep(0)
    return wire.decode(link.messages[0])


async def test_make_speak_fn_sends_audio(monkeypatch):
    msg = await _speak(
        Settings(speech_mode="elevenlabs", elevenlabs_api_key="key"), monkeypatch
    )
    assert msg["type"] == "audio"
    assert msg["format"] == "mp3"
    assert base64.b64decode(msg["data"]) == b"mp3-bytes"


async def test_tts_failure_falls_back_to_speak(monkeypatch):
    msg = await _speak(
        Settings(speech_mode="elevenlabs", elevenlabs_api_key="key"),
        monkeypatch,
        failure=True,
    )
    assert msg["type"] == "speak"


@pytest.mark.parametrize(
    "settings",
    [Settings(speech_mode="text"), Settings(speech_mode="auto", elevenlabs_api_key=None)],
)
async def test_text_modes_send_speak(settings):
    assert (await _speak(settings))["type"] == "speak"
