"""ElevenLabs streaming text-to-speech client."""

from __future__ import annotations

from collections import OrderedDict

import httpx


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
        self.http = http
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    async def synthesize(self, text: str) -> bytes:
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
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

        if self.http is None:
            async with httpx.AsyncClient(timeout=8.0) as client:
                data = await self._stream(client, request)
        else:
            data = await self._stream(self.http, request)

        self._cache[text] = data
        self._cache.move_to_end(text)
        while len(self._cache) > 8:
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
