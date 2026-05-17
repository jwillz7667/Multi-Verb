"""`ElevenLabsTTS` — tested with httpx MockTransport + a chunked byte stream.

The provider returns raw `pcm_<rate>` bytes (no SSE framing), so the
test transport wraps a custom `AsyncByteStream` that hands the client
multiple discrete chunks. Same production parsing path runs end to
end. Covers:

  * happy path — multiple chunks then a terminal `is_final` marker
  * empty byte sequences mid-stream are skipped, not yielded as audio
  * non-200 response surfaces as `TTSError`
  * transport-level error surfaces as `TTSError`
  * the request URL, query params, headers, and body match ElevenLabs'
    documented schema (pin so a silent contract drift breaks CI)
  * custom `model_id` and `voice_settings` flow through
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx
import pytest

from verbio_engine.tts.elevenlabs import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL_ID,
    ElevenLabsTTS,
)
from verbio_engine.tts.protocol import AudioChunk, TTSClient, TTSError, TTSRequest


class _ChunkedStream(httpx.AsyncByteStream):
    """Stream that yields the supplied byte chunks in order.

    `httpx.MockTransport` accepts a `stream=` on `Response` and uses it
    for `aiter_bytes()` — letting us simulate a real chunked response
    without serialising to a single buffer.
    """

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = list(chunks)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _make_client(
    *,
    status_code: int = 200,
    chunks: Iterable[bytes] | None = None,
    raw_body: bytes | None = None,
    captured: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        if raw_body is not None:
            return httpx.Response(status_code, content=raw_body)
        return httpx.Response(
            status_code,
            stream=_ChunkedStream(chunks or []),
            headers={"Content-Type": "audio/pcm"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _make_tts(client: httpx.AsyncClient, **kwargs: Any) -> ElevenLabsTTS:
    return ElevenLabsTTS(api_key="test-key", client=client, **kwargs)


async def _collect(tts: ElevenLabsTTS, text: str = "hello") -> list[AudioChunk]:
    request = TTSRequest(voice_id="voice-uuid")
    chunks: list[AudioChunk] = []
    async for chunk in tts.synthesize(request, text):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_satisfies_tts_client_protocol(self) -> None:
        tts = _make_tts(_make_client())
        assert isinstance(tts, TTSClient)

    def test_rejects_empty_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key must be non-empty"):
            ElevenLabsTTS(api_key="", client=_make_client())

    async def test_strips_trailing_slash_from_base_url(self) -> None:
        captured: list[httpx.Request] = []
        client = _make_client(chunks=[], captured=captured)
        tts = ElevenLabsTTS(
            api_key="k",
            client=client,
            base_url="https://example.invalid/",
        )
        async for _ in tts.synthesize(TTSRequest(voice_id="v"), "x"):
            pass
        # No double-slash before the v1 path; full path includes voice id.
        assert str(captured[0].url).startswith("https://example.invalid/v1/text-to-speech/v/stream")


# ---------------------------------------------------------------------------
# Happy path streaming
# ---------------------------------------------------------------------------


class TestStreamingHappyPath:
    async def test_yields_each_chunk_then_final_terminator(self) -> None:
        chunk_a = b"\x01\x02\x03\x04"
        chunk_b = b"\x05\x06\x07\x08"
        client = _make_client(chunks=[chunk_a, chunk_b])
        chunks = await _collect(_make_tts(client))

        # 2 audio chunks + 1 final terminator.
        assert len(chunks) == 3
        assert chunks[0].pcm == chunk_a
        assert chunks[0].is_final is False
        assert chunks[0].sample_rate == 24000
        assert chunks[1].pcm == chunk_b
        assert chunks[1].is_final is False
        assert chunks[2].pcm == b""
        assert chunks[2].is_final is True
        assert chunks[2].sample_rate == 24000

    async def test_empty_byte_chunks_are_skipped_not_yielded(self) -> None:
        # The provider sometimes emits zero-length keep-alive chunks
        # between PCM frames — they're not audio and shouldn't surface.
        chunk_a = b"\x42"
        client = _make_client(chunks=[b"", chunk_a, b""])
        chunks = await _collect(_make_tts(client))
        assert [c.pcm for c in chunks if not c.is_final] == [chunk_a]

    async def test_terminator_present_even_with_empty_stream(self) -> None:
        # Empty server response (no audio at all) still gets the
        # is_final marker so consumers don't hang waiting for one.
        client = _make_client(chunks=[])
        chunks = await _collect(_make_tts(client))
        assert chunks == [AudioChunk(pcm=b"", sample_rate=24000, is_final=True)]


# ---------------------------------------------------------------------------
# Request encoding
# ---------------------------------------------------------------------------


class TestRequestEncoding:
    async def test_posts_to_voice_stream_with_documented_schema(self) -> None:
        captured: list[httpx.Request] = []
        client = _make_client(chunks=[], captured=captured)
        tts = _make_tts(client)
        request = TTSRequest(voice_id="voice-abc", sample_rate=16000)
        async for _ in tts.synthesize(request, "Hello world."):
            pass

        assert len(captured) == 1
        http_req = captured[0]
        assert http_req.method == "POST"
        # URL includes voice id in the path + output_format query param.
        url = str(http_req.url)
        assert url.startswith(f"{DEFAULT_BASE_URL}/v1/text-to-speech/voice-abc/stream")
        assert "output_format=pcm_16000" in url

        # Headers: API key + content negotiation.
        assert http_req.headers["xi-api-key"] == "test-key"
        assert http_req.headers["Accept"] == "audio/pcm"
        assert http_req.headers["Content-Type"] == "application/json"

        # Body: text + model_id; no voice_settings unless explicitly set.
        body = json.loads(http_req.content.decode())
        assert body == {
            "text": "Hello world.",
            "model_id": DEFAULT_MODEL_ID,
        }

    async def test_custom_model_id_is_forwarded(self) -> None:
        captured: list[httpx.Request] = []
        client = _make_client(chunks=[], captured=captured)
        tts = ElevenLabsTTS(
            api_key="k",
            client=client,
            model_id="eleven_turbo_v2_5",
        )
        async for _ in tts.synthesize(TTSRequest(voice_id="v"), "x"):
            pass
        body = json.loads(captured[0].content.decode())
        assert body["model_id"] == "eleven_turbo_v2_5"

    async def test_voice_settings_are_forwarded_when_provided(self) -> None:
        captured: list[httpx.Request] = []
        client = _make_client(chunks=[], captured=captured)
        tts = ElevenLabsTTS(
            api_key="k",
            client=client,
            voice_settings={"stability": 0.6, "similarity_boost": 0.8},
        )
        async for _ in tts.synthesize(TTSRequest(voice_id="v"), "x"):
            pass
        body = json.loads(captured[0].content.decode())
        assert body["voice_settings"] == {"stability": 0.6, "similarity_boost": 0.8}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorWrapping:
    async def test_non_200_response_raises_tts_error(self) -> None:
        client = _make_client(
            status_code=401,
            raw_body=b'{"detail":"unauthorized"}',
        )
        with pytest.raises(TTSError, match="ElevenLabs TTS call failed"):
            await _collect(_make_tts(client))

    async def test_429_rate_limit_raises_tts_error(self) -> None:
        # Rate-limit fallout is the most plausible production failure;
        # pin it so the orchestrator sees a uniform TTSError regardless
        # of which status code the provider returned.
        client = _make_client(status_code=429, raw_body=b'{"detail":"too many"}')
        with pytest.raises(TTSError, match="ElevenLabs TTS call failed"):
            await _collect(_make_tts(client))

    async def test_network_error_wraps_in_tts_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(TTSError, match="ElevenLabs TTS call failed"):
            await _collect(_make_tts(client))


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_default_constants_match_brief_defaults() -> None:
    # Pin defaults to the brief's spec — silent provider/model swaps
    # must move this test as well, not slide through a config tweak.
    assert DEFAULT_BASE_URL == "https://api.elevenlabs.io"
    assert DEFAULT_MODEL_ID == "eleven_flash_v2_5"
