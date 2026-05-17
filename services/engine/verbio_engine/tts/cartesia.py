"""`CartesiaTTS` — production default TTS via Cartesia Sonic.

Calls Cartesia's `/tts/sse` endpoint with the requested voice and
streams the response as PCM `AudioChunk`s. Chosen over the WebSocket
endpoint because:
  * The §6 1500 ms p95 budget is reachable with HTTP/2 SSE — we wait
    for the full mouth-layer sentence (one sentence; ~1s LLM time
    p95) before invoking TTS, then SSE chunks back the audio.
  * SSE means one streaming HTTP call rather than connection lifecycle
    state, which is simpler to instrument, retry, and rate-limit.

True bidirectional LLM-token → TTS-text streaming via WebSocket lives
behind the same `TTSClient` Protocol — a later layer can swap the
implementation if measured latency forces it.

Errors (HTTP non-200, network, malformed SSE) wrap into `TTSError` so
the orchestrator catches one type at the boundary.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import structlog
from httpx_sse import aconnect_sse

from verbio_engine.tts.protocol import AudioChunk, TTSError, TTSRequest

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.cartesia.ai"
DEFAULT_MODEL_ID = "sonic-2"
DEFAULT_CARTESIA_VERSION = "2024-06-10"


class CartesiaTTS:
    """Streaming TTS that delegates to an injected `httpx.AsyncClient`."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient,
        model_id: str = DEFAULT_MODEL_ID,
        base_url: str = DEFAULT_BASE_URL,
        cartesia_version: str = DEFAULT_CARTESIA_VERSION,
    ) -> None:
        if not api_key:
            msg = "CartesiaTTS: api_key must be non-empty"
            raise ValueError(msg)

        self._api_key = api_key
        self._client = client
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._cartesia_version = cartesia_version

    async def synthesize(
        self,
        request: TTSRequest,
        text: str,
    ) -> AsyncIterator[AudioChunk]:
        body: dict[str, Any] = {
            "model_id": self._model_id,
            "transcript": text,
            "voice": {"mode": "id", "id": request.voice_id},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": request.sample_rate,
            },
            "language": "en",
        }
        headers = {
            "X-API-Key": self._api_key,
            "Cartesia-Version": self._cartesia_version,
            "Content-Type": "application/json",
        }
        url = f"{self._base_url}/tts/sse"

        try:
            async with aconnect_sse(
                self._client,
                "POST",
                url,
                json=body,
                headers=headers,
            ) as event_source:
                # Surface non-200 responses as TTSError before we start
                # iterating; httpx_sse otherwise raises a less specific
                # exception deeper in the loop.
                event_source.response.raise_for_status()
                async for sse in event_source.aiter_sse():
                    parsed = _parse_chunk(sse.data, request.sample_rate)
                    if parsed is _DONE:
                        break
                    if isinstance(parsed, AudioChunk):
                        yield parsed
        except TTSError:
            raise
        except Exception as exc:
            log.warning(
                "cartesia_tts_error",
                reason=type(exc).__name__,
                detail=str(exc) or None,
                voice_id=request.voice_id,
                model_id=self._model_id,
            )
            msg = f"Cartesia TTS call failed: {exc}"
            raise TTSError(msg) from exc

        yield AudioChunk(pcm=b"", sample_rate=request.sample_rate, is_final=True)


_DONE = object()


def _parse_chunk(data: str, sample_rate: int) -> AudioChunk | object | None:
    """Parse one SSE event body.

    Returns:
        AudioChunk — for `type=chunk` events with non-empty `data`.
        _DONE sentinel — for `type=done` events (caller breaks loop).
        None — for empty/keep-alive lines and forward-compat event types.

    Raises:
        TTSError — JSON decode failure, missing audio base64, or
            structurally invalid chunk (provider contract broken).
    """
    if not data:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        msg = f"Cartesia returned non-JSON SSE: {data[:120]!r}"
        raise TTSError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"Cartesia SSE payload was not an object: {data[:120]!r}"
        raise TTSError(msg)

    msg_type = payload.get("type")
    if msg_type == "done" or payload.get("done") is True:
        return _DONE
    if msg_type != "chunk":
        return None  # unknown / informational event — silently skip

    b64 = payload.get("data")
    if not b64:
        return None
    if not isinstance(b64, str):
        msg = f"Cartesia chunk.data was not a string: {type(b64).__name__}"
        raise TTSError(msg)
    try:
        pcm = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError) as exc:
        msg = "Cartesia chunk.data was not valid base64"
        raise TTSError(msg) from exc
    return AudioChunk(pcm=pcm, sample_rate=sample_rate)
