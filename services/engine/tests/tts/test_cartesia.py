"""`CartesiaTTS` — tested with an httpx MockTransport simulating SSE.

We never reach api.cartesia.ai in tests. A `MockTransport` intercepts
the POST and returns a canned text/event-stream body; the same parsing
path runs as in production. Covers:

  * happy path — multiple chunk events then a done event
  * each chunk decoded from base64 into raw PCM
  * non-200 response surfaces as TTSError
  * malformed JSON in an SSE event surfaces as TTSError
  * non-base64 audio surfaces as TTSError
  * unknown event types are silently skipped (forward compat)
  * the request body + headers match Cartesia's documented schema
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from verbio_engine.tts.cartesia import (
    DEFAULT_BASE_URL,
    DEFAULT_CARTESIA_VERSION,
    DEFAULT_MODEL_ID,
    CartesiaTTS,
)
from verbio_engine.tts.protocol import AudioChunk, TTSClient, TTSError, TTSRequest


def _sse_body(events: list[dict[str, Any] | str]) -> bytes:
    """Encode a list of event dicts as a text/event-stream body."""
    out = b""
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event)
        out += f"data: {data}\n\n".encode()
    return out


def _make_client(
    *,
    status_code: int = 200,
    events: list[dict[str, Any] | str] | None = None,
    raw_body: bytes | None = None,
    captured: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    body = raw_body if raw_body is not None else _sse_body(events or [])

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(
            status_code,
            content=body,
            headers={"Content-Type": "text/event-stream"},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _make_tts(client: httpx.AsyncClient) -> CartesiaTTS:
    return CartesiaTTS(api_key="test-key", client=client)


async def _collect(tts: CartesiaTTS, text: str = "hello") -> list[AudioChunk]:
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
            CartesiaTTS(api_key="", client=_make_client())

    async def test_strips_trailing_slash_from_base_url(self) -> None:
        # Ensure callers can pass either form without producing a
        # double-slash in the resolved /tts/sse URL.
        captured: list[httpx.Request] = []
        client = _make_client(
            events=[{"type": "done"}],
            captured=captured,
        )
        tts = CartesiaTTS(
            api_key="k",
            client=client,
            base_url="https://example.invalid/",
        )
        async for _ in tts.synthesize(TTSRequest(voice_id="v"), "x"):
            pass
        assert str(captured[0].url) == "https://example.invalid/tts/sse"


# ---------------------------------------------------------------------------
# Happy path streaming
# ---------------------------------------------------------------------------


class TestStreamingHappyPath:
    async def test_yields_each_chunk_then_final_terminator(self) -> None:
        chunk_a = b"\x01\x02\x03\x04"
        chunk_b = b"\x05\x06\x07\x08"
        client = _make_client(
            events=[
                {"type": "chunk", "data": base64.b64encode(chunk_a).decode()},
                {"type": "chunk", "data": base64.b64encode(chunk_b).decode()},
                {"type": "done"},
            ],
        )
        tts = _make_tts(client)
        chunks = await _collect(tts)

        # 2 audio chunks + 1 final terminator.
        assert len(chunks) == 3
        assert chunks[0].pcm == chunk_a
        assert chunks[0].is_final is False
        assert chunks[0].sample_rate == 24000
        assert chunks[1].pcm == chunk_b
        assert chunks[1].is_final is False
        assert chunks[2].pcm == b""
        assert chunks[2].is_final is True

    async def test_terminator_present_even_without_done_event(self) -> None:
        # Stream ends naturally (server closed) without a done event —
        # we still emit the is_final terminator so consumers don't hang.
        chunk_a = b"\xff" * 8
        client = _make_client(
            events=[
                {"type": "chunk", "data": base64.b64encode(chunk_a).decode()},
            ],
        )
        chunks = await _collect(_make_tts(client))
        assert [c.pcm for c in chunks] == [chunk_a, b""]
        assert chunks[-1].is_final is True

    async def test_done_flag_in_chunk_event_also_terminates(self) -> None:
        # Some Cartesia models inline done=true on the last chunk
        # rather than emitting a separate `type=done` event.
        chunk_a = b"\x10\x20"
        client = _make_client(
            events=[
                {"type": "chunk", "data": base64.b64encode(chunk_a).decode()},
                {"type": "chunk", "data": "", "done": True},
            ],
        )
        chunks = await _collect(_make_tts(client))
        assert chunks[0].pcm == chunk_a
        assert chunks[-1].is_final is True

    async def test_unknown_event_types_are_silently_skipped(self) -> None:
        # Forward-compat: a new event type from Cartesia must not break
        # the stream — yield what we understand, terminate cleanly.
        chunk_a = b"\x01"
        client = _make_client(
            events=[
                {"type": "timestamps", "words": [{"text": "hi", "start": 0.0}]},
                {"type": "chunk", "data": base64.b64encode(chunk_a).decode()},
                {"type": "done"},
            ],
        )
        chunks = await _collect(_make_tts(client))
        assert [c.pcm for c in chunks if not c.is_final] == [chunk_a]

    async def test_empty_chunk_data_is_skipped_not_yielded(self) -> None:
        chunk_b = b"\x42"
        client = _make_client(
            events=[
                {"type": "chunk", "data": ""},
                {"type": "chunk", "data": base64.b64encode(chunk_b).decode()},
                {"type": "done"},
            ],
        )
        chunks = await _collect(_make_tts(client))
        assert [c.pcm for c in chunks if not c.is_final] == [chunk_b]


# ---------------------------------------------------------------------------
# Request body / headers
# ---------------------------------------------------------------------------


class TestRequestEncoding:
    async def test_posts_to_tts_sse_with_documented_body_and_headers(self) -> None:
        captured: list[httpx.Request] = []
        client = _make_client(events=[{"type": "done"}], captured=captured)
        tts = _make_tts(client)
        request = TTSRequest(voice_id="voice-abc", sample_rate=16000)
        async for _ in tts.synthesize(request, "Hello world."):
            pass

        assert len(captured) == 1
        http_req = captured[0]
        assert http_req.method == "POST"
        assert str(http_req.url) == f"{DEFAULT_BASE_URL}/tts/sse"
        assert http_req.headers["X-API-Key"] == "test-key"
        assert http_req.headers["Cartesia-Version"] == DEFAULT_CARTESIA_VERSION

        body = json.loads(http_req.content.decode())
        assert body == {
            "model_id": DEFAULT_MODEL_ID,
            "transcript": "Hello world.",
            "voice": {"mode": "id", "id": "voice-abc"},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
            },
            "language": "en",
        }

    async def test_custom_model_id_is_forwarded(self) -> None:
        captured: list[httpx.Request] = []
        client = _make_client(events=[{"type": "done"}], captured=captured)
        tts = CartesiaTTS(api_key="k", client=client, model_id="sonic-turbo")
        async for _ in tts.synthesize(TTSRequest(voice_id="v"), "x"):
            pass
        body = json.loads(captured[0].content.decode())
        assert body["model_id"] == "sonic-turbo"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorWrapping:
    async def test_non_200_response_raises_tts_error(self) -> None:
        client = _make_client(status_code=401, raw_body=b'{"error":"unauthorized"}')
        with pytest.raises(TTSError, match="Cartesia TTS call failed"):
            await _collect(_make_tts(client))

    async def test_malformed_json_in_sse_raises_tts_error(self) -> None:
        client = _make_client(events=["not json at all"])
        with pytest.raises(TTSError, match="non-JSON SSE"):
            await _collect(_make_tts(client))

    async def test_non_base64_chunk_data_raises_tts_error(self) -> None:
        client = _make_client(
            events=[{"type": "chunk", "data": "!!! not base64 !!!"}],
        )
        with pytest.raises(TTSError, match="not valid base64"):
            await _collect(_make_tts(client))

    async def test_non_string_chunk_data_raises_tts_error(self) -> None:
        client = _make_client(events=[{"type": "chunk", "data": 42}])
        with pytest.raises(TTSError, match="not a string"):
            await _collect(_make_tts(client))

    async def test_non_object_sse_payload_raises_tts_error(self) -> None:
        client = _make_client(events=["[1, 2, 3]"])
        with pytest.raises(TTSError, match="not an object"):
            await _collect(_make_tts(client))

    async def test_network_error_wraps_in_tts_error(self) -> None:
        # MockTransport handler raises mid-request — surfaces as TTSError.
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(TTSError, match="Cartesia TTS call failed"):
            await _collect(_make_tts(client))


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_default_constants_match_brief_defaults() -> None:
    # Pinning the constants here so the brief stays the source of
    # truth — any change to provider / version / model must move
    # together with this test, not silently in a config tweak.
    assert DEFAULT_BASE_URL == "https://api.cartesia.ai"
    assert DEFAULT_MODEL_ID == "sonic-2"
    assert DEFAULT_CARTESIA_VERSION == "2024-06-10"
