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
    assert msg["text"] == "Take a walk"
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


# -- one persistent connection, warm-up, stall fallback ---------------------

from pipeline.capture import speak as speak_mod
from pipeline.capture import tts as tts_mod


def _counting_client_factory(monkeypatch, handler):
    """Make the TTS's own lazily-built client run on a MockTransport, and count builds."""
    built = []
    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        client = real(**kwargs)
        built.append(kwargs)
        return client

    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", factory)
    return built


async def test_uncached_calls_share_one_keep_alive_client(monkeypatch):
    """A new client per call meant a new TCP+TLS handshake per utterance."""
    built = _counting_client_factory(
        monkeypatch, lambda request: httpx.Response(200, content=b"mp3")
    )
    tts = ElevenLabsTTS("key", "voice")
    assert await tts.synthesize("one") == b"mp3"
    assert await tts.synthesize("two") == b"mp3"
    assert len(built) == 1
    # The httpx default (5 s) would drop the idle socket between utterances.
    assert built[0]["limits"].keepalive_expiry >= 60
    assert built[0]["timeout"].read <= speak_mod.SYNTH_TIMEOUT_S
    await tts.aclose()
    assert tts.http is None


async def test_aclose_leaves_a_borrowed_client_open():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x"))
    ) as client:
        tts = ElevenLabsTTS("key", "voice", http=client)
        await tts.aclose()
        assert not client.is_closed


async def test_warm_opens_a_connection_without_synthesising():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(401, text="invalid api key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        # A dead key still counts: the socket is handshaken either way.
        assert await ElevenLabsTTS("dead", "voice", http=client).warm() is True
    assert seen == [("GET", "/")], "warm-up must never spend synthesis credit"


async def test_warm_never_raises_when_the_network_is_down():
    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await ElevenLabsTTS("key", "voice", http=client).warm() is False


async def test_speak_fn_exposes_a_safe_warm_hook(monkeypatch):
    async def exploding(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(ElevenLabsTTS, "warm", exploding)
    link = StubLink()
    fn = make_speak_fn(link, Settings(speech_mode="elevenlabs", elevenlabs_api_key="k"))
    assert await fn.warm() is False
    text_fn = make_speak_fn(StubLink(), Settings(speech_mode="text"))
    assert await text_fn.warm() is False, "nothing to warm in text mode"


async def test_a_stale_pooled_connection_is_retried_once():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("server closed idle connection", request=request)
        return httpx.Response(200, content=b"mp3")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await ElevenLabsTTS("key", "voice", http=client).synthesize("hi") == b"mp3"
    assert calls == 2


async def test_the_retry_is_one_shot():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadError("reset", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            await ElevenLabsTTS("key", "voice", http=client).synthesize("hi")
    assert calls == 2


async def test_cache_holds_a_demos_worth_of_lines():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"mp3")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tts = ElevenLabsTTS("key", "voice", http=client)
        for i in range(tts_mod.CACHE_SIZE):
            await tts.synthesize(f"line {i}")
        await tts.synthesize("line 0")
        assert calls == tts_mod.CACHE_SIZE, "the first line survived 63 others"
        await tts.synthesize("one more")
        await tts.synthesize("line 1")
    assert calls == tts_mod.CACHE_SIZE + 2, "LRU still evicts past the cap"


async def test_a_stalled_synthesis_falls_back_within_three_seconds(monkeypatch):
    """Dead air before the phone's voice takes over is bounded by SYNTH_TIMEOUT_S."""
    assert speak_mod.SYNTH_TIMEOUT_S <= 2.5

    async def hang(self, text):
        await asyncio.sleep(30)
        return b"too late"

    monkeypatch.setattr(ElevenLabsTTS, "synthesize", hang)
    link = StubLink()
    speech = speak_mod.make_speech(
        link, Settings(speech_mode="elevenlabs", elevenlabs_api_key="key")
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await speech.send("Take a walk") == 1
    assert loop.time() - started < 3.0
    assert wire.decode(link.messages[0])["type"] == "speak"
    assert speech.stats.tts_failures == 1


# -- mouth-busy estimate ------------------------------------------------------


class Mono:
    """A hand-wound monotonic clock for the playback estimate."""

    def __init__(self, t: float = 100.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _elevenlabs_speech(monkeypatch, n_bytes: int, link=None):
    async def synthesize(self, text):
        return b"x" * n_bytes
    monkeypatch.setattr(ElevenLabsTTS, "synthesize", synthesize)
    speech = speak_mod.make_speech(
        link or StubLink(), Settings(speech_mode="elevenlabs", elevenlabs_api_key="key")
    )
    speech.clock = Mono()
    return speech


async def test_a_sent_clip_keeps_the_mouth_busy_for_its_length(monkeypatch):
    """mp3_22050_32 is 4000 B/s: 12000 bytes is 3 s of audio, plus the A2DP pad."""
    assert "32" in ElevenLabsTTS("k", "v").output_format.split("_")[-1]
    speech = _elevenlabs_speech(monkeypatch, 12_000)
    assert speech.busy_for() == 0.0
    await speech.send("Put the chips down.")
    assert speech.busy_for() == pytest.approx(3.0 + speak_mod.PLAYBACK_PAD_S)
    speech.clock.t += 1.0
    assert speech.busy_for() == pytest.approx(2.0 + speak_mod.PLAYBACK_PAD_S)
    speech.clock.t += 2.0 + speak_mod.PLAYBACK_PAD_S
    assert speech.busy_for() == 0.0


async def test_text_mode_estimates_playback_from_words():
    speech = speak_mod.make_speech(StubLink(), Settings(speech_mode="text"))
    speech.clock = Mono()
    await speech.send("one two three four")
    assert speech.busy_for() == pytest.approx(
        4 * speak_mod.TEXT_S_PER_WORD + speak_mod.PLAYBACK_PAD_S)


async def test_a_clip_nobody_received_holds_nothing_up(monkeypatch):
    class DeadLink(StubLink):
        async def send_text(self, message):
            return 0
    speech = _elevenlabs_speech(monkeypatch, 12_000, link=DeadLink())
    assert await speech.send("hello") == 0
    assert speech.busy_for() == 0.0


async def test_a_line_still_synthesising_already_counts_as_busy(monkeypatch):
    """The agent closes and the next hand-off can arrive before the speak task
    has even started: the hook reserves the mouth synchronously."""
    gate = asyncio.Event()

    async def slow(self, text):
        await gate.wait()
        return b"x" * 4000
    monkeypatch.setattr(ElevenLabsTTS, "synthesize", slow)
    speak = make_speak_fn(
        StubLink(), Settings(speech_mode="elevenlabs", elevenlabs_api_key="key"))
    speak.speech.clock = Mono()
    assert speak.busy_for() == 0.0
    assert speak("Hello there", "normal") is True
    assert speak.busy_for() > 0.0, "busy before the task has run at all"
    await asyncio.sleep(0)
    assert speak.busy_for() > 0.0, "busy while synthesising"
    gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert speak.busy_for() == pytest.approx(1.0 + speak_mod.PLAYBACK_PAD_S)
    assert speak.speech._inflight == 0
