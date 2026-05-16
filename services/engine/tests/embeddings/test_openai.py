"""Tests for `OpenAIEmbeddingProvider` with a stubbed AsyncOpenAI client.

We don't hit OpenAI in tests — that would be slow, costly, and flaky.
Instead we construct a stub client whose `.embeddings.create` returns
a canned response shaped like the real SDK's, then assert the provider
unpacks it correctly and that error paths wrap into `EmbeddingError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from verbio_engine.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)


@dataclass
class _StubEmbeddingDatum:
    embedding: list[float]


@dataclass
class _StubEmbeddingResponse:
    data: list[_StubEmbeddingDatum]


@dataclass
class _StubEmbeddingsAPI:
    """Records calls, returns canned responses, raises on demand."""

    canned: list[list[float]] = field(default_factory=list)
    raise_on_create: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> _StubEmbeddingResponse:
        self.calls.append(kwargs)
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return _StubEmbeddingResponse(data=[_StubEmbeddingDatum(embedding=v) for v in self.canned])


@dataclass
class _StubClient:
    embeddings: _StubEmbeddingsAPI


def _make_provider(
    *,
    canned: list[list[float]] | None = None,
    raise_on_create: Exception | None = None,
    model: str = "text-embedding-3-small",
    dim: int | None = None,
) -> tuple[OpenAIEmbeddingProvider, _StubEmbeddingsAPI]:
    api = _StubEmbeddingsAPI(canned=canned or [], raise_on_create=raise_on_create)
    client = _StubClient(embeddings=api)
    provider = OpenAIEmbeddingProvider(
        client=cast("Any", client),
        model=model,
        dim=dim,
    )
    return provider, api


class TestConstruction:
    def test_default_dim_for_known_model(self) -> None:
        provider, _ = _make_provider(model="text-embedding-3-small")
        assert provider.dim == 1536
        assert provider.model_name == "text-embedding-3-small"

    def test_default_dim_for_3_large(self) -> None:
        provider, _ = _make_provider(model="text-embedding-3-large")
        assert provider.dim == 3072

    def test_explicit_dim_wins(self) -> None:
        provider, _ = _make_provider(model="text-embedding-3-small", dim=512)
        assert provider.dim == 512

    def test_unknown_model_requires_explicit_dim(self) -> None:
        api = _StubEmbeddingsAPI()
        client = _StubClient(embeddings=api)
        with pytest.raises(ValueError, match="unknown model"):
            OpenAIEmbeddingProvider(
                client=cast("Any", client),
                model="custom-model-name",
            )

    def test_negative_dim_rejected(self) -> None:
        api = _StubEmbeddingsAPI()
        client = _StubClient(embeddings=api)
        with pytest.raises(ValueError, match="dim must be positive"):
            OpenAIEmbeddingProvider(
                client=cast("Any", client),
                model="text-embedding-3-small",
                dim=0,
            )

    def test_satisfies_protocol(self) -> None:
        provider, _ = _make_provider()
        assert isinstance(provider, EmbeddingProvider)


class TestEmbedOne:
    async def test_returns_first_vector(self) -> None:
        provider, api = _make_provider(canned=[[0.1] * 1536], dim=1536)
        result = await provider.embed_one("hello")
        assert result == [0.1] * 1536
        assert api.calls[0]["input"] == ["hello"]
        assert api.calls[0]["model"] == "text-embedding-3-small"

    async def test_does_not_send_dimensions_for_native_size(self) -> None:
        provider, api = _make_provider(canned=[[0.0] * 1536])
        await provider.embed_one("x")
        assert "dimensions" not in api.calls[0]

    async def test_sends_dimensions_when_truncating(self) -> None:
        provider, api = _make_provider(
            canned=[[0.0] * 256],
            model="text-embedding-3-small",
            dim=256,
        )
        await provider.embed_one("x")
        assert api.calls[0]["dimensions"] == 256

    async def test_omits_dimensions_for_ada_002(self) -> None:
        # ada-002 doesn't accept `dimensions=`; we never send it.
        provider, api = _make_provider(
            canned=[[0.0] * 1536],
            model="text-embedding-ada-002",
        )
        await provider.embed_one("x")
        assert "dimensions" not in api.calls[0]


class TestEmbedMany:
    async def test_preserves_order_and_count(self) -> None:
        provider, api = _make_provider(canned=[[0.1] * 1536, [0.2] * 1536, [0.3] * 1536])
        result = await provider.embed_many(["a", "b", "c"])
        assert result == [[0.1] * 1536, [0.2] * 1536, [0.3] * 1536]
        assert api.calls[0]["input"] == ["a", "b", "c"]

    async def test_empty_input_short_circuits(self) -> None:
        provider, api = _make_provider(canned=[])
        result = await provider.embed_many([])
        assert result == []
        assert api.calls == []  # never reached the network


class TestErrorWrapping:
    async def test_sdk_error_wraps_in_embedding_error(self) -> None:
        provider, _ = _make_provider(
            canned=[],
            raise_on_create=RuntimeError("connection reset"),
        )
        with pytest.raises(EmbeddingError, match="OpenAI embeddings call failed"):
            await provider.embed_one("x")

    async def test_wrapped_error_preserves_cause(self) -> None:
        original = RuntimeError("connection reset")
        provider, _ = _make_provider(canned=[], raise_on_create=original)
        try:
            await provider.embed_one("x")
        except EmbeddingError as e:
            assert e.__cause__ is original
        else:
            pytest.fail("Expected EmbeddingError")

    async def test_count_mismatch_raises(self) -> None:
        # Two inputs, only one vector back — provider must refuse.
        provider, _ = _make_provider(canned=[[0.0] * 1536])
        with pytest.raises(EmbeddingError, match="returned 1 vectors for 2"):
            await provider.embed_many(["a", "b"])

    async def test_dim_mismatch_raises(self) -> None:
        # Asked for 1536-dim model, server returned 512.
        provider, _ = _make_provider(canned=[[0.0] * 512])
        with pytest.raises(EmbeddingError, match="dim mismatch"):
            await provider.embed_one("x")

    async def test_malformed_response_raises(self) -> None:
        # Stub returns a non-conformant object (no .data attribute).

        class _BrokenAPI:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def create(self, **kwargs: Any) -> object:
                self.calls.append(kwargs)
                return object()  # no .data, no .embedding

        broken = _BrokenAPI()
        client = _StubClient(embeddings=cast("Any", broken))
        provider = OpenAIEmbeddingProvider(
            client=cast("Any", client),
            model="text-embedding-3-small",
        )
        with pytest.raises(EmbeddingError, match="response missing"):
            await provider.embed_one("x")
