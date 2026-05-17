"""`ElevenLabsTTS` — brief §9 fallback TTS via ElevenLabs Flash.

The orchestrator (P4 L8) reaches here when the default Cartesia path
raises `TTSError`. Flash is chosen over the higher-quality v2 models
because it streams first audio in ~75 ms server-side, which keeps the
§6 1500 ms p95 budget within reach even on the fallback path.

ElevenLabs' `/v1/text-to-speech/{voice_id}/stream` endpoint returns
raw audio bytes (no SSE framing) when `output_format=pcm_<rate>` is
set, so this client is simpler than `CartesiaTTS` — we read straight
from `aiter_bytes()` and emit each non-empty chunk. The terminal
`is_final=True` `AudioChunk` always follows, even when the upstream
closes without a clean end marker, so consumers can rely on a single
termination signal regardless of provider.

Errors (HTTP non-200, network, transport) wrap into `TTSError` so the
orchestrator catches one type and decides whether to retry, fall
through to the pre-synthesised cache (P4 L6), or abandon the
intervention.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog

from verbio_engine.tts.protocol import AudioChunk, TTSError, TTSRequest

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL_ID = "eleven_flash_v2_5"


class ElevenLabsTTS:
    """Streaming TTS via ElevenLabs Flash, used as the §9 fallback."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient,
        model_id: str = DEFAULT_MODEL_ID,
        base_url: str = DEFAULT_BASE_URL,
        voice_settings: dict[str, Any] | None = None,
    ) -> None:
        if not api_key:
            msg = "ElevenLabsTTS: api_key must be non-empty"
            raise ValueError(msg)

        self._api_key = api_key
        self._client = client
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        # Defaults are intentionally None — the persona-tagged voice
        # itself already encodes formality/warmth/pace (brief §9). We
        # only forward voice_settings if the caller wants to override.
        self._voice_settings = voice_settings

    async def synthesize(
        self,
        request: TTSRequest,
        text: str,
    ) -> AsyncIterator[AudioChunk]:
        url = f"{self._base_url}/v1/text-to-speech/{request.voice_id}/stream"
        params = {"output_format": f"pcm_{request.sample_rate}"}
        body: dict[str, Any] = {
            "text": text,
            "model_id": self._model_id,
        }
        if self._voice_settings is not None:
            body["voice_settings"] = self._voice_settings
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }

        try:
            async with self._client.stream(
                "POST",
                url,
                params=params,
                json=body,
                headers=headers,
            ) as response:
                # Surface HTTP errors as TTSError before we start
                # iterating the body — once aiter_bytes starts on a
                # non-2xx, httpx raises a less specific exception.
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield AudioChunk(pcm=chunk, sample_rate=request.sample_rate)
        except TTSError:
            raise
        except Exception as exc:
            log.warning(
                "elevenlabs_tts_error",
                reason=type(exc).__name__,
                detail=str(exc) or None,
                voice_id=request.voice_id,
                model_id=self._model_id,
            )
            msg = f"ElevenLabs TTS call failed: {exc}"
            raise TTSError(msg) from exc

        yield AudioChunk(pcm=b"", sample_rate=request.sample_rate, is_final=True)
