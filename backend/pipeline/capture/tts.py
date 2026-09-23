"""ElevenLabs streaming text-to-speech client."""

from __future__ import annotations

import logging
from collections import OrderedDict

import httpx

log = logging.getLogger(__name__)

API_ROOT = "https://api.elevenlabs.io"

#: Per-phase HTTP limits. The outer deadline in ``speak.SYNTH_TIMEOUT_S`` (2.5 s)
#: is what actually bounds an utterance; these only make a dead socket fail
#: *inside* it with a precise httpx error instead of a bare TimeoutError. Connect
#: gets less because a handshake that has not finished in 1.5 s is not going to
#: leave time for synthesis anyway.
HTTP_TIMEOUT = httpx.Timeout(2.5, connect=1.5)

#: httpx drops an idle pooled connection after 5 s by default. Utterances on
#: stage are tens of seconds apart, so with the default every one would pay a
#: fresh TCP + TLS handshake again (~80-150 ms) -- exactly what the shared client
#: exists to avoid. The server may still close first; that is handled by the
#: one-shot retry in :meth:`ElevenLabsTTS.synthesize`.
HTTP_LIMITS = httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120.0)

#: Exact-text repeats are common on stage (one question was asked 7 of 13 times
#: on demo night), and an mp3 clip is ~8-12 KB, so 64 entries is well under 1 MB.
CACHE_SIZE = 64

#: The errors a pooled keep-alive connection raises when the server closed it
#: while it sat idle. Retrying once on a fresh connection is cheap; letting it
#: fall back to the phone's robot voice for a stale socket is not.
_STALE_CONNECTION_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)


class ElevenLabsTTS:
    """Render short utterances with ElevenLabs Flash and cache repeats."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        output_format: str = "mp3_22050_32",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.output_format = output_format
        # A caller-supplied client is borrowed (tests pass a MockTransport one);
        # without one we build a single long-lived client lazily and own it.
        self.http = http
        self._owns_http = False
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    def _client(self) -> httpx.AsyncClient:
        """The one client every call shares, so its keep-alive socket is reused.

        Built on first use rather than in ``__init__`` because wiring constructs
        this object synchronously, before the event loop the client will run on.
        """
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=HTTP_TIMEOUT, limits=HTTP_LIMITS)
            self._owns_http = True
        return self.http

    async def warm(self) -> bool:
        """Open the TLS connection now so the first utterance does not pay for it.

        Costs no credit: it hits the API root, not the synthesis endpoint, and
        any HTTP status -- a 401 for a dead key, a 404 -- still leaves a
        handshaken socket in the pool. Never raises: a warm-up that fails (no
        network, bad key) must not take the backend down; the first real
        utterance simply pays the handshake, or falls back to the phone voice as
        it always could. Returns whether a connection was made, for the log.
        """
        try:
            response = await self._client().get(
                f"{API_ROOT}/", headers={"xi-api-key": self.api_key}
            )
            await response.aclose()
        except Exception as exc:  # noqa: BLE001 -- warm-up is best effort by design
            log.warning("ElevenLabs warm-up failed: %s: %s", type(exc).__name__, exc)
            return False
        log.info("ElevenLabs connection warm (status %d)", response.status_code)
        return True

    async def aclose(self) -> None:
        """Close the client if we built it; a borrowed one belongs to its owner."""
        if self._owns_http and self.http is not None:
            await self.http.aclose()
            self.http = None
            self._owns_http = False

    async def synthesize(self, text: str) -> bytes:
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        url = f"{API_ROOT}/v1/text-to-speech/{self.voice_id}/stream"
        request = {
            "method": "POST",
            "url": url,
            "params": {"output_format": self.output_format},
            "headers": {"xi-api-key": self.api_key},
            "json": {
                "text": text,
                "model_id": self.model_id,
                "voice_settings": {"stability": 0.4, "similarity_boost": 0.8},
            },
        }

        client = self._client()
        try:
            data = await self._stream(client, request)
        except _STALE_CONNECTION_ERRORS as exc:
            # Most likely the pooled socket was closed by the server while idle;
            # httpx has already discarded it, so this attempt opens a fresh one.
            log.info("ElevenLabs connection dropped (%s); retrying once", type(exc).__name__)
            data = await self._stream(client, request)

        self._cache[text] = data
        self._cache.move_to_end(text)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return data

    @staticmethod
    async def _stream(client: httpx.AsyncClient, request: dict[str, object]) -> bytes:
        chunks: list[bytes] = []
        async with client.stream(**request) as response:  # type: ignore[arg-type]
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")
                raise RuntimeError(
                    f"ElevenLabs TTS returned {response.status_code}: {body}"
                )
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
        return b"".join(chunks)
