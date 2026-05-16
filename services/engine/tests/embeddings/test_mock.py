"""Tests for `MockEmbeddingProvider`."""

from __future__ import annotations

import math

import pytest

from verbio_engine.embeddings import EmbeddingProvider, MockEmbeddingProvider, cosine_similarity


class TestMockProviderShape:
    def test_satisfies_protocol(self) -> None:
        provider = MockEmbeddingProvider()
        assert isinstance(provider, EmbeddingProvider)

    def test_default_dim_matches_openai_small(self) -> None:
        assert MockEmbeddingProvider().dim == 1536

    def test_default_model_name(self) -> None:
        assert MockEmbeddingProvider().model_name == "mock-embed-v1"

    def test_custom_dim_and_name(self) -> None:
        p = MockEmbeddingProvider(dim=32, model_name="test-v2")
        assert p.dim == 32
        assert p.model_name == "test-v2"

    def test_zero_dim_raises(self) -> None:
        with pytest.raises(ValueError, match="dim must be positive"):
            MockEmbeddingProvider(dim=0)

    def test_negative_dim_raises(self) -> None:
        with pytest.raises(ValueError, match="dim must be positive"):
            MockEmbeddingProvider(dim=-1)


class TestMockProviderDeterminism:
    async def test_same_input_same_vector(self) -> None:
        p = MockEmbeddingProvider(dim=32)
        v1 = await p.embed_one("hello world")
        v2 = await p.embed_one("hello world")
        assert v1 == v2

    async def test_same_input_across_instances(self) -> None:
        p1 = MockEmbeddingProvider(dim=32)
        p2 = MockEmbeddingProvider(dim=32)
        v1 = await p1.embed_one("hello world")
        v2 = await p2.embed_one("hello world")
        assert v1 == v2

    async def test_returned_dim_matches(self) -> None:
        p = MockEmbeddingProvider(dim=64)
        v = await p.embed_one("anything")
        assert len(v) == 64

    async def test_case_insensitive_tokenisation(self) -> None:
        p = MockEmbeddingProvider(dim=32)
        assert await p.embed_one("Hello World") == await p.embed_one("hello world")

    async def test_punctuation_normalised_away(self) -> None:
        p = MockEmbeddingProvider(dim=32)
        # Same tokens once punctuation is stripped.
        assert await p.embed_one("hello, world!") == await p.embed_one("hello world")


class TestMockProviderEmptyInput:
    async def test_empty_string_returns_zero_vector(self) -> None:
        p = MockEmbeddingProvider(dim=16)
        v = await p.embed_one("")
        assert v == [0.0] * 16

    async def test_whitespace_only_returns_zero_vector(self) -> None:
        p = MockEmbeddingProvider(dim=16)
        v = await p.embed_one("   \t  \n")
        assert v == [0.0] * 16

    async def test_punctuation_only_returns_zero_vector(self) -> None:
        p = MockEmbeddingProvider(dim=16)
        v = await p.embed_one("!?.,;:")
        assert v == [0.0] * 16


class TestMockProviderSemantics:
    """The mock is only coarsely semantic, but two design constraints
    must hold for downstream rule tests to be expressive:

      1. Shared tokens should push two inputs closer than disjoint tokens.
      2. A vector with content should be normalised (unit length).
    """

    async def test_nonzero_outputs_are_unit_length(self) -> None:
        p = MockEmbeddingProvider(dim=64)
        v = await p.embed_one("focus group moderator")
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-9)

    async def test_shared_tokens_beat_disjoint_tokens(self) -> None:
        p = MockEmbeddingProvider(dim=128)
        prompt = await p.embed_one("what features do you want in a music app")
        related = await p.embed_one("a music app with good features")
        unrelated = await p.embed_one("yesterday I went hiking near a lake")

        sim_related = cosine_similarity(prompt, related)
        sim_unrelated = cosine_similarity(prompt, unrelated)

        assert sim_related > sim_unrelated

    async def test_identical_text_scores_1(self) -> None:
        p = MockEmbeddingProvider(dim=64)
        v1 = await p.embed_one("the same sentence")
        v2 = await p.embed_one("the same sentence")
        assert cosine_similarity(v1, v2) == pytest.approx(1.0)


class TestMockProviderBatch:
    async def test_batch_matches_single(self) -> None:
        p = MockEmbeddingProvider(dim=32)
        single = [await p.embed_one(t) for t in ["one", "two", "three"]]
        batch = await p.embed_many(["one", "two", "three"])
        assert batch == single

    async def test_batch_preserves_order(self) -> None:
        p = MockEmbeddingProvider(dim=16)
        a = await p.embed_one("alpha")
        b = await p.embed_one("bravo")
        batch = await p.embed_many(["bravo", "alpha"])
        assert batch[0] == b
        assert batch[1] == a

    async def test_empty_batch_returns_empty(self) -> None:
        p = MockEmbeddingProvider(dim=32)
        assert await p.embed_many([]) == []
